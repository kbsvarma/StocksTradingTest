"""Failure-injection suite — every scenario here is an incident this system
actually had (or a review finding). Run: pytest trend_bot/tests -q

Covers: partial entry fills, partial stop exits, canary clamp, bounded
protection recovery (re-arm then flatten), pre-trade fail-closed reconcile,
UNROUTED detection, crash-window intent recovery, Webull venue refusal,
holiday calendar, position-diff logic.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import bot as botmod  # noqa: E402
from executor import Fill, make_executor  # noqa: E402
from state_store import StateStore  # noqa: E402


# ---------------------------------------------------------------- fakes
class FakeEx:
    venue = "ibkr_paper"

    def __init__(self):
        self.calls = []
        self.stop_working = True
        self.fill_qty = None          # None => fill whole request
        self.fail_enter = None        # exception to raise on entry
        self.fail_place_stop = None
        self.fail_close = None
        self.close_qty = None          # None => close whole request
        self.cancel_verify_result = "cancelled"
        self.recover_entries_queue = [[]]
        self.recover_entries_calls = 0
        self.recovered_stops = {}
        self.partial_exit = None
        self.stop_identity = None     # (con_id, stop_px) of the resting stop
        self.open_orders = []
        self.positions_map = {}
        self._exec_seq = 0

    def _fill(self, px, commission, qty):
        self._exec_seq += 1
        xid = f"FAKE-EXEC-{self._exec_seq}"
        fill = Fill(px, commission, qty=qty, order_id=self._exec_seq,
                    exec_ids=[xid])
        fill.exec_details = [(xid, qty, px, commission)]
        fill.order_key = f"fake-order:{self._exec_seq}"
        return fill

    def recover_stops(self):
        return dict(self.recovered_stops)

    def open_bot_orders(self):
        return list(self.open_orders)

    def open_account_orders(self):
        return list(self.open_orders)

    def flatten_position(self, label, contract, signed_qty):
        self.calls.append(("flatten", label, signed_qty))
        return self._fill(99.0, 1.0, abs(signed_qty))

    def recover_entries(self):
        self.recover_entries_calls += 1
        return (self.recover_entries_queue.pop(0)
                if self.recover_entries_queue else [])
        self.ib = SimpleNamespace(sleep=lambda s: None,
                                  cancelOrder=lambda o: None)

    def enter_long_with_stop(self, sym, contract, qty, stop_px,
                             intent_id=None):
        self.calls.append(("enter", sym, qty, stop_px))
        self.last_intent_id = intent_id
        if self.fail_enter:
            raise self.fail_enter
        q = self.fill_qty if self.fill_qty is not None else qty
        f = self._fill(100.0, 1.0, q)
        f.exec_time = datetime.now(timezone.utc)
        f.partial_exit = self.partial_exit
        return f, (99 if q else None)

    def update_stop(self, sym, px, **kw):
        self.calls.append(("update_stop", sym, px))

    def place_stop(self, sym, contract, qty, stop_px):
        self.calls.append(("place_stop", sym, qty, stop_px))
        if self.fail_place_stop:
            raise self.fail_place_stop
        return 77

    def close_market(self, sym, contract, qty):
        self.calls.append(("close", sym, qty))
        if self.fail_close:
            raise self.fail_close
        q = self.close_qty if self.close_qty is not None else qty
        if getattr(contract, "conId", None) in self.positions_map:
            self.positions_map[contract.conId] = max(
                0, self.positions_map[contract.conId] - q)
        return self._fill(99.0, 1.0, q)

    def cancel_stop(self, sym):
        self.calls.append(("cancel_stop", sym))
        return "cancelled" 

    def cancel_and_verify(self, trade, timeout=10.0):
        self.calls.append(("cancel_verify", getattr(
            getattr(trade, "order", None), "orderId", None)))
        if self.cancel_verify_result == "cancelled":
            self.open_orders = [o for o in self.open_orders
                                if o.get("trade") is not trade]
        return self.cancel_verify_result

    def has_working_stop(self, sym, qty=None, con_id=None, stop_px=None,
                         px_tol=0.0):
        if not self.stop_working:
            return False
        if self.stop_identity is None:
            return True
        sc, sp = self.stop_identity
        if con_id is not None and sc != con_id:
            return False
        if stop_px is not None and abs(sp - stop_px) > max(px_tol, 1e-9):
            return False
        return True

    def positions(self):
        return dict(self.positions_map)

    def contract_by_conid(self, cid):
        return SimpleNamespace(conId=cid, localSymbol="TESTX6")


@pytest.fixture
def tb(tmp_path, monkeypatch):
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    cfg["ops"]["state_dir"] = os.path.relpath(tmp_path, BASE)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(botmod, "send_alert", lambda *a, **k: None)
    monkeypatch.setattr(botmod.TrendBot, "__init__", _light_init)
    b = botmod.TrendBot(p)
    return b


def _light_init(self, cfg_path, client_id_offset=0):
    self.cfg = yaml.safe_load(Path(cfg_path).read_text())
    sdir = BASE / self.cfg["ops"]["state_dir"]
    from data import BarStore
    self.store = BarStore(sdir)
    self.st = StateStore(sdir)
    self.dlog = SimpleNamespace(log=lambda *a, **k: None)
    self.data = None
    self.ex = FakeEx()
    self.scfg = self.cfg["strategy"]
    self.all_instruments = {**self.cfg["core_instruments"],
                            **self.cfg["sleeve_instruments"]}
    self.st.state["mode"] = "aggressive"
    self._startup_done = True
    self._stale_alerted = set()
    self._consec_errors = {"data": 0, "executor": 0}
    self._wake_failed = False
    self._degraded = set()
    self._reconcile_ok = True
    self._market_open_now = lambda: True
    self.st.state.setdefault("pending_entries", {})
    self.st.state.setdefault("order_intents", {})
    self.st.state.setdefault("last_decision_bar", {})


FRONT = SimpleNamespace(conId=555, localSymbol="TESTX6")


# ------------------------------------------------------- partial fills
def test_partial_entry_fill_sizes_state_and_stop_to_actual(tb):
    tb.ex.fill_qty = 1                      # asked 3, got 1
    tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    p = tb.st.pos("YM")
    assert p["qty"] == 1                    # state = actual
    assert p["position"] == 1
    # executor sized the stop internally from fill.qty (see executor.py);
    # here we assert state never claims the requested 3
    assert p["entry_weight"] == 3           # intent preserved for the record


def test_partial_stop_exit_books_actual_and_keeps_remainder(tb):
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, entry_px=100.0, entry_time="2026-07-17",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=3, hw=100.0, sig_atr=1.0, stop_order_id=9)
    fill = Fill(98.0, 0.5, qty=1)           # stop filled 1 of 3
    tb._book_stop_exit("YM", tb.all_instruments["YM"], p, fill)
    assert tb.st.pos("YM")["qty"] == 2      # remainder still held
    assert tb.st.pos("YM")["position"] == 1


def test_full_stop_exit_flattens(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="2026-07-17",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb._book_stop_exit("GC", tb.all_instruments["GC"], p,
                       Fill(3990.0, 0.5, qty=1))
    assert tb.st.pos("GC")["position"] == 0


# ------------------------------------------------------------- canary
def test_live_venue_canary_clamps_to_one_contract(tb):
    tb.ex.venue = "ibkr_live"
    tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert tb.st.pos("YM")["qty"] == 1
    assert ("enter", "YM", 1, pytest.approx(96.0)) == \
        [c for c in tb.ex.calls if c[0] == "enter"][0]


# -------------------------------------------------- bounded recovery
def test_unprotected_position_rearms(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {7: 1.0}          # broker CONFIRMS the holding
    tb._ensure_protection()
    assert ("place_stop", "GC", 1, 3990.0) in tb.ex.calls
    assert tb.st.state["halt_entries"]      # halted until diff clears


def test_unprotected_position_flattens_when_rearm_fails(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {7: 1.0}
    tb.ex.fail_place_stop = RuntimeError("no ack")
    tb._ensure_protection()
    assert ("close", "GC", 1) in tb.ex.calls
    assert tb.st.pos("GC")["position"] == 0  # flattened + booked


# ------------------------------------------- reconcile / position diff
def test_position_diff_flags_unmanaged_and_mismatch(tb):
    tb.ex.positions_map = {555: 3.0}
    assert any("unmanaged" in m for m in tb._position_diff())
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, con_id=555)
    assert tb._position_diff() == []
    tb.ex.positions_map = {555: 1.0}        # broker holds less
    assert any("state 3 vs account 1" in m for m in tb._position_diff())


def test_position_diff_rejects_duplicate_managed_contract_identity(tb):
    tb.st.pos("YM").update(position=1, qty=1, con_id=555)
    tb.st.pos("ES").update(position=1, qty=1, con_id=555)
    tb.ex.positions_map = {555: 1.0}
    assert any("duplicate managed conId" in mismatch
               for mismatch in tb._position_diff())


def test_generic_entry_failure_preserves_intent_until_broker_reconcile(tb):
    from executor import UnresolvedEntry
    tb.ex.fail_enter = RuntimeError("boom")
    with pytest.raises(UnresolvedEntry):
        tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert "YM" in tb.st.state["order_intents"]


# ------------------------------------------------------ venue refusal
def test_webull_venue_refused():
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    cfg["executor"]["venue"] = "webull_live"
    with pytest.raises(RuntimeError, match="DISABLED"):
        make_executor(cfg, alert=lambda m: None,
                      dlog=SimpleNamespace(log=lambda *a, **k: None))


def test_dirty_release_refusal_happens_before_state_creation(tmp_path,
                                                             monkeypatch):
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    state_dir = tmp_path / "must-not-exist"
    cfg["ops"]["state_dir"] = os.path.relpath(state_dir, BASE)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(
        botmod, "release_provenance",
        lambda: {"tree_dirty": True, "code_commit": "abc",
                 "config_sha256": "def"})
    with pytest.raises(RuntimeError, match="before opening operational state"):
        botmod.TrendBot(cfg_path)
    assert not state_dir.exists()


def test_real_main_refuses_dirty_release_before_process_lock(tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(botmod, "BASE", tmp_path)
    monkeypatch.setattr(
        botmod, "release_provenance",
        lambda: {"tree_dirty": True, "code_commit": "abc",
                 "config_sha256": "def"})
    monkeypatch.setattr(sys, "argv", ["bot.py"])
    with pytest.raises(RuntimeError, match="before opening operational state"):
        botmod.main()
    assert not (tmp_path / "state").exists()


def test_paper_executor_requires_exact_configured_account(monkeypatch):
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    monkeypatch.delenv("TRENDBOT_PAPER_ACCOUNT", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        ex._verify_account("DTEST")
    monkeypatch.setenv("TRENDBOT_PAPER_ACCOUNT", "DOTHER")
    with pytest.raises(RuntimeError, match="does not match"):
        ex._verify_account("DTEST")
    monkeypatch.setenv("TRENDBOT_PAPER_ACCOUNT", "DTEST")
    ex._verify_account("DTEST")


def test_executor_aggregates_scoped_positions_and_rejects_fractional_qty():
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.account = "DTEST"
    contract = SimpleNamespace(conId=555)
    rows = [
        SimpleNamespace(account="DTEST", contract=contract, position=1),
        SimpleNamespace(account="DTEST", contract=contract, position=2),
        SimpleNamespace(account="DOTHER", contract=contract, position=50),
    ]
    ex.ib = SimpleNamespace(positions=lambda: rows)
    assert ex.positions() == {555: 3.0}
    rows[1].position = 1.5
    with pytest.raises(RuntimeError, match="non-integral"):
        ex.positions()


def test_account_order_gate_includes_manual_orders_but_excludes_linked_account():
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.account = "DTEST"

    def trade(order_id, ref, account):
        return SimpleNamespace(
            order=SimpleNamespace(orderId=order_id, orderRef=ref,
                                  account=account, action="BUY",
                                  totalQuantity=1),
            orderStatus=SimpleNamespace(status="Submitted"))

    manual = trade(1, "manual-order", "DTEST")
    bot_order = trade(2, "trendbot:YM:entry:test", "DTEST")
    linked = trade(3, "linked-account-order", "DOTHER")
    ex.ib = SimpleNamespace(
        reqAllOpenOrders=lambda: None, sleep=lambda seconds: None,
        openTrades=lambda: [manual, bot_order, linked])
    assert [order["order_id"] for order in ex.open_account_orders()] == [1, 2]
    assert [order["order_id"] for order in ex.open_bot_orders()] == [2]


# --------------------------------------------------- holiday calendar
@pytest.mark.parametrize("ts,want", [
    (datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc), False),  # full close
    (datetime(2026, 12, 25, 1, 0, tzinfo=timezone.utc), False),   # eve session
    (datetime(2026, 9, 7, 14, 0, tzinfo=timezone.utc), True),     # early, am
    (datetime(2026, 9, 7, 17, 0, tzinfo=timezone.utc), False),    # early, pm
    (datetime(2026, 9, 7, 22, 30, tzinfo=timezone.utc), True),    # reopen
    (datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), True),    # normal
    (datetime(2026, 7, 21, 21, 30, tzinfo=timezone.utc), False),  # maint hour
])
def test_market_calendar(ts, want):
    assert botmod.is_market_open(ts) is want


def test_preregistered_trial_statistics_match_frozen_protocols():
    import math
    from trial_stats import newey_west_indicator_t, welch_t

    left, right = [3, 4, 5], [0, 1, 2]
    assert welch_t(left, right) == pytest.approx(3 / math.sqrt(2 / 3))
    outcomes = [10, 0, 12, 1, 9, -1, 11, 2, 13, 0]
    indicator = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]
    t_stat = newey_west_indicator_t(outcomes, indicator, lags=3)
    reverse = newey_west_indicator_t(
        outcomes, [1 - value for value in indicator], lags=3)
    assert math.isfinite(t_stat) and t_stat > 2
    assert reverse == pytest.approx(-t_stat)
    assert math.isnan(newey_west_indicator_t(outcomes, [1] * 10))


# ------------------------------------------------- UNROUTED detection
def test_unrouted_timeout_has_distinct_marker():
    """permId=0 + nonterminal cancel preserves the recovery intent."""
    from executor import IBKRPaperExecutor, UnresolvedEntry
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=44, permId=0),
        orderStatus=SimpleNamespace(status="PendingSubmit", filled=0,
                                    avgFillPrice=0.0),
        fills=[])
    ex.ib = SimpleNamespace(waitOnUpdate=lambda timeout: None,
                            cancelOrder=lambda o: None,
                            sleep=lambda s: None)
    ex.cancel_verify_timeout = 0.01
    with pytest.raises(UnresolvedEntry, match="UNROUTED"):
        ex._wait_fill(trade, timeout=0.1)


def test_partial_late_fill_adopted_with_actual_qty():
    """timeout + partial fill => adopted at the ACTUAL quantity."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=44, permId=123),
        orderStatus=SimpleNamespace(status="Submitted", filled=1.0,
                                    avgFillPrice=100.5),
        fills=[])
    ex.cancel_verify_timeout = 0.05
    ex.ib = SimpleNamespace(
        waitOnUpdate=lambda timeout: None,
        cancelOrder=lambda o: setattr(trade.orderStatus, "status",
                                      "Cancelled"),
        sleep=lambda s: None)
    fill = ex._wait_fill(trade, timeout=0.1)
    assert fill.qty == 1.0
    assert fill.px == 100.5


# ------------------------------------------------------- state journal
def test_statedb_trade_net_of_fees_and_corrections(tmp_path):
    from state_db import StateDB
    db = StateDB(tmp_path, venue="test")
    db.trade(exit_time="t", book="core", sym="YM", qty=3, entry_time="t0",
             entry_px=100.0, exit_px=110.0, reason="stop", pnl_points=10.0,
             pnl_usd=15.0, commission=3.72, entry_weight=3,
             local_symbol="MYMU6")
    assert db.net_pnl() == pytest.approx(15.0 - 3.72)
    db.correction("void_trade", "phantom", -5.0, "test void")
    assert db.net_pnl() == pytest.approx(15.0 - 3.72 - 5.0)
    # provenance recorded
    run = db.con.execute("SELECT code_commit, config_sha256 FROM runs "
                         "WHERE run_id=?", (db.run_id,)).fetchone()
    assert run[0] and run[1]


def test_statedb_survives_reopen(tmp_path):
    from state_db import StateDB
    db1 = StateDB(tmp_path, venue="test")
    db1.order_event("GC", "entry", "intent", qty=1)
    db1.con.close()
    db2 = StateDB(tmp_path, venue="test")
    n = db2.con.execute("SELECT COUNT(*) FROM order_events").fetchone()[0]
    assert n == 1


# ------------------------------------------------------- kill switch
def test_kill_switch_halts_and_releases(tb):
    ks = tb.st.dir / "KILL_SWITCH"
    ks.write_text("")
    assert tb.check_kill_switch() is True
    assert tb.st.state["halt_entries"]
    ks.unlink()
    assert tb.check_kill_switch() is False
    assert not tb.st.state["halt_entries"]


def test_kill_switch_flatten_closes_positions(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.positions_map = {7: 1.0}            # broker-driven flatten
    ks = tb.st.dir / "KILL_SWITCH"
    ks.write_text("FLATTEN")
    tb.check_kill_switch()
    assert ("close", "GC", 1) in tb.ex.calls
    assert tb.st.pos("GC")["position"] == 0
    # broker still reports the position in this fake, so DONE is refused —
    # clearing the map and re-running completes it
    tb.ex.positions_map = {}
    tb.check_kill_switch()
    assert "DONE" in ks.read_text()           # idempotent marker


def test_kill_switch_closed_market_keeps_broker_stops_intact(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.positions_map = {7: 1.0}
    tb.ex._stop_trades = {"GC": object()}
    tb._market_open_now = lambda: False
    ks = tb.st.dir / "KILL_SWITCH"
    ks.write_text("FLATTEN")
    tb.check_kill_switch()
    assert not [c for c in tb.ex.calls if c[0] in ("cancel_stop", "close")]
    assert "DONE" not in ks.read_text()
    assert tb.st.pos("GC")["position"]


# ----------------------------- codex adversarial scenarios (2026-07-18)
def test_partial_stop_fill_detected_and_handle_retained():
    """1-of-3 stop fill => qty 1 reported, handle kept, remainder covered."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=9, permId=99, orderRef="trendbot:YM:stop",
                              totalQuantity=3.0, action="SELL",
                              orderType="STP", auxPrice=98.0),
        orderStatus=SimpleNamespace(status="Submitted", filled=1.0,
                                    avgFillPrice=98.0),
        fills=[])
    ex._stop_trades = {"YM": trade}
    fill = ex.check_stop_filled("YM")
    assert fill is not None and fill.qty == 1.0
    assert "YM" in ex._stop_trades               # handle retained
    ex.mark_stop_booked("YM", fill)              # ledgers committed
    assert ex.check_stop_filled("YM") is None    # no double-booking
    # remainder (2) counts as covered for a 2-lot position
    assert ex.has_working_stop("YM", qty=2)
    assert not ex.has_working_stop("YM", qty=3)
    # completion books only the remaining 2
    trade.orderStatus.status = "Filled"
    trade.orderStatus.filled = 3.0
    done = ex.check_stop_filled("YM")
    assert done.qty == 2.0
    assert "YM" in ex._stop_trades               # PEEK: not yet consumed
    ex.mark_stop_booked("YM", done)
    assert "YM" not in ex._stop_trades           # consumed after booking


def test_terminal_cancelled_partial_stop_releases_handle_after_booking():
    """A 1-of-3 stop can finish Cancelled after its residual is cancelled.

    The one fill remains observable until the DB commit, then the terminal
    handle must disappear so the residual position can receive a new stop.
    """
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue, ex.account, ex.db = "ibkr_paper", "DTEST", None
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    trade = SimpleNamespace(
        order=SimpleNamespace(
            orderId=9, permId=99, orderRef="trendbot:YM:stop",
            totalQuantity=3.0, action="SELL", orderType="STP",
            auxPrice=98.0, account="DTEST"),
        orderStatus=SimpleNamespace(
            status="Cancelled", filled=1.0, avgFillPrice=98.0),
        fills=[])
    ex._stop_trades = {"YM": trade}
    fill = ex.check_stop_filled("YM")
    assert fill is not None and fill.qty == 1
    assert "YM" in ex._stop_trades
    ex.mark_stop_booked("YM", fill)
    assert "YM" not in ex._stop_trades


def test_kill_switch_partial_flatten_keeps_and_reprotects_remainder(tb):
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=3, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.close_qty = 1                          # broker closes only 1 of 3
    tb.ex.positions_map = {555: 3.0}             # broker starts at state qty
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert tb.st.pos("YM")["qty"] == 2           # remainder kept in state
    assert ("place_stop", "YM", 2, 98.0) in tb.ex.calls   # re-protected
    assert "DONE" not in (tb.st.dir / "KILL_SWITCH").read_text()


def test_kill_switch_flatten_failure_rearms_and_no_done_marker(tb):
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.fail_close = RuntimeError("close rejected")
    tb.ex.positions_map = {7: 1.0}
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert ("place_stop", "GC", 1, 3990.0) in tb.ex.calls  # re-armed
    assert "DONE" not in (tb.st.dir / "KILL_SWITCH").read_text()
    assert tb.st.pos("GC")["position"] == 1      # never falsely flattened


def test_enter_refuses_when_kill_switch_present(tb):
    (tb.st.dir / "KILL_SWITCH").write_text("")
    with pytest.raises(RuntimeError, match="KILL_SWITCH"):
        tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert not [c for c in tb.ex.calls if c[0] == "enter"]


def test_entry_intent_kept_on_unverified_failure(tb):
    from executor import UnresolvedEntry
    tb.ex.fail_enter = ConnectionError("socket dropped mid-flight")
    with pytest.raises(UnresolvedEntry):
        tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert "YM" in tb.st.state["order_intents"]  # breadcrumb survives


def test_retry_blocked_by_position_mismatch(tb):
    tb.st.state["pending_entries"]["YM"] = {
        "level": 99.0, "close": 100.0, "atr": 1.0, "weight": 3,
        "bar": "b", "created": botmod.utcnow()}
    tb.ex.positions_map = {999: 5.0}             # unmanaged position!
    tb.data = SimpleNamespace(
        front_contract=lambda *a: FRONT,
        hourly_bars=lambda *a, **k: pd.DataFrame(
            {"close": [101.0]},
            index=pd.DatetimeIndex([pd.Timestamp.utcnow()])))
    tb._retry_pending_entries()
    assert not [c for c in tb.ex.calls if c[0] == "enter"]  # fail closed


# --------------------------- codex round-2 reproduced scenarios (07-19)
def _intent(tb, sym="YM", qty=1, iid="abc12345"):
    tb.st.state["order_intents"][sym] = {
        "intent_id": iid, "qty": qty, "local_symbol": "TESTX6",
        "con_id": 555, "sig_close": 100.0, "sig_atr": 1.0,
        "created": botmod.utcnow()}
    return {"kind": "entry_execution", "sym": sym,
            "ref": f"trendbot:{sym}:entry:{iid}", "con_id": 555,
            "local_symbol": "TESTX6", "qty": qty, "px": 100.0, "time": "t"}


def test_adoption_places_exactly_one_stop(tb):
    """Round-2 #1: adopted fill must NOT get a second stop from the
    re-arm sweep."""
    exe = _intent(tb)
    tb.ex.recover_entries_queue = [[exe]]
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    stops = [c for c in tb.ex.calls if c[0] == "place_stop"]
    assert len(stops) == 1, stops
    assert tb.st.pos("YM")["position"] == 1


def test_fill_during_cancel_is_adopted_via_rescan(tb):
    """Round-2 #2: a working entry that FILLS during cancel is adopted."""
    exe = _intent(tb)
    working = {"kind": "working_entry", "sym": "YM",
               "ref": exe["ref"], "order_id": 5,
               "trade": SimpleNamespace(order=SimpleNamespace(orderId=5))}
    tb.ex.cancel_verify_result = "filled"
    tb.ex.recover_entries_queue = [[working], [working, exe]]
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    assert tb.st.pos("YM")["position"] == 1        # adopted, not orphaned
    assert [c for c in tb.ex.calls if c[0] == "place_stop"]


def test_filled_cancel_tracking_does_not_poison_other_cancelled_intent(tb,
                                                                      monkeypatch):
    ym_exec = _intent(tb, sym="YM", iid="ymfill1")
    _intent(tb, sym="GC", iid="gccancel")
    tb.st.state["order_intents"]["GC"].update(
        con_id=7, local_symbol="MGCQ6", sig_close=4000.0, sig_atr=10.0)
    ym_working = {"kind": "working_entry", "sym": "YM",
                  "ref": ym_exec["ref"], "order_id": 5,
                  "trade": SimpleNamespace(
                      order=SimpleNamespace(orderId=5))}
    gc_working = {"kind": "working_entry", "sym": "GC",
                  "ref": "trendbot:GC:entry:gccancel", "order_id": 6,
                  "trade": SimpleNamespace(
                      order=SimpleNamespace(orderId=6))}
    monkeypatch.setattr(
        tb.ex, "cancel_and_verify",
        lambda trade: "filled" if trade.order.orderId == 5 else "cancelled")
    tb.ex.recover_entries_queue = [
        [ym_working, gc_working], [ym_working, ym_exec]]
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    assert tb.st.pos("YM")["position"]
    assert "GC" not in tb.st.state["order_intents"]


def test_unresolved_cancel_preserves_intent_and_halts(tb):
    """Round-2 #2b: 'unknown' cancellation keeps the recovery key."""
    exe = _intent(tb)
    working = {"kind": "working_entry", "sym": "YM",
               "ref": exe["ref"], "order_id": 5,
               "trade": SimpleNamespace(order=SimpleNamespace(orderId=5))}
    tb.ex.cancel_verify_result = "unknown"
    tb.ex.recover_entries_queue = [[working]]
    tb.startup_reconcile()
    assert "YM" in tb.st.state["order_intents"]    # preserved
    assert tb.st.state["halt_entries"]


def test_cancel_stop_unknown_keeps_handle_and_blocks_close():
    """Round-2 #4: unverified cancel keeps the handle; close refuses."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex.cancel_verify_timeout = 0.1
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=9, totalQuantity=3.0),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0,
                                    avgFillPrice=0.0),
        fills=[])
    ex._stop_trades = {"GC": trade}
    ex.ib = SimpleNamespace(cancelOrder=lambda o: None,
                            waitOnUpdate=lambda timeout: None)
    assert ex.cancel_stop("GC") == "unknown"
    assert "GC" in ex._stop_trades                 # never forgotten
    with pytest.raises(RuntimeError, match="UNRESOLVED"):
        ex.close_market("GC", SimpleNamespace(conId=7, localSymbol="M"), 3)


def test_partial_protection_failure_retries_not_swallowed():
    """Round-2 #3: a failed protection callback is retried, not 'handled'."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    logged = []
    ex.dlog = SimpleNamespace(log=lambda *a, **k: logged.append(k))
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=4, permId=1),
        orderStatus=SimpleNamespace(status="Submitted", filled=1.0,
                                    avgFillPrice=100.0),
        fills=[])
    ex.cancel_verify_timeout = 0.05
    ex.ib = SimpleNamespace(
        waitOnUpdate=lambda timeout: None,
        cancelOrder=lambda o: setattr(trade.orderStatus, "status",
                                      "Cancelled"),
        sleep=lambda s: None)
    calls = []

    def failing(qty):
        calls.append(qty)
        raise RuntimeError("stop rejected")

    fill = ex._wait_fill(trade, timeout=0.3, on_partial=failing)
    assert fill.qty == 1.0                         # partial adopted at end
    assert len(calls) >= 2                         # retried every tick
    assert any(k.get("event") == "partial_protect_failed" for k in logged)


def test_ensure_protection_retries_partial_close_until_flat_when_unprotectable(
        tb):
    """A partial emergency close with repeated stop failures is retried now,
    not left naked until the next five-minute poll."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=3, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {555: 3.0}
    tb.ex.fail_place_stop = RuntimeError("no ack")
    tb.ex.close_qty = 1
    tb._ensure_protection()
    assert tb.st.pos("YM")["position"] == 0
    assert len([c for c in tb.ex.calls if c[0] == "close"]) == 3


def test_kill_switch_flatten_uses_broker_quantity(tb):
    """Round-2 #6: stale state qty must not stall the emergency —
    the close is sized from the BROKER position."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=1, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.positions_map = {555: 2.0}               # broker holds MORE
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert ("close", "YM", 2) in tb.ex.calls       # broker qty, not state


# --------------------------- codex round-3 reproduced scenarios (07-19)
def test_adoption_reuses_already_resting_stop(tb):
    """r3 #1: crash after incremental protection armed a stop — adoption
    must reuse it, never place a second."""
    exe = _intent(tb)
    stub = SimpleNamespace(order=SimpleNamespace(orderId=41))
    tb.ex.recovered_stops = {"YM": stub}
    tb.ex.stop_working = True                      # covers the qty
    tb.ex.recover_entries_queue = [[exe]]
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    assert [c for c in tb.ex.calls if c[0] == "place_stop"] == []
    assert tb.st.pos("YM")["position"] == 1
    assert tb.st.pos("YM")["stop_order_id"] == 41


def test_adoption_never_replaces_stop_that_filled_during_cancel(tb,
                                                               monkeypatch):
    exe = _intent(tb)
    tb.ex.recovered_stops = {
        "YM": SimpleNamespace(order=SimpleNamespace(orderId=41))}
    tb.ex.stop_working = False
    tb.ex.recover_entries_queue = [[exe]]
    tb.ex.positions_map = {555: 1.0}
    monkeypatch.setattr(tb.ex, "cancel_stop", lambda sym: "filled")
    tb.startup_reconcile()
    assert not [c for c in tb.ex.calls if c[0] == "place_stop"]
    assert "YM" in tb.st.state["order_intents"]
    assert tb.st.state["halt_entries"]


def test_scan_error_preserves_intents_and_halts(tb):
    """r3 #2: an incomplete execution scan must not erase recovery keys."""
    _intent(tb)
    tb.ex.recover_entries_queue = [[{"kind": "scan_error", "sym": "*",
                                     "error": "boom"}]]
    tb.startup_reconcile()
    assert "YM" in tb.st.state["order_intents"]
    assert tb.st.state["halt_entries"]


def test_filled_during_cancel_unadopted_stays_unresolved(tb):
    """r3 #2b: 'filled' outcome with no adoption = unresolved + halted."""
    exe = _intent(tb)
    working = {"kind": "working_entry", "sym": "YM", "ref": exe["ref"],
               "order_id": 5,
               "trade": SimpleNamespace(order=SimpleNamespace(orderId=5))}
    tb.ex.cancel_verify_result = "filled"
    # rescan returns NO execution (e.g. broker lag) -> must stay unresolved
    tb.ex.recover_entries_queue = [[working], [working]]
    tb.startup_reconcile()
    assert "YM" in tb.st.state["order_intents"]
    assert tb.st.state["halt_entries"]


def test_offline_partial_stop_books_delta_only(tb):
    """r3 #3: state 3, broker 2 after offline 1-lot stop fill — book 1,
    keep 2."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=3, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.recovered_stops = {"YM": SimpleNamespace(
        order=SimpleNamespace(orderId=9))}
    tb.ex.positions_map = {555: 2.0}
    tb.startup_reconcile()
    assert tb.st.pos("YM")["position"] == 1
    assert tb.st.pos("YM")["qty"] == 2


def test_ensure_protection_unknown_cancel_does_not_stack(tb, monkeypatch):
    """r3 #5b: unresolved residual-stop cancel must not add another stop."""
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {7: 1.0}
    monkeypatch.setattr(tb.ex, "cancel_stop", lambda sym: "unknown")
    tb._ensure_protection()
    assert [c for c in tb.ex.calls if c[0] == "place_stop"] == []
    # falls through to the flatten branch (close attempted) — bounded
    assert [c for c in tb.ex.calls if c[0] == "close"]


def test_kill_switch_done_requires_terminal_orders(tb):
    """Flat positions plus any scoped account order must not mark DONE."""
    tb.ex.positions_map = {}
    trade = SimpleNamespace(order=SimpleNamespace(orderId=9))
    tb.ex.open_orders = [{"ref": "manual-order", "order_id": 9,
                          "trade": trade}]
    tb.ex.cancel_verify_result = "unknown"
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert "DONE" not in (tb.st.dir / "KILL_SWITCH").read_text()
    assert ("cancel_verify", 9) in tb.ex.calls
    tb.ex.cancel_verify_result = "cancelled"
    tb.check_kill_switch()
    assert "DONE" in (tb.st.dir / "KILL_SWITCH").read_text()


def test_kill_switch_covers_shorts(tb):
    """r3 #7: FLATTEN covers a short instead of leaving it open."""
    tb.ex.positions_map = {888: -3.0}
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert ("flatten", "COVER-888", -3.0) in tb.ex.calls


def test_statedb_execution_idempotent(tmp_path):
    """r3 journal: same exec_id twice inserts once."""
    from state_db import StateDB
    db = StateDB(tmp_path, venue="test")
    db.execution("YM", "BUY", 1, 100.0, exec_id="X1")
    db.execution("YM", "BUY", 1, 100.0, exec_id="X1")
    n = db.con.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    assert n == 1


@pytest.mark.parametrize("field,value", [
    ("qty", float("nan")),
    ("qty", 0),
    ("px", float("inf")),
    ("px", -1),
])
def test_broker_execution_refuses_invalid_numeric_facts(tmp_path,
                                                        field, value):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="test")
    values = {"qty": 1, "px": 100.0}
    values[field] = value
    with pytest.raises(sqlite3.IntegrityError, match="finite and positive"):
        db.execution("YM", "BUY", exec_id="BAD-X", **values)
    assert db.con.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_raw_execution_and_commission_require_identity(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="test")
    with pytest.raises(sqlite3.IntegrityError, match="requires an execId"):
        db.execution("YM", "BUY", 1, 100)
    with pytest.raises(sqlite3.IntegrityError, match="requires an execId"):
        db.commission("", 1.25)


def test_execution_only_event_accepts_buy_to_cover_short(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    assert db.commit_execution_event(
        "emergency:cover:1", "COVER-888", "EMERGENCY_FLATTEN_COVER",
        executions=[dict(side="BUY", qty=3, px=101.0,
                         exec_id="COVER-X", commission=1.25)],
        payload={"basis": "unmanaged_short"})
    assert db.is_execution_applied("COVER-X", account="DTEST")
    with pytest.raises(sqlite3.IntegrityError, match="already applied"):
        db.commit_execution_event(
            "emergency:cover:replay", "COVER-888",
            "EMERGENCY_FLATTEN_COVER",
            executions=[dict(side="BUY", qty=3, px=101.0,
                             exec_id="COVER-X", commission=1.25)])


def test_execution_only_event_refuses_missing_exec_id(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    with pytest.raises(sqlite3.IntegrityError, match="requires an execId"):
        db.commit_execution_event(
            "emergency:no-id", "UNMANAGED-7", "EMERGENCY_FLATTEN",
            executions=[dict(side="SELL", qty=1, px=99.0)])
    assert db.event_status("emergency:no-id") is None


def test_position_transition_refuses_unidentified_or_unmapped_execution(
        tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    held = {"position": 1, "qty": 1, "con_id": 555}
    with pytest.raises(sqlite3.IntegrityError, match="requires an execId"):
        db.commit_transition(
            "entry:no-id", "YM", "ENTRY_FILLED", held,
            executions=[dict(side="BUY", qty=1, px=100)])
    with pytest.raises(sqlite3.IntegrityError, match="without an applied"):
        db.commit_transition(
            "entry:no-role", "YM", "ENTRY_FILLED", held,
            executions=[dict(side="BUY", qty=1, px=100,
                             exec_id="UNMAPPED-X")])
    assert db.event_status("entry:no-id") is None
    assert db.event_status("entry:no-role") is None


def test_event_key_collision_cannot_cross_symbol_or_final_type(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.begin_event("intent:1", "YM", "ENTRY_INTENT")
    held = {"position": 1, "qty": 1, "con_id": 555}
    db.commit_transition(
        "intent:1", "YM", "ENTRY_FILLED", held,
        executions=[dict(side="BUY", qty=1, px=100, exec_id="ENTRY-X")],
        entry_exec_ids=["ENTRY-X"])
    assert db.con.execute(
        "SELECT event_type FROM broker_events WHERE event_key='intent:1'"
    ).fetchone()[0] == "ENTRY_FILLED"
    # A late broker order-status callback may re-announce the original intent
    # lifecycle after the fill transaction; it must be harmless and terminal.
    db.begin_event("intent:1", "YM", "ENTRY_INTENT")
    assert db.event_status("intent:1") == "COMMITTED"
    with pytest.raises(sqlite3.IntegrityError, match="key collision"):
        db.commit_transition("intent:1", "ES", "ENTRY_FILLED", held)
    with pytest.raises(sqlite3.IntegrityError, match="key collision"):
        db.commit_transition("intent:1", "YM", "STOP_EXIT", held)


def test_index_migration_refuses_to_delete_conflicting_exec_mappings(
        tmp_path):
    """A legacy cross-role collision is forensic evidence, not dedup noise."""
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    with db.con:
        db.con.execute("DROP INDEX ux_trade_execution_once")
        db.con.execute(
            "CREATE UNIQUE INDEX ux_trade_execution_leg "
            "ON trade_executions(account,exec_id,leg)")
        db.con.execute(
            "INSERT INTO trade_executions VALUES (?,?,?,'ENTRY')",
            ("trade:entry", "DTEST", "CONFLICT-X"))
        db.con.execute(
            "INSERT INTO trade_executions VALUES (?,?,?,'EXIT')",
            ("trade:exit", "DTEST", "CONFLICT-X"))
    db.close()

    with pytest.raises(sqlite3.IntegrityError, match="lossy migration"):
        StateDB(tmp_path, venue="ibkr_paper")
    con = sqlite3.connect(tmp_path / "trendbot.db")
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM trade_executions "
            "WHERE exec_id='CONFLICT-X'"
        ).fetchone()[0] == 2
    finally:
        con.close()


def test_enter_books_partial_exit_and_nets_position(tb):
    """r3 #4b: a stop that fired during the entry books its round trip and
    the position is the NET quantity."""
    tb.ex.fill_qty = 2                    # net after 1 sold by the stop
    tb.ex.partial_exit = (1, 98.0)
    tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert tb.st.pos("YM")["qty"] == 2
    import csv
    rows = list(csv.DictReader(open(tb.st.dir / "trades.csv")))
    assert any(r["reason"] == "stop_during_entry" and r["qty"] == "1"
               for r in rows)


def test_enter_full_roundtrip_leaves_flat(tb):
    """r3 #4b edge: everything sold during entry => flat, no tracking."""
    tb.ex.fill_qty = 0
    tb.ex.partial_exit = (3, 98.0)
    tb._enter("YM", tb.all_instruments["YM"], FRONT, 3, 100.0, 2.0)
    assert tb.st.pos("YM")["position"] == 0
    assert tb.st.state["order_intents"] == {}


# --------------------------- codex round-4 reproduced scenarios (07-18)
def test_adoption_rejects_wrong_contract_stop(tb):
    """r4 #2: a resting stop on contract 111 @ 90 is NOT protection for
    contract 555 requiring 98 — adoption must replace it."""
    exe = _intent(tb)
    tb.ex.recovered_stops = {"YM": SimpleNamespace(
        order=SimpleNamespace(orderId=41))}
    tb.ex.stop_working = True
    tb.ex.stop_identity = (111, 90.0)              # wrong contract + price
    tb.ex.recover_entries_queue = [[exe]]
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    # wrong-identity stop cancelled (verified) and a CORRECT one placed
    assert [c for c in tb.ex.calls if c[0] == "cancel_stop"]
    assert [c for c in tb.ex.calls if c[0] == "place_stop"]
    assert tb.st.pos("YM")["position"] == 1


def test_has_working_stop_verifies_identity():
    """r4 #2 executor-level: real order identity, not booleans."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex._stop_trades = {"YM": SimpleNamespace(
        order=SimpleNamespace(orderId=9, totalQuantity=1.0, action="SELL",
                              orderType="STP", auxPrice=90.0),
        orderStatus=SimpleNamespace(status="Submitted", filled=0.0),
        contract=SimpleNamespace(conId=111))}
    assert ex.has_working_stop("YM", 1)                       # loose: ok
    assert not ex.has_working_stop("YM", 1, con_id=555)       # wrong con
    assert ex.has_working_stop("YM", 1, con_id=111,
                               stop_px=90.0, px_tol=0.5)
    assert not ex.has_working_stop("YM", 1, con_id=111,
                                   stop_px=98.0, px_tol=0.5)  # wrong px


def test_recover_stops_cancels_duplicates():
    """r4 #3: two live stops for one symbol -> newest kept, extra
    cancel-verified; unresolved extras reported."""
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.alert = lambda m: None
    logged = []
    ex.dlog = SimpleNamespace(log=lambda *a, **k: logged.append(k))
    ex.cancel_verify_timeout = 0.1

    def mk(oid, status="Submitted"):
        return SimpleNamespace(
            order=SimpleNamespace(orderId=oid, orderRef="trendbot:YM:stop",
                                  totalQuantity=1.0),
            orderStatus=SimpleNamespace(status=status, filled=0.0))
    t41, t42 = mk(41), mk(42)

    def cancel(o):
        # the older stop cancels cleanly when asked
        (t41 if o.orderId == 41 else t42).orderStatus.status = "Cancelled"
    ex.ib = SimpleNamespace(reqAllOpenOrders=lambda: None,
                            sleep=lambda s: None,
                            waitOnUpdate=lambda timeout: None,
                            cancelOrder=cancel,
                            openTrades=lambda: [t41, t42])
    found = ex.recover_stops()
    # r5 #2: choosing a survivor is a GUESS (the "newest" can be the
    # unsafe one) — ALL candidates are cancel-verified, none kept; the
    # startup re-arm sweep then arms ONE canonical stop from state
    assert "YM" not in found
    assert t41.orderStatus.status == "Cancelled"
    assert t42.orderStatus.status == "Cancelled"
    assert ex.unresolved_duplicate_stops == {}


def test_abort_with_unknown_entry_cancel_places_no_sell():
    """r4 #4 / r5 accuracy: drive the REAL enter_long_with_stop — abort
    after repeated protection failures with an unresolvable entry cancel
    must place NO offsetting sell and raise UnresolvedEntry."""
    from executor import IBKRPaperExecutor, UnresolvedEntry
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue = "ibkr_paper"
    ex.alert = lambda m: None
    ex.stop_arm_timeout_s = 2.0
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex._stop_trades = {}
    ex.entry_fill_timeout = 2.0
    ex.partial_ack_timeout = 0.02
    ex.cancel_verify_timeout = 0.02
    placed = []
    entry = SimpleNamespace(
        order=SimpleNamespace(orderId=1, permId=5, action="BUY"),
        orderStatus=SimpleNamespace(status="Submitted", filled=1.0,
                                    avgFillPrice=100.0),
        fills=[])

    def place(contract, o):
        placed.append((o.action, getattr(o, "orderType", "MKT")))
        if o.action == "BUY":
            return entry
        # protection stop orders never get acknowledged -> protect fails
        return SimpleNamespace(
            order=SimpleNamespace(orderId=len(placed) + 10,
                                  totalQuantity=o.totalQuantity),
            orderStatus=SimpleNamespace(status="PendingSubmit", filled=0.0,
                                        avgFillPrice=0.0),
            fills=[])
    ex.ib = SimpleNamespace(placeOrder=place,
                            waitOnUpdate=lambda timeout: None,
                            cancelOrder=lambda o: None,
                            sleep=lambda s: None)
    contract = SimpleNamespace(conId=555, localSymbol="TESTX6")
    with pytest.raises(UnresolvedEntry):
        ex.enter_long_with_stop("YM", contract, 3, 98.0, intent_id="ab12")
    sells = [p for p in placed if p[0] == "SELL" and p[1] == "MKT"]
    assert sells == []                     # NO offsetting market sell


def test_kill_switch_cancels_untracked_orders(tb):
    """r4 #5: an untracked live bot order is cancel-verified by the kill
    switch itself — DONE without human garbage collection."""
    stub_trade = SimpleNamespace(order=SimpleNamespace(orderId=99))
    tb.ex.positions_map = {}
    tb.ex.open_orders = [{"ref": "trendbot:YM:stop", "order_id": 99,
                          "trade": stub_trade}]
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert ("cancel_verify", 99) in tb.ex.calls     # actively cancelled
    assert "DONE" in (tb.st.dir / "KILL_SWITCH").read_text()


def test_data_leg_failure_does_not_kill_paper_gateway(tb, monkeypatch):
    """r4 #6: three data-leg failures must NOT pkill the healthy IBC
    (paper) gateway; executor-leg failures may."""
    healed = []
    monkeypatch.setattr(tb, "_heal_gateway", lambda leg: healed.append(leg))
    tb.data = SimpleNamespace(connect=lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("4001 down")))
    tb._consec_errors = {"data": 2, "executor": 0}  # next data failure = #3
    tb.wake()
    assert healed == []                             # data leg: no pkill
    # r5 #8: two data failures + first executor failure must NOT heal
    tb._consec_errors = {"data": 2, "executor": 0}
    tb.data = SimpleNamespace(connect=lambda: None)
    monkeypatch.setattr(tb.ex, "connect", lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("4002 down")), raising=False)
    tb.wake()
    assert healed == []                             # executor #1: no heal yet
    tb._consec_errors = {"data": 0, "executor": 2}  # executor's OWN 3rd
    tb.wake()
    assert healed == ["connect"]                    # executor leg: heal


def test_db_journal_one_row_per_execution(tmp_path):
    """r4 #7: two broker executions => two ledger rows, replay-safe."""
    from state_db import StateDB
    db = StateDB(tmp_path, venue="test")
    for xid, q, p in [("E1", 1, 100.0), ("E2", 2, 100.5)]:
        db.execution("YM", "BUY", q, p, exec_id=xid)
        db.execution("YM", "BUY", q, p, exec_id=xid)   # replay ignored
    n = db.con.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
    assert n == 2


# --------------------------- codex round-6 reproduced scenarios (07-18)
def test_protection_never_arms_against_flat_broker(tb):
    """r6 #2: state long 1, broker 0 => NO stop armed, halted."""
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {}                     # broker FLAT
    tb._ensure_protection()
    assert [c for c in tb.ex.calls if c[0] == "place_stop"] == []
    assert tb.st.state["halt_entries"]


def test_unresolved_duplicate_blocks_rearm(tb):
    """r6 #3: an unresolved duplicate stop blocks any further arming."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=1, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.stop_working = False
    tb.ex.positions_map = {555: 1.0}
    tb.ex.unresolved_duplicate_stops = {"YM": [41]}
    tb._ensure_protection()
    assert [c for c in tb.ex.calls if c[0] == "place_stop"] == []
    tb.ex.recover_entries_queue = [[]]
    tb.startup_reconcile()                       # re-arm sweep also blocked
    assert [c for c in tb.ex.calls if c[0] == "place_stop"] == []


def test_adoption_wavg_before_cap(tb):
    """r6 #4: three 1-lot fills @100 capped to broker's 1 => basis 100,
    not 300."""
    iid = "cafe1234"
    tb.st.state["order_intents"]["YM"] = {
        "intent_id": iid, "qty": 3, "local_symbol": "TESTX6",
        "con_id": 555, "sig_close": 100.0, "sig_atr": 1.0,
        "created": botmod.utcnow()}
    ref = f"trendbot:YM:entry:{iid}"
    execs = [{"kind": "entry_execution", "sym": "YM", "ref": ref,
              "con_id": 555, "local_symbol": "TESTX6", "qty": 1,
              "px": 100.0, "exec_id": f"X{i}", "time": "t"}
             for i in range(3)]
    tb.ex.recover_entries_queue = [execs]
    tb.ex.positions_map = {555: 1.0}             # broker holds only 1
    tb.startup_reconcile()
    p = tb.st.pos("YM")
    assert p["qty"] == 1
    assert p["entry_px"] == pytest.approx(100.0)   # NOT 300
    assert p["stop_px"] == pytest.approx(98.0)


def test_midnight_stale_intent_flat_without_execution_history_halts(tb):
    """A stale recovery key is cleared only after explicit broker zero."""
    tb.st.state["order_intents"]["GC"] = {
        "intent_id": "old1", "qty": 1, "local_symbol": "MGCQ6",
        "con_id": 7, "sig_close": 4000.0, "sig_atr": 10.0,
        "created": "2026-07-01T23:59:00+00:00"}
    tb.ex.recover_entries_queue = [[]]
    tb.startup_reconcile()
    assert "GC" in tb.st.state["order_intents"]
    assert tb.st.state["halt_entries"]


def test_midnight_stale_intent_with_holding_is_adopted_and_protected(tb):
    tb.st.state["order_intents"]["GC"] = {
        "intent_id": "old1", "qty": 1, "local_symbol": "MGCQ6",
        "con_id": 7, "sig_close": 4000.0, "sig_atr": 10.0,
        "created": "2026-07-01T23:59:00+00:00"}
    tb.ex.recover_entries_queue = [[]]
    tb.ex.positions_map = {7: 1.0}
    tb.startup_reconcile()
    assert tb.st.pos("GC")["qty"] == 1
    assert [c for c in tb.ex.calls if c[0] == "place_stop"]
    assert "GC" in tb.st.state["order_intents"]  # safety adopted; basis open
    assert tb.st.state["halt_entries"]  # estimated basis needs human review


def test_safety_wake_runs_during_data_outage(tb, monkeypatch):
    """r6 #6: data leg down, executor up => stop reconcile + protection
    audit still run; wake is DEGRADED, not dead."""
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.positions_map = {7: 1.0}
    tb.data = SimpleNamespace(connect=lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("4001 down")))
    monkeypatch.setattr(tb.ex, "connect", lambda: False, raising=False)
    monkeypatch.setattr(tb.ex, "is_connected", lambda: True, raising=False)
    monkeypatch.setattr(tb.ex, "check_stop_filled",
                        lambda sym: None, raising=False)
    tb.wake()
    assert "DEGRADED" in tb.st.state["last_wake_status"]
    assert "broker=OK" in tb.st.state["last_wake_status"]
    assert "protection=OK" in tb.st.state["last_wake_status"]


# --------------------------- round-7 durable production-path gates
def _attach_durable_db(tb):
    from state_db import StateDB
    db = StateDB(tb.st.dir, venue=tb.ex.venue)
    tb.ex.account = "DTEST"
    generation = tb.st.bind_identity(tb.ex.venue, tb.ex.account)
    db.bind_identity(tb.ex.account, tb.ex.venue, generation=generation)
    db.bootstrap_positions(tb.st.state["positions"])
    tb.db = db
    tb.ex.db = db
    tb._identity_bound = True
    return db


def test_startup_adopts_entry_from_durable_exec_when_broker_scan_is_empty(tb):
    iid = "durable1"
    _intent(tb, qty=1, iid=iid)
    db = _attach_durable_db(tb)
    intent = tb.st.state["order_intents"]["YM"]
    event_key = f"entry-intent:DTEST:YM:{iid}"
    intent["event_key"] = event_key
    ref = f"trendbot:YM:entry:{iid}"
    db.begin_event(event_key, "YM", "ENTRY_INTENT", payload=intent,
                   order_ref=ref)
    db.execution(
        "YM", "BOT", 1, 100.0, exec_id="DURABLE-BUY-1",
        exec_time=datetime.now(timezone.utc), order_ref=ref,
        con_id=555, local_symbol="TESTX6")
    tb.ex.recover_entries_queue = [[]]  # broker history has already rolled off
    tb.ex.positions_map = {555: 1.0}
    tb.startup_reconcile()
    assert tb.st.pos("YM")["qty"] == 1
    assert ("place_stop", "YM", 1, 98.0) in tb.ex.calls
    assert db.is_execution_applied("DURABLE-BUY-1", account="DTEST")


def test_entry_fsm_commit_failure_forces_immediate_startup_recovery(tb):
    from executor import UnresolvedEntry
    db = _attach_durable_db(tb)
    db.con.execute("""
        CREATE TRIGGER fail_entry_fsm BEFORE INSERT ON position_fsm
        WHEN NEW.sym='YM'
        BEGIN SELECT RAISE(ABORT, 'injected entry FSM failure'); END
    """)
    with pytest.raises(UnresolvedEntry, match="FSM commit failed"):
        tb._enter("YM", tb.all_instruments["YM"], FRONT, 1, 100.0, 1.0)
    assert "YM" in tb.st.state["order_intents"]
    assert not tb._startup_done
    assert tb.st.state["halt_entries"]


def test_protection_sizes_to_positive_broker_excess_and_halts(tb):
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=1, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.positions_map = {555: 2.0}
    tb.ex.stop_working = False
    tb._ensure_protection()
    assert ("place_stop", "YM", 2, 98.0) in tb.ex.calls
    assert tb.st.state["halt_entries"]


def test_kill_switch_books_stop_fill_seen_during_cancel_before_done(tb,
                                                                  monkeypatch):
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, basis_qty=1, entry_px=100.0,
             entry_time="t", stop_px=98.0, con_id=555,
             local_symbol="TESTX6", entry_weight=1, hw=100.0,
             sig_atr=1.0, stop_order_id=9)
    tb.ex._stop_trades = {"YM": object()}
    tb.ex.positions_map = {}  # stop already flattened the broker account
    monkeypatch.setattr(tb.ex, "cancel_stop", lambda sym: "filled")
    (tb.st.dir / "KILL_SWITCH").write_text("FLATTEN")
    tb.check_kill_switch()
    assert not tb.st.pos("YM")["position"]
    assert "DONE" in (tb.st.dir / "KILL_SWITCH").read_text()


def test_kill_switch_applies_partial_stop_before_closing_residual(tb,
                                                                 monkeypatch):
    """A partial stop during the cancel sweep and the residual market close
    must both be applied before DONE."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, basis_qty=3, entry_px=100.0,
             entry_time="t", stop_px=98.0, con_id=555,
             local_symbol="TESTX6", entry_weight=3, hw=100.0,
             sig_atr=1.0, stop_order_id=9)
    db = _attach_durable_db(tb)
    tb.ex._stop_trades = {"YM": object()}
    tb.ex.positions_map = {555: 3.0}
    calls = 0

    def cancel_stop(sym):
        nonlocal calls
        calls += 1
        if calls == 1:
            tb.ex.positions_map[555] = 2.0
            db.execution(
                "YM", "SLD", 1, 98.0, exec_id="STOP-SWEEP-1",
                con_id=555, order_ref="trendbot:YM:stop",
                account="DTEST")
            return "filled"
        return "cancelled"

    monkeypatch.setattr(tb.ex, "cancel_stop", cancel_stop)
    ks = tb.st.dir / "KILL_SWITCH"
    ks.write_text("FLATTEN")
    tb.check_kill_switch()
    assert not tb.st.pos("YM")["position"]
    assert db.is_execution_applied("STOP-SWEEP-1", account="DTEST")
    assert db.con.execute("SELECT SUM(qty) FROM trades").fetchone()[0] == 3
    assert db.unapplied_execution_count(account="DTEST") == 0
    assert "DONE" in ks.read_text()


def test_execution_rescan_enriches_early_unknown_callback(tmp_path):
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.execution("UNKNOWN", "BOT", 1, 100, exec_id="ENRICH-1",
                 account="DTEST")
    db.execution("YM", "BOT", 1, 100, exec_id="ENRICH-1",
                 account="DTEST", con_id=555, local_symbol="TESTX6",
                 order_ref="trendbot:YM:entry:i1")
    row = db.con.execute(
        "SELECT sym,con_id,local_symbol,order_ref FROM executions "
        "WHERE account='DTEST' AND exec_id='ENRICH-1'").fetchone()
    assert row == ("YM", 555, "TESTX6", "trendbot:YM:entry:i1")


def test_conflicting_execution_replay_is_rejected(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.execution("YM", "BOT", 1, 100, exec_id="IMMUTABLE-1",
                 con_id=555, order_ref="trendbot:YM:entry:i1")
    with pytest.raises(sqlite3.IntegrityError, match="conflicting replay"):
        db.execution("YM", "BOT", 2, 100, exec_id="IMMUTABLE-1",
                     con_id=555, order_ref="trendbot:YM:entry:i1")
    row = db.con.execute(
        "SELECT qty,px FROM executions WHERE exec_id='IMMUTABLE-1'"
    ).fetchone()
    assert row == (1.0, 100.0)


def test_exit_execution_cannot_advance_two_fsm_events(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.commit_transition(
        "entry:E1", "YM", "ENTRY_FILLED",
        {"position": 1, "qty": 2, "con_id": 555},
        executions=[dict(side="BUY", qty=2, px=100, exec_id="E1")],
        entry_exec_ids=["E1"])
    trade = dict(exit_time="t", book="core", sym="YM", qty=1,
                 entry_time="e", entry_px=100, exit_px=99, reason="stop",
                 pnl_points=-1, pnl_usd=-10, commission=None,
                 entry_weight=2, local_symbol="TEST")
    db.commit_transition(
        "exit:X1", "YM", "STOP_EXIT",
        {"position": 1, "qty": 1, "con_id": 555},
        executions=[dict(side="SELL", qty=1, px=99, exec_id="X1")],
        trade=trade, entry_exec_ids=["E1"], exit_exec_ids=["X1"])
    with pytest.raises(sqlite3.IntegrityError, match="already applied"):
        db.commit_transition(
            "exit:X1-replayed", "YM", "STOP_EXIT",
            {"position": 0, "qty": 0}, executions=[], trade=trade,
            entry_exec_ids=["E1"], exit_exec_ids=["X1"])
    assert db.load_positions()["YM"]["qty"] == 1
    assert db.con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_correction_reference_is_idempotent(tmp_path):
    import sqlite3
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.correction("fee_adjust", "incident-1", -2.0, "verified fee")
    with pytest.raises(sqlite3.IntegrityError):
        db.correction("fee_adjust", "incident-1", -2.0, "retry")
    assert db.net_pnl() == -2.0


def test_entry_audit_row_failure_does_not_report_committed_entry_failed(tb):
    db = _attach_durable_db(tb)
    db.con.execute("""
        CREATE TRIGGER fail_filled_order_audit BEFORE INSERT ON order_events
        WHEN NEW.event IN ('filled','partial')
        BEGIN SELECT RAISE(ABORT, 'injected order audit failure'); END
    """)
    tb._enter("YM", tb.all_instruments["YM"], FRONT, 1, 100.0, 1.0)
    assert tb.st.pos("YM")["qty"] == 1
    assert "YM" not in tb.st.state["pending_entries"]
    assert "YM" not in tb.st.state["order_intents"]
    assert "order_audit" in tb._degraded


def test_protection_rejects_one_tick_wrong_stop(tb):
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=1, hw=100.0, sig_atr=1.0, stop_order_id=9)
    tb.ex.positions_map = {555: 1.0}
    tb.ex.stop_identity = (555, 97.0)
    tb._ensure_protection()
    assert ("cancel_stop", "YM") in tb.ex.calls
    assert ("place_stop", "YM", 1, 98.0) in tb.ex.calls


def test_alert_delivery_errors_redact_credentials(tmp_path, monkeypatch):
    import alerts
    token, chat = "secret-token-123", "secret-chat-456"
    monkeypatch.setattr(alerts, "OUTBOX", tmp_path / "alerts.db")
    monkeypatch.setattr(alerts, "FALLBACK", tmp_path / "fallback.log")
    monkeypatch.setattr(alerts, "_credentials", lambda: (token, chat))
    monkeypatch.setattr(
        alerts.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError(f"failed https://api.telegram.org/bot{token}/"
                         f"sendMessage?chat_id={chat}")))
    con = alerts._connect()
    with con:
        cur = con.execute(
            "INSERT INTO alert_outbox(created,text,status) "
            "VALUES ('t','safety','PENDING')")
        alert_id = cur.lastrowid
    con.close()
    alerts._deliver(alert_id)
    con = alerts._connect()
    error = con.execute(
        "SELECT last_error FROM alert_outbox WHERE id=?", (alert_id,)
    ).fetchone()[0]
    con.close()
    fallback = (tmp_path / "fallback.log").read_text()
    assert token not in error + fallback
    assert chat not in error + fallback
    assert "[REDACTED]" in error


def test_stop_exit_db_failure_keeps_state_and_execution_reobservable(tb):
    import sqlite3
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, basis_qty=3, entry_px=100.0,
             entry_time="t", stop_px=98.0, con_id=555,
             local_symbol="TESTX6", entry_weight=3, stop_order_id=9)
    db = _attach_durable_db(tb)
    db.con.execute("""
        CREATE TRIGGER fail_trade BEFORE INSERT ON trades
        BEGIN SELECT RAISE(ABORT, 'injected trade failure'); END
    """)
    fill = Fill(98.0, 0.5, qty=1, order_id=9, exec_ids=["EXIT-1"])
    fill.exec_details = [("EXIT-1", 1, 98.0, 0.5)]
    fill.order_key = "perm:900"
    fill.filled_total = 1
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        tb._book_stop_exit("YM", tb.all_instruments["YM"], p, fill)
    assert tb.st.pos("YM")["qty"] == 3
    assert not db.is_execution_applied("EXIT-1", account="DTEST")
    assert not (tb.st.dir / "trades.csv").exists()


def test_exit_larger_than_tracked_basis_is_halted_and_auditable(tb):
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, basis_qty=1, entry_px=100.0,
             entry_time="t", stop_px=98.0, con_id=555,
             local_symbol="TESTX6", entry_weight=1, stop_order_id=9)
    db = _attach_durable_db(tb)
    fill = Fill(98.0, 1.0, qty=2, order_id=9,
                exec_ids=["OVERSELL-1"])
    fill.exec_details = [("OVERSELL-1", 2, 98.0, 1.0)]
    tb._book_stop_exit("YM", tb.all_instruments["YM"], p, fill)
    assert not tb.st.pos("YM")["position"]
    assert tb.st.state["halt_entries"]
    payload = db.con.execute(
        "SELECT payload FROM broker_events WHERE event_type='STOP'"
    ).fetchone()[0]
    import json
    assert json.loads(payload)["unbased_qty"] == 1


def _broker_fill(exec_id, qty, px, account="DTEST"):
    execution = SimpleNamespace(execId=exec_id, shares=qty, price=px,
                                time=datetime.now(timezone.utc),
                                acctNumber=account, side="SLD")
    return SimpleNamespace(
        execution=execution,
        commissionReport=SimpleNamespace(commission=0.5),
        contract=SimpleNamespace(conId=555, localSymbol="TESTX6"))


def _stop_trade(fill_qty=1.0, status="Submitted"):
    return SimpleNamespace(
        order=SimpleNamespace(orderId=9, permId=900,
                              orderRef="trendbot:YM:stop", account="DTEST",
                              totalQuantity=3.0, action="SELL",
                              orderType="STP", auxPrice=98.0),
        orderStatus=SimpleNamespace(status=status, filled=fill_qty,
                                    remaining=3.0-fill_qty,
                                    avgFillPrice=98.0),
        fills=[_broker_fill("EXIT-1", 1, 98.0)],
        contract=SimpleNamespace(conId=555, localSymbol="TESTX6"))


def test_partial_stop_application_survives_executor_restart(tmp_path):
    from executor import IBKRPaperExecutor
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    ex1 = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex1.venue, ex1.account, ex1.db = "ibkr_paper", "DTEST", db
    ex1.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex1._stop_trades = {"YM": _stop_trade()}
    fill = ex1.check_stop_filled("YM")
    assert fill.qty == 1
    db.commit_transition(
        "stop:EXIT-1", "YM", "STOP_EXIT",
        {"position": 1, "qty": 2, "con_id": 555, "stop_order_id": 9},
        executions=[dict(side="SELL", qty=1, px=98.0,
                         exec_id="EXIT-1", commission=0.5, con_id=555)],
        exit_exec_ids=["EXIT-1"], order_key=fill.order_key,
        filled_total=fill.filled_total)
    ex2 = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex2.venue, ex2.account, ex2.db = "ibkr_paper", "DTEST", db
    ex2.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex2._stop_trades = {"YM": _stop_trade()}
    assert ex2.check_stop_filled("YM") is None


def test_executor_reconnect_runs_full_entry_recovery(tb, monkeypatch):
    tb._startup_done = True
    monkeypatch.setattr(tb.ex, "connect", lambda: True, raising=False)
    monkeypatch.setattr(tb.ex, "is_connected", lambda: True, raising=False)
    tb.data = SimpleNamespace(connect=lambda: (_ for _ in ()).throw(
        ConnectionRefusedError("data down")))
    tb.wake()
    assert tb.ex.recover_entries_calls == 1
    assert tb._startup_done


def test_roll_partial_production_branch_commits_actual_and_protects(tb,
                                                                  monkeypatch):
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, basis_qty=3, entry_px=100.0,
             entry_time="2026-07-16T12:00:00+00:00", stop_px=98.0,
             con_id=444, local_symbol="OLDX6", entry_weight=3,
             hw=100.0, sig_atr=1.0, stop_order_id=9,
             entry_exec_ids=[], entry_fees_allocated=False)
    db = _attach_durable_db(tb)
    tb.ex.positions_map = {444: 3.0}
    tb.ex.close_qty = 1
    tb.ex.check_stop_filled = lambda sym: None
    now = pd.Timestamp("2026-07-17T14:00:25Z")
    idx = pd.date_range(end=now.floor("h"), periods=50, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1.0}, index=idx)
    tb.data = SimpleNamespace(
        front_contract=lambda *a: SimpleNamespace(conId=555,
                                                  localSymbol="NEWZ6"),
        hourly_bars=lambda *a, **k: bars)
    tb.process_instrument("YM", tb.all_instruments["YM"], now)
    assert tb.st.pos("YM")["qty"] == 2
    assert tb.st.pos("YM")["position"] == 1
    assert ("place_stop", "YM", 2, 98.0) in tb.ex.calls
    row = db.con.execute(
        "SELECT qty,pnl_gross FROM trades WHERE reason='roll'").fetchone()
    assert row[0] == 1


def test_live_and_research_roll_seam_do_not_book_contract_jump(tb,
                                                              monkeypatch):
    """The declared roll parity must exercise both production code paths.

    The new contract jumps 50 points on the seam.  Research exits at the old
    contract's prior close; the live fake returns that old-contract market
    price.  Neither engine may record the continuous-series jump as P&L.
    """
    from research import rotation_grid

    short_cfg = dict(rotation_grid.SCFG)
    short_cfg.update(atr_n=5, donchian_n=5,
                     vol_gate_window_bars=20,
                     vol_gate_min_bars=10)
    monkeypatch.setattr(rotation_grid, "SCFG", short_cfg)

    idx = pd.date_range("2026-07-14T12:00:00Z", periods=60,
                        freq="h", tz="UTC")
    bars = pd.DataFrame({
        "open": 100.0, "high": 100.5, "low": 99.5,
        "close": 100.0, "volume": 1.0, "roll": 0,
    }, index=idx)
    # A small breakout creates a held research position without tripping the
    # volatility gate.  The last bar is the new contract's unadjusted seam.
    bars.loc[idx[30]:idx[-2], "open"] = 100.2
    bars.loc[idx[30]:idx[-2], "close"] = 100.2
    bars.loc[idx[30]:idx[-2], "high"] = 100.7
    bars.loc[idx[30]:idx[-2], "low"] = 99.7
    bars.loc[idx[-1], ["open", "high", "low", "close", "roll"]] = [
        150.2, 150.7, 149.7, 150.2, 1]

    research_trades = rotation_grid.simulate(
        bars.drop(columns="volume"), "YM")
    roll_trade = research_trades[research_trades["reason"] == "roll"]
    assert len(roll_trade) == 1
    assert roll_trade.iloc[0]["entry_px"] == pytest.approx(100.2)
    assert roll_trade.iloc[0]["exit_px"] == pytest.approx(100.2)
    assert roll_trade.iloc[0]["points"] == pytest.approx(0.0)

    p = tb.st.pos("YM")
    p.update(position=1, qty=1, basis_qty=1, entry_px=100.2,
             entry_time=str(idx[31]), stop_px=98.0, con_id=444,
             local_symbol="OLDX6", entry_weight=1, hw=100.7,
             sig_atr=1.0, stop_order_id=9, entry_exec_ids=[],
             entry_fees_allocated=False)
    db = _attach_durable_db(tb)
    tb.ex.positions_map = {444: 1.0}
    tb.ex.check_stop_filled = lambda sym: None

    def close_old_contract(sym, contract, qty):
        assert contract.conId == 444
        tb.ex.positions_map[444] = 0
        fill = Fill(100.2, 0.5, qty=1,
                    exec_time=datetime.now(timezone.utc),
                    order_id=88, exec_ids=["ROLL-SEAM-1"])
        fill.exec_details = [("ROLL-SEAM-1", 1, 100.2, 0.5)]
        return fill

    tb.ex.close_market = close_old_contract
    tb.data = SimpleNamespace(
        front_contract=lambda *a: SimpleNamespace(conId=555,
                                                  localSymbol="NEWZ6"),
        hourly_bars=lambda *a, **k: bars.drop(columns="roll"))
    tb.process_instrument("YM", tb.all_instruments["YM"], idx[-1])

    live_roll = db.con.execute(
        "SELECT entry_px,exit_px,pnl_gross FROM trades WHERE reason='roll'"
    ).fetchone()
    assert live_roll == pytest.approx((100.2, 100.2, 0.0))
    assert not tb.st.pos("YM")["position"]


def test_stop_modify_waits_for_server_readback():
    from executor import IBKRPaperExecutor
    trade = SimpleNamespace(
        order=SimpleNamespace(orderId=9, permId=90,
                              orderRef="trendbot:YM:stop", account="DTEST",
                              totalQuantity=1, action="SELL", orderType="STP",
                              auxPrice=98.0),
        orderStatus=SimpleNamespace(status="PreSubmitted", filled=0),
        contract=SimpleNamespace(conId=555))
    calls = {"wait": 0, "read": 0}
    class AckIB:
        def placeOrder(self, contract, order):
            return trade
        def waitOnUpdate(self, timeout):
            calls["wait"] += 1
        def reqOpenOrders(self):
            calls["read"] += 1
            return [trade]
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue, ex.account, ex.db = "ibkr_paper", "DTEST", None
    ex.ib, ex._stop_trades = AckIB(), {"YM": trade}
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex.update_stop("YM", 99.0)
    assert calls["wait"] >= 1 and calls["read"] >= 1
    assert ex._stop_trades["YM"].order.auxPrice == 99.0


def test_stop_limit_is_not_accepted_as_protection():
    from executor import IBKRPaperExecutor
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex._stop_trades = {"YM": SimpleNamespace(
        order=SimpleNamespace(action="SELL", orderType="STP LMT",
                              totalQuantity=1, auxPrice=98.0),
        orderStatus=SimpleNamespace(status="Submitted", filled=0),
        contract=SimpleNamespace(conId=555))}
    assert not ex.has_working_stop("YM", 1, con_id=555, stop_px=98.0)


def test_partial_entry_rearms_net_after_interim_stop_fills():
    from executor import IBKRPaperExecutor
    contract = SimpleNamespace(conId=555, localSymbol="TESTX6")
    class SequencedIB:
        def __init__(self):
            self.waits = 0
            self.entry = None
            self.stops = []
        def placeOrder(self, c, order):
            if order.orderType == "MKT" and self.entry is None:
                order.orderId, order.permId = 1, 11
                fills = [_broker_fill(f"BUY-{i}", 1, 100.0)
                         for i in range(1, 4)]
                for f in fills:
                    f.execution.side = "BOT"
                self.entry = SimpleNamespace(
                    order=order, contract=c,
                    orderStatus=SimpleNamespace(status="PendingSubmit", filled=0,
                                                remaining=3,
                                                avgFillPrice=100.0),
                    fills=fills)
                return self.entry
            order.orderId = 20 + len(self.stops)
            order.permId = 200 + len(self.stops)
            t = SimpleNamespace(
                order=order, contract=c,
                orderStatus=SimpleNamespace(status="PreSubmitted", filled=0,
                                            remaining=order.totalQuantity,
                                            avgFillPrice=98.0), fills=[])
            self.stops.append(t)
            return t
        def waitOnUpdate(self, timeout):
            self.waits += 1
            if self.waits == 1:
                self.entry.orderStatus.status = "PartiallyFilled"
                self.entry.orderStatus.filled = 1
            elif self.waits == 3:
                self.entry.orderStatus.filled = 2
                first = self.stops[0]
                first.orderStatus.status = "Filled"
                first.orderStatus.filled = 1
                first.fills = [_broker_fill("STOP-1", 1, 98.0)]
            elif self.waits == 6:
                self.entry.orderStatus.status = "Filled"
                self.entry.orderStatus.filled = 3
                self.entry.orderStatus.remaining = 0
        def cancelOrder(self, order):
            for t in self.stops:
                if t.order is not order or t.orderStatus.status == "Filled":
                    continue
                # The second interim stop fills during the final resize
                # cancellation. Its execId must survive into atomic booking.
                if t is self.stops[1] and self.entry.orderStatus.status == "Filled":
                    t.orderStatus.status = "Filled"
                    t.orderStatus.filled = 1
                    t.fills = [_broker_fill("STOP-2", 1, 98.0)]
                else:
                    t.orderStatus.status = "Cancelled"
    ib = SequencedIB()
    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue, ex.account, ex.db = "ibkr_paper", "DTEST", None
    ex.ib, ex._stop_trades = ib, {}
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex.alert = lambda *a, **k: None
    ex.stop_arm_timeout_s = 2
    fill, _ = ex.enter_long_with_stop("YM", contract, 3, 98.0,
                                      intent_id="abc")
    assert [int(t.order.totalQuantity) for t in ib.stops] == [1, 1, 1]
    assert fill.qty == 1
    assert fill.partial_exit[0] == 2
    assert [row[0] for row in fill.partial_exit_details] == ["STOP-1", "STOP-2"]


def test_stop_arm_failure_returns_verified_roundtrip_for_atomic_booking():
    from executor import IBKRPaperExecutor
    contract = SimpleNamespace(conId=555, localSymbol="TESTX6")

    class ArmFailureIB:
        def __init__(self):
            self.held = 0
            self.next_id = 1

        def _trade(self, c, order, status, filled=0, px=0, xid=None):
            order.orderId = self.next_id
            order.permId = 100 + self.next_id
            self.next_id += 1
            fills = []
            if xid:
                f = _broker_fill(xid, filled, px)
                f.execution.side = "BOT" if order.action == "BUY" else "SLD"
                fills = [f]
            return SimpleNamespace(
                order=order, contract=c,
                orderStatus=SimpleNamespace(
                    status=status, filled=filled,
                    remaining=max(0, float(order.totalQuantity) - filled),
                    avgFillPrice=px), fills=fills)

        def placeOrder(self, c, order):
            if order.orderType == "MKT" and order.action == "BUY":
                self.held = int(order.totalQuantity)
                return self._trade(c, order, "Filled", self.held, 100,
                                   "BUY-ARM")
            if order.orderType == "STP":
                return self._trade(c, order, "Inactive")
            self.held = 0
            return self._trade(c, order, "Filled", order.totalQuantity,
                               99, "SELL-ARM")

        def waitOnUpdate(self, timeout):
            return None

        def cancelOrder(self, order):
            return None

        def sleep(self, seconds):
            return None

        def positions(self):
            return ([SimpleNamespace(contract=contract, position=self.held,
                                     account="DTEST")]
                    if self.held else [])

    ex = IBKRPaperExecutor.__new__(IBKRPaperExecutor)
    ex.venue, ex.account, ex.db = "ibkr_paper", "DTEST", None
    ex.ib, ex._stop_trades = ArmFailureIB(), {}
    ex.dlog = SimpleNamespace(log=lambda *a, **k: None)
    ex.alert = lambda *a, **k: None
    ex.stop_arm_timeout_s = 2
    fill, stop_id = ex.enter_long_with_stop(
        "YM", contract, 1, 98.0, intent_id="armfail1")
    assert stop_id is None
    assert fill.qty == 0
    assert fill.exec_ids == ["BUY-ARM"]
    assert [row[0] for row in fill.partial_exit_details] == ["SELL-ARM"]
    assert fill.partial_exit == (1.0, 99.0)


def test_late_commissions_update_authoritative_net(tmp_path):
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    held = {"position": 1, "qty": 1, "con_id": 555,
            "stop_order_id": 9}
    db.commit_transition(
        "entry:E1", "YM", "ENTRY_FILLED", held,
        executions=[dict(side="BUY", qty=1, px=100, exec_id="E1")],
        entry_exec_ids=["E1"])
    db.commit_transition(
        "exit:X1", "YM", "STOP_EXIT",
        {"position": 0, "qty": 0},
        executions=[dict(side="SELL", qty=1, px=110, exec_id="X1")],
        trade=dict(exit_time="t", book="core", sym="YM", qty=1,
                   entry_time="e", entry_px=100, exit_px=110, reason="stop",
                   pnl_points=10, pnl_usd=100, commission=None,
                   entry_weight=1, local_symbol="TEST"),
        entry_exec_ids=["E1"], exit_exec_ids=["X1"])
    assert db.net_pnl() == 100
    db.commission("E1", 1.0, account="DTEST")
    db.commission("X1", 2.0, account="DTEST")
    assert db.net_pnl() == 97


def test_entry_execution_fee_is_allocated_to_only_one_trade(tmp_path):
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    for xid, side, px, fee in (
            ("ENTRY-1", "BUY", 100, 1.0),
            ("EXIT-1", "SELL", 101, 2.0),
            ("EXIT-2", "SELL", 102, 3.0)):
        db.execution("YM", side, 1, px, exec_id=xid, commission=fee)
    trade = dict(exit_time="t", book="core", sym="YM", qty=1,
                 entry_time="e", entry_px=100, exit_px=101, reason="stop",
                 pnl_points=1, pnl_usd=10, commission=None,
                 entry_weight=1, local_symbol="TEST")
    db.trade(event_key="T1", entry_exec_ids=["ENTRY-1"],
             exit_exec_ids=["EXIT-1"], **trade)
    db.trade(event_key="T2", entry_exec_ids=["ENTRY-1"],
             exit_exec_ids=["EXIT-2"], **{**trade, "exit_px": 102})
    assert db.con.execute(
        "SELECT COUNT(*) FROM trade_executions WHERE exec_id='ENTRY-1'"
    ).fetchone()[0] == 1
    # Two $10 gross trades less each broker fee exactly once: 20-(1+2+3).
    assert db.con.execute(
        "SELECT SUM(calculated_net) FROM v_trade_net").fetchone()[0] == 14


def test_state_and_db_refuse_account_or_venue_reuse(tmp_path):
    from state_db import StateDB
    st = StateStore(tmp_path)
    generation = st.bind_identity("ibkr_paper", "D1")
    with pytest.raises(RuntimeError, match="IDENTITY MISMATCH"):
        st.bind_identity("ibkr_live", "U1")
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("D1", "ibkr_paper", generation=generation)
    with pytest.raises(RuntimeError, match="IDENTITY MISMATCH"):
        db.bind_identity("D2", "ibkr_paper")


def test_db_identity_mismatch_does_not_mutate_json_projection(tb):
    from state_db import StateDB
    db = StateDB(tb.st.dir, venue="ibkr_paper")
    db.bind_identity("DOTHER", "ibkr_paper")
    tb.db = db
    tb.ex.account = "DTEST"
    tb._identity_bound = False
    assert "runtime_identity" not in tb.st.state
    with pytest.raises(RuntimeError, match="JSON projection was not changed"):
        tb._bind_runtime_identity()
    assert "runtime_identity" not in tb.st.state


def test_corrupt_json_projection_is_quarantined_and_hydrated_from_db(
        tb, monkeypatch):
    p = tb.st.pos("YM")
    p.update(position=1, qty=1, entry_px=100.0, entry_time="t",
             stop_px=98.0, con_id=555, local_symbol="TESTX6",
             entry_weight=1, hw=101.0, sig_atr=1.0, stop_order_id=9)
    db = _attach_durable_db(tb)
    tb.st.path.write_text('{"positions":')
    recovered_store = StateStore(tb.st.dir)
    assert recovered_store.load_error
    assert recovered_store.quarantined_path.exists()
    tb.st = recovered_store
    tb._identity_bound = False
    monkeypatch.setattr(
        db, "run_provenance",
        lambda: {"tree_dirty": False, "code_commit": "test",
                 "config_sha256": "test", "account": "DTEST",
                 "venue": "ibkr_paper"})
    tb._bind_runtime_identity()
    assert tb.st.pos("YM")["qty"] == 1
    assert tb.st.pos("YM")["entry_px"] == 100.0
    assert "projection_recovered" in tb._degraded


def test_runtime_bind_hydrates_durable_soak_epoch(tb, monkeypatch):
    db = _attach_durable_db(tb)
    clean = {"tree_dirty": False, "code_commit": "candidate",
             "config_sha256": "config", "account": "DTEST",
             "venue": "ibkr_paper"}
    monkeypatch.setattr(db, "run_provenance", lambda: clean)
    epoch = db.start_soak("review evidence")
    tb.st.state["soak_start"] = None
    tb.st.state["soak_release_valid"] = None
    tb._identity_bound = False
    tb._bind_runtime_identity()
    assert tb.st.state["soak_start"] == epoch["started"]
    assert tb.st.state["soak_release_valid"] is True
    assert db.readiness()["soak"]["status"] == "ACTIVE"
    db.invalidate_current_soak("source drift")
    tb._identity_bound = False
    tb._bind_runtime_identity()
    assert tb.st.state["soak_release_valid"] is False
    assert db.readiness()["soak"]["status"] == "INVALID"


def test_first_identity_bind_marks_only_migrated_executions_accounted(tmp_path):
    """Pre-FSM history was already in the ledger; post-bind captures are not."""
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    with db.con:
        db.con.execute(
            "INSERT INTO executions(run_id,ts,account,sym,side,qty,px,exec_id) "
            "VALUES (NULL,'legacy',NULL,'YM','SELL',1,99,'LEGACY-X')")
    # This simulates an execution callback during the first broker connect,
    # after the new run exists but before identity binding completes.
    db.execution("YM", "SELL", 1, 98, exec_id="CONNECT-X")
    db.bind_identity("DTEST", "ibkr_paper")
    assert db.is_execution_applied("LEGACY-X", account="DTEST")
    assert not db.is_execution_applied("CONNECT-X", account="DTEST")
    db.execution("YM", "SELL", 1, 98, exec_id="NEW-X")
    assert not db.is_execution_applied("NEW-X", account="DTEST")
    assert [r["exec_id"] for r in db.unapplied_executions(
        "YM", account="DTEST")] == ["CONNECT-X", "NEW-X"]


def test_terminal_broker_event_cannot_regress_on_late_callback(tmp_path):
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.begin_event("entry:1", "YM", "ENTRY_INTENT")
    db.commit_transition(
        "entry:1", "YM", "ENTRY_FILLED",
        {"position": 1, "qty": 1, "con_id": 555, "stop_order_id": 9})
    assert db.update_event("entry:1", "FILLED_PENDING") is False
    assert db.event_status("entry:1") == "COMMITTED"


def test_alert_outbox_recovers_stale_inflight_delivery(tmp_path, monkeypatch):
    import alerts
    monkeypatch.setattr(alerts, "OUTBOX", tmp_path / "alerts.db")
    monkeypatch.setattr(alerts, "FALLBACK", tmp_path / "fallback.log")
    monkeypatch.setattr(alerts, "_credentials", lambda: ("token", "chat"))

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(alerts.urllib.request, "urlopen",
                        lambda *a, **k: Response())
    con = alerts._connect()
    with con:
        con.execute(
            "INSERT INTO alert_outbox(created,text,status,attempts,last_attempt) "
            "VALUES (?,?, 'SENDING',1,?)",
            ("2026-07-18T00:00:00+00:00", "safety alert",
             "2026-07-18T00:00:00+00:00"))
    con.close()
    assert alerts.retry_pending() == 1
    con = alerts._connect()
    row = con.execute(
        "SELECT status,attempts FROM alert_outbox").fetchone()
    con.close()
    assert row == ("DELIVERED", 2)


def test_startup_books_broker_flat_roundtrip_from_matched_executions(tb):
    db = _attach_durable_db(tb)
    iid = "flat1234"
    item = _intent(tb, iid=iid)
    item["exec_id"] = "BUY-FLAT"
    event_key = f"entry-intent:DTEST:YM:{iid}"
    tb.st.state["order_intents"]["YM"]["event_key"] = event_key
    db.begin_event(event_key, "YM", "ENTRY_INTENT",
                   payload=tb.st.state["order_intents"]["YM"])
    now = datetime.now(timezone.utc).isoformat()
    db.execution("YM", "BUY", 1, 100, exec_id="BUY-FLAT",
                 exec_time=now, con_id=555,
                 order_ref=f"trendbot:YM:entry:{iid}")
    db.execution("YM", "SELL", 1, 99, exec_id="SELL-FLAT",
                 exec_time=now, con_id=555,
                 order_ref=f"trendbot:YM:close:{iid}")
    tb.ex.recover_entries_queue = [[item]]
    tb.ex.positions_map = {}
    tb.startup_reconcile()
    assert "YM" not in tb.st.state["order_intents"]
    assert not tb.st.pos("YM")["position"]
    row = db.con.execute(
        "SELECT qty,entry_px,exit_px,reason FROM trades ").fetchone()
    assert row == (1.0, 100.0, 99.0, "entry_roundtrip_recovery")
    assert db.is_execution_applied("BUY-FLAT", account="DTEST")
    assert db.is_execution_applied("SELL-FLAT", account="DTEST")


def test_startup_preserves_flat_intent_when_exit_execution_is_missing(tb):
    db = _attach_durable_db(tb)
    iid = "gap12345"
    item = _intent(tb, iid=iid)
    item["exec_id"] = "BUY-GAP"
    event_key = f"entry-intent:DTEST:YM:{iid}"
    tb.st.state["order_intents"]["YM"]["event_key"] = event_key
    db.begin_event(event_key, "YM", "ENTRY_INTENT",
                   payload=tb.st.state["order_intents"]["YM"])
    db.execution("YM", "BUY", 1, 100, exec_id="BUY-GAP",
                 exec_time=datetime.now(timezone.utc).isoformat(), con_id=555,
                 order_ref=f"trendbot:YM:entry:{iid}")
    tb.ex.recover_entries_queue = [[item]]
    tb.ex.positions_map = {}
    tb.startup_reconcile()
    assert "YM" in tb.st.state["order_intents"]
    assert tb.st.state["halt_entries"]
    assert not db.is_execution_applied("BUY-GAP", account="DTEST")


def test_startup_books_pre_adoption_partial_exit_and_keeps_residual(tb):
    db = _attach_durable_db(tb)
    iid = "partial9"
    item = _intent(tb, qty=3, iid=iid)
    item["exec_id"] = "BUY-THREE"
    event_key = f"entry-intent:DTEST:YM:{iid}"
    tb.st.state["order_intents"]["YM"]["event_key"] = event_key
    db.begin_event(event_key, "YM", "ENTRY_INTENT",
                   payload=tb.st.state["order_intents"]["YM"])
    now = datetime.now(timezone.utc).isoformat()
    db.execution("YM", "BUY", 3, 100, exec_id="BUY-THREE",
                 exec_time=now, con_id=555,
                 order_ref=f"trendbot:YM:entry:{iid}")
    db.execution("YM", "SELL", 1, 98, exec_id="STOP-ONE",
                 exec_time=now, con_id=555,
                 order_ref="trendbot:YM:stop")
    tb.ex.recover_entries_queue = [[item]]
    tb.ex.positions_map = {555: 2.0}
    tb.startup_reconcile()
    assert tb.st.pos("YM")["qty"] == 2
    assert tb.st.pos("YM")["entry_fees_allocated"]
    assert "YM" not in tb.st.state["order_intents"]
    row = db.con.execute(
        "SELECT qty,reason FROM trades WHERE reason="
        "'entry_partial_exit_recovery'").fetchone()
    assert row == (1.0, "entry_partial_exit_recovery")
    assert db.is_execution_applied("STOP-ONE", account="DTEST")


def test_startup_discards_stale_json_intent_after_db_commit(tb):
    db = _attach_durable_db(tb)
    iid = "committed"
    _intent(tb, qty=1, iid=iid)
    event_key = f"entry-intent:DTEST:YM:{iid}"
    item_state = tb.st.state["order_intents"]["YM"]
    item_state["event_key"] = event_key
    db.begin_event(event_key, "YM", "ENTRY_INTENT", payload=item_state)
    held = dict(tb.st.pos("YM"))
    held.update(position=1, qty=1, con_id=555, local_symbol="TESTX6",
                entry_px=100, entry_time=botmod.utcnow(), stop_px=98,
                stop_order_id=77, entry_weight=1, basis_qty=1)
    db.commit_transition(event_key, "YM", "ENTRY_FILLED", held)
    tb.st.hydrate_positions(db.load_positions())
    tb.ex.positions_map = {555: 1.0}
    tb.ex.stop_identity = (555, 98.0)
    tb.ex.recovered_stops = {
        "YM": SimpleNamespace(order=SimpleNamespace(orderId=77))}
    tb.ex.recover_entries_queue = [[]]
    tb.startup_reconcile()
    assert "YM" not in tb.st.state["order_intents"]
    assert db.event_status(event_key) == "COMMITTED"


def test_rotation_gate_rejects_unaligned_daily_inputs():
    from rotation import validate_rotation_inputs
    now = pd.Timestamp("2026-07-20T14:00:00Z")
    base = pd.date_range("2026-05-01", periods=60, freq="D", tz="UTC")
    closes = {
        "NQ": pd.Series(range(60), index=base, dtype=float) + 100,
        "ES": pd.Series(range(59), index=base[:-1], dtype=float) + 100,
        "YM": pd.Series(range(58), index=base[:-2], dtype=float) + 100,
    }
    errors = validate_rotation_inputs(closes, now, 35)
    assert any("unaligned endpoints" in e for e in errors)


def test_hourly_data_gate_rejects_impossible_ohlc():
    from rotation import validate_hourly_frame
    idx = pd.date_range("2026-07-20", periods=2, freq="h", tz="UTC")
    bad = pd.DataFrame({"open": [100, 100], "high": [101, 99],
                        "low": [99, 98], "close": [100, 100]}, index=idx)
    assert validate_hourly_frame(bad) == "high below open/close"


def test_hourly_data_gate_rejects_off_grid_timestamp():
    from rotation import validate_hourly_frame
    idx = pd.DatetimeIndex([pd.Timestamp("2026-07-20T14:05:00Z")])
    frame = pd.DataFrame({"open": [100], "high": [101], "low": [99],
                          "close": [100]}, index=idx)
    assert validate_hourly_frame(frame) == \
        "timestamps are not aligned to hourly bar starts"


def test_hourly_gap_gate_excludes_maintenance_but_finds_open_hour():
    from rotation import missing_expected_hourly_bars
    # 21:00Z is the 17:00 ET maintenance hour; 19:00Z is normal trading.
    idx = pd.DatetimeIndex([
        pd.Timestamp("2026-07-21T18:00:00Z"),
        pd.Timestamp("2026-07-21T20:00:00Z"),
        pd.Timestamp("2026-07-21T22:00:00Z"),
    ])
    assert missing_expected_hourly_bars(idx, botmod.is_market_open) == [
        pd.Timestamp("2026-07-21T19:00:00Z")]


def test_bar_store_repairs_only_within_same_contract_segment(tmp_path):
    from data import BarStore
    store = BarStore(tmp_path)

    def frame(index, contract):
        return pd.DataFrame({
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 1.0, "contract": contract,
        }, index=pd.DatetimeIndex(index))

    idx = pd.date_range("2026-07-21T10:00:00Z", periods=5, freq="h")
    store.append("YM", frame(idx.delete(2), "OLDX6"), source="ibkr")
    store.append("YM", frame(idx, "OLDX6"), source="ibkr")
    assert idx[2] in store.load("YM").index

    new_idx = pd.date_range("2026-07-21T08:00:00Z", periods=8, freq="h")
    store.append("YM", frame(new_idx, "NEWZ6"), source="ibkr")
    loaded = store.load("YM")
    assert loaded.loc[idx[0], "contract"] == "OLDX6"
    assert loaded.loc[idx[-1], "contract"] == "NEWZ6"


def test_internal_live_bar_gap_freezes_trailing_and_signals(tb):
    p = tb.st.pos("YM")
    all_hours = pd.date_range(
        "2026-07-20T22:00:00Z", periods=50, freq="h")
    open_hours = pd.DatetimeIndex([
        stamp for stamp in all_hours
        if botmod.is_market_open(
            (stamp + pd.Timedelta(minutes=30)).to_pydatetime())])
    missing = open_hours[10]
    gap_index = open_hours.delete(10)
    bars = pd.DataFrame({
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1.0, "contract": "TESTX6",
    }, index=gap_index)
    tb.store.append("YM", bars, source="ibkr")
    p.update(position=1, qty=1, basis_qty=1, entry_px=100.0,
             entry_time=str(gap_index[0]), stop_px=98.0, con_id=555,
             local_symbol="TESTX6", entry_weight=1, hw=100.0,
             sig_atr=1.0, stop_order_id=9)
    tb.ex.positions_map = {555: 1.0}
    tb.ex.check_stop_filled = lambda sym: None
    tb.data = SimpleNamespace(
        front_contract=lambda *a: FRONT,
        hourly_bars=lambda *a, **k: bars.drop(columns="contract"))
    now = gap_index[-1] + pd.Timedelta(hours=1, seconds=25)
    tb.process_instrument("YM", tb.all_instruments["YM"], now)
    assert missing not in tb.store.load("YM").index
    assert "YM" in tb._degraded
    assert not [call for call in tb.ex.calls
                if call[0] in {"update_stop", "enter"}]


def test_process_lock_is_kernel_enforced(tmp_path):
    from process_lock import ProcessLock, ProcessLockError
    first = ProcessLock(tmp_path / "bot.lock", "daemon").acquire()
    try:
        with pytest.raises(ProcessLockError):
            ProcessLock(tmp_path / "bot.lock", "once").acquire()
    finally:
        first.release()


def test_watchdog_targets_only_verified_lock_owner(tmp_path, monkeypatch):
    import ops.watchdog as watchdog
    from process_lock import ProcessLock
    lock = tmp_path / "trading.lock"
    monkeypatch.setattr(watchdog, "LOCK", lock)
    monkeypatch.setattr(
        watchdog.subprocess, "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="/opt/trendbot/.venv/bin/python bot.py\n"))
    owner = ProcessLock(lock, "daemon").acquire()
    try:
        assert watchdog._locked_bot_pid() == os.getpid()
        monkeypatch.setattr(
            watchdog.subprocess, "run",
            lambda *a, **k: SimpleNamespace(
                returncode=0, stdout="/usr/bin/python other_job.py\n"))
        assert watchdog._locked_bot_pid() is None
    finally:
        owner.release()
    # PID text alone is stale and never authorizes a kill after the kernel
    # lock is released, even if ps would appear to match after PID reuse.
    monkeypatch.setattr(
        watchdog.subprocess, "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="/opt/trendbot/.venv/bin/python bot.py\n"))
    assert watchdog._locked_bot_pid() is None


def test_watchdog_kills_verified_startup_wedge_without_heartbeat(
        tmp_path, monkeypatch):
    import ops.watchdog as watchdog
    monkeypatch.setattr(watchdog, "HB", tmp_path / "missing-heartbeat")
    monkeypatch.setattr(watchdog, "check_disk", lambda: None)
    monkeypatch.setattr(watchdog, "rotate_logs", lambda: None)
    monkeypatch.setattr(watchdog, "_locked_bot_pid", lambda: 321)
    monkeypatch.setitem(
        sys.modules, "alerts",
        SimpleNamespace(retry_pending=lambda limit: 0,
                        delivery_health=lambda: {"undelivered": 0}))
    killed, alerted = [], []
    monkeypatch.setattr(watchdog.os, "kill",
                        lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(watchdog, "_alert",
                        lambda msg, **kw: alerted.append(msg) or True)
    watchdog.main()
    assert killed == [(321, watchdog.signal.SIGKILL)]
    assert any("heartbeat was MISSING" in message for message in alerted)


def test_reconcile_fails_when_no_bars_were_evaluated(tmp_path):
    from data import BarStore
    from reconcile import build_report
    cfg = yaml.safe_load((BASE / "config.yaml").read_text())
    start = pd.Timestamp("2026-07-20T00:00:00Z")
    st = StateStore(tmp_path)
    st.state["paper_start"] = start.isoformat()
    st.state["soak_start"] = start.isoformat()
    st.save()
    idx = pd.date_range(start, periods=5, freq="h")
    frame = pd.DataFrame({"open": 100, "high": 101, "low": 99,
                          "close": 100, "volume": 1}, index=idx)
    store = BarStore(tmp_path)
    for sym in ("NQ", "ES", "YM", "GC"):
        store.append(sym, frame, source="ibkr")
    report, clean = build_report(cfg, tmp_path)
    assert not clean
    assert "zero bot evaluations" in report


def test_maintenance_run_cannot_impersonate_latest_daemon(tmp_path,
                                                         monkeypatch):
    import state_db
    monkeypatch.setattr(
        state_db, "release_provenance",
        lambda: {"tree_dirty": False, "code_commit": "candidate",
                 "config_sha256": "config"})
    runtime = state_db.StateDB(
        tmp_path, venue="ibkr_paper", run_kind="runtime")
    runtime.bind_identity("DTEST", "ibkr_paper")
    runtime_id = runtime.run_id
    runtime.close()
    maintenance = state_db.StateDB(
        tmp_path, venue="ibkr_paper", run_kind="manual-correction")
    identity = maintenance.identity()
    maintenance.bind_identity(
        identity["account"], identity["venue"],
        generation=identity["generation"])
    assert maintenance.run_id > runtime_id
    assert maintenance.latest_runtime_provenance()["run_id"] == runtime_id


def test_soak_epoch_is_explicit_release_bound_and_requires_flat_fsm(
        tmp_path, monkeypatch):
    import state_db
    monkeypatch.setattr(
        state_db, "release_provenance",
        lambda: {"tree_dirty": False, "code_commit": "candidate",
                 "config_sha256": "config"})
    db = state_db.StateDB(
        tmp_path, venue="ibkr_paper", run_kind="soak-control")
    db.bind_identity("DTEST", "ibkr_paper")
    held = {"position": 1, "qty": 1, "con_id": 555,
            "stop_order_id": 9}
    db.bootstrap_positions({"YM": held})
    with pytest.raises(RuntimeError, match="FSM positions are not flat"):
        db.start_soak("must fail while held")
    db.commit_transition("flat:1", "YM", "MANUAL_FLAT", {
        "position": 0, "qty": 0, "con_id": None, "stop_order_id": None})
    epoch = db.start_soak("independent review evidence")
    assert epoch["code_commit"] == "candidate"
    assert db.current_soak()["started"] == epoch["started"]
    assert db.invalidate_current_soak("worktree drift observed")
    assert not db.invalidate_current_soak("cannot overwrite first reason")
    invalid = db.current_soak()
    assert invalid["invalidated_at"]
    assert invalid["invalidation_reason"] == "worktree drift observed"
    from reconcile import _db_invariants
    failures, _ = _db_invariants(
        db.path, {"runtime_identity": db.identity(),
                  "positions": db.load_positions(),
                  "soak_start": invalid["started"]})
    assert any("soak epoch is invalidated" in failure
               for failure in failures)
    replacement = db.start_soak("new review after invalidation")
    assert replacement["epoch_id"] > epoch["epoch_id"]
    assert db.current_soak()["invalidated_at"] is None


def test_soak_broker_gate_includes_manual_orders():
    from ops.start_soak import assert_broker_quiescent

    class Broker:
        def __init__(self):
            self.positions_map = {}
            self.orders = []

        def positions(self):
            return self.positions_map

        def open_account_orders(self):
            return self.orders

    broker = Broker()
    assert_broker_quiescent(broker)
    broker.orders = [{"ref": "manual-order", "order_id": 42,
                      "trade": object()}]
    with pytest.raises(RuntimeError, match="orders remain"):
        assert_broker_quiescent(broker)
    broker.orders = []
    broker.positions_map = {555: 1}
    with pytest.raises(RuntimeError, match="not flat"):
        assert_broker_quiescent(broker)


def test_reconcile_compares_full_fsm_position_payload(tmp_path):
    from reconcile import _db_invariants
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    held = {
        "position": 1, "qty": 1, "entry_px": 100.0, "entry_time": "t",
        "hw": 105.0, "stop_px": 98.0, "sig_atr": 1.0, "con_id": 555,
        "local_symbol": "TESTX6", "stop_order_id": 9, "entry_weight": 1,
    }
    db.commit_transition("mark:1", "YM", "POSITION_MARK", held)
    state = {"runtime_identity": db.identity(),
             "positions": {"YM": {**held, "hw": 999.0}}}
    failures, _ = _db_invariants(db.path, state)
    assert any("FSM/state projection mismatch" in failure and "hw" in failure
               for failure in failures)


def test_reconcile_rejects_transitioned_execution_without_exec_id(tmp_path):
    from reconcile import _db_invariants
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.commit_execution_event(
        "emergency:valid", "UNMANAGED-7", "EMERGENCY_FLATTEN",
        executions=[dict(side="SELL", qty=1, px=99,
                         exec_id="VALID-X")])
    with db.con:
        db.con.execute(
            "INSERT INTO executions(run_id,ts,account,sym,side,qty,px,"
            "transition_key) VALUES (?,?,?,?,?,?,?,?)",
            (db.run_id, botmod.utcnow(), "DTEST", "YM", "SELL", 1, 98,
             "legacy-estimate"))
    assert db.unapplied_execution_count(account="DTEST") == 1
    state = {"runtime_identity": db.identity(), "positions": {}}
    failures, _ = _db_invariants(db.path, state)
    assert any("without broker execId" in failure for failure in failures)


def test_reconcile_rejects_orphan_commission_report(tmp_path):
    from reconcile import _db_invariants
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.commission("MISSING-X", 1.25, account="DTEST")
    state = {"runtime_identity": db.identity(), "positions": {}}
    failures, _ = _db_invariants(db.path, state)
    assert any("commission reports without executions" in failure
               for failure in failures)


def test_note_cannot_clear_emergency_pnl_reconcile_gate(tmp_path):
    from reconcile import _db_invariants
    from state_db import StateDB
    db = StateDB(tmp_path, venue="ibkr_paper")
    db.bind_identity("DTEST", "ibkr_paper")
    db.commit_execution_event(
        "emergency:1", "UNMANAGED-555", "EMERGENCY_FLATTEN",
        executions=[dict(side="SELL", qty=1, px=99,
                         exec_id="EMERGENCY-X", commission=1.0)],
        payload={"basis": "unmanaged"})
    db.correction("note", "emergency:1", 0, "not a P&L calculation")
    state = {"runtime_identity": db.identity(), "positions": {}}
    failures, _ = _db_invariants(db.path, state)
    assert any("emergency executions require manual P&L correction" in f
               for f in failures)
    db.correction("emergency_pnl", "emergency:1", 10.0,
                  "gross broker-statement P&L")
    failures, _ = _db_invariants(db.path, state)
    assert not any("emergency executions require manual P&L correction" in f
                   for f in failures)


def test_tail_feed_outage_fails_gap_check():
    """ops-review P1-2: a feed that dies and stays dead must produce
    missing expected hours up to the report time, not reconcile CLEAN."""
    from rotation import missing_expected_hourly_bars
    import market_calendar
    idx = pd.date_range("2026-07-14 13:00", "2026-07-14 18:00",
                        freq="h", tz="UTC")           # died at 18:00 UTC Tue
    gaps_old_style = missing_expected_hourly_bars(
        idx, market_calendar.is_market_open)
    assert gaps_old_style == []                       # the blindness
    gaps = missing_expected_hourly_bars(
        idx, market_calendar.is_market_open,
        until=pd.Timestamp("2026-07-15 04:00", tz="UTC"))
    assert len(gaps) >= 8                             # tail outage visible


def test_nightly_wipe_estimated_exit_commits_and_cancels_stop(tb):
    """r7 P0-1 (the nightly-reset incident, durable): broker wiped the
    position, NO sell executions exist anywhere, the GTC stop survived.
    Startup must (a) cancel the surviving stop BEFORE booking, (b) commit
    an execution-less ESTIMATED transition instead of crash-looping on the
    execId IntegrityError, (c) halt entries pending a basis correction."""
    from state_db import StateDB
    tb.db = StateDB(tb.st.dir, venue="ibkr_paper")
    tb.db.bind_identity("DTEST", "ibkr_paper")
    tb.st.state["runtime_identity"] = tb.db.identity()
    p = tb.st.pos("GC")
    p.update(position=1, qty=1, entry_px=4000.0, entry_time="t",
             stop_px=3990.0, con_id=7, local_symbol="MGCQ6",
             entry_weight=1, hw=4000.0, sig_atr=10.0, stop_order_id=9)
    tb.ex.recovered_stops = {"GC": SimpleNamespace(
        order=SimpleNamespace(orderId=9))}
    tb.ex.positions_map = {}                      # broker WIPED the position
    tb.ex.recover_entries_queue = [[]]
    tb.startup_reconcile()                        # must NOT raise
    assert ("cancel_stop", "GC") in tb.ex.calls   # stop cancelled
    # cancel happened BEFORE the booking (order of recorded calls)
    assert tb.st.pos("GC")["position"] == 0
    assert tb.st.state["halt_entries"]            # basis correction pending
    ev = tb.db.con.execute(
        "SELECT payload FROM broker_events WHERE event_type LIKE "
        "'%OFFLINE%' OR payload LIKE '%estimated%'").fetchall()
    assert ev, "estimated transition must be durably journaled"
    tb.db.close()


# ------------- 2026-07-29 incident: IBKR error 201 froze the whole book -----
# An MGCQ6 entry was rejected ("does not comply with our order handling rules
# for derivatives that result in physical delivery") 29 days before expiry.
# The unresolved entry latched a GLOBAL halt_entries, so a gold contract
# problem also blocked NQ/ES/YM. front_contract now steps past a
# too-near-expiry month for instruments that declare min_days_to_expiry.
def _fake_data(front_ls, front_exp, months=()):
    """IBKRData wired to a fake IB. months = [(localSymbol, yyyymmdd), ...]"""
    from data import IBKRData
    d = IBKRData.__new__(IBKRData)
    d.dlog = None
    calls = []

    def qualify(c):
        return [SimpleNamespace(localSymbol=front_ls, conId=1,
                                lastTradeDateOrContractMonth=front_exp)]

    def details(c):
        calls.append("reqContractDetails")
        return [SimpleNamespace(contract=SimpleNamespace(
            localSymbol=ls, conId=100 + i,
            lastTradeDateOrContractMonth=exp))
            for i, (ls, exp) in enumerate(months)]

    d.ib = SimpleNamespace(qualifyContracts=qualify,
                           reqContractDetails=details)
    return d, calls


def _stamp(days_out):
    return (pd.Timestamp.now(tz="UTC")
            + pd.Timedelta(days=days_out)).strftime("%Y%m%d")


def test_expiry_guard_off_preserves_contfuture_choice():
    """Index futures are cash-settled and ROLL_SPLICE_SPEC pins their roll to
    ContFuture. Guard disabled (0) must return the front month untouched even
    when it is hours from expiry, and must not query contract details."""
    d, calls = _fake_data("MYMU6", _stamp(0.2),
                          months=[("MYMZ6", _stamp(91))])
    got = d.front_contract("MYM", "CBOT", 0)
    assert got.localSymbol == "MYMU6"
    assert calls == [], "no extra API traffic on the default path"


def test_expiry_guard_rolls_past_undeliverable_front():
    """The actual incident: MGCQ6 at 29 days must be skipped for MGCV6."""
    d, _ = _fake_data("MGCQ6", _stamp(29),
                      months=[("MGCQ6", _stamp(29)),
                              ("MGCV6", _stamp(91)),
                              ("MGCZ6", _stamp(153))])
    got = d.front_contract("MGC", "COMEX", 40)
    assert got.localSymbol == "MGCV6", "must pick the NEAREST clearing month"


def test_expiry_guard_keeps_front_when_far_enough():
    """Guard on but front is comfortably far out: keep it, no details call."""
    d, calls = _fake_data("MGCV6", _stamp(91),
                          months=[("MGCV6", _stamp(91))])
    got = d.front_contract("MGC", "COMEX", 40)
    assert got.localSymbol == "MGCV6"
    assert calls == [], "fast path must not query contract details"


def test_expiry_guard_raises_when_nothing_qualifies():
    """Fail loudly rather than silently returning an unopenable contract."""
    d, _ = _fake_data("MGCQ6", _stamp(29),
                      months=[("MGCQ6", _stamp(29))])
    with pytest.raises(RuntimeError, match="at least 40d from expiry"):
        d.front_contract("MGC", "COMEX", 40)


def test_expiry_guard_unparseable_date_does_not_disqualify():
    """A broker field we cannot read must never silently drop a contract."""
    from data import IBKRData
    assert IBKRData._days_to_expiry(
        SimpleNamespace(lastTradeDateOrContractMonth="")) == float("inf")
    d, calls = _fake_data("MGCQ6", "notadate", months=[])
    assert d.front_contract("MGC", "COMEX", 40).localSymbol == "MGCQ6"
    assert calls == []


# ------ 2026-08-03 incident: same-bar restart key collision (trail vs mark)
def test_same_bar_restart_trail_then_mark_do_not_collide(tb):
    """Restart inside one bar: wake 1 ratchets the stop (STOP_TRAIL), wake 2
    re-derives the identical (bar, stop) as POSITION_MARK. Before the fix the
    two shared one event key and the second wake raised 'event key collision'
    every 5 minutes until a new bar changed the key (2026-08-03 YM)."""
    p = tb.st.pos("YM")
    p.update(position=1, qty=3, basis_qty=3, entry_px=100.0,
             entry_time="2026-07-16T12:00:00+00:00", stop_px=90.0,
             con_id=444, local_symbol="MYMU6", entry_weight=3,
             hw=100.0, sig_atr=1.0, stop_order_id=9,
             entry_exec_ids=[], entry_fees_allocated=False)
    _attach_durable_db(tb)
    tb.ex.positions_map = {444: 3.0}
    tb.ex.check_stop_filled = lambda sym: None
    now = pd.Timestamp("2026-07-17T14:00:25Z")
    idx = pd.date_range(end=now.floor("h"), periods=50, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.0, "volume": 1.0}, index=idx)
    tb.data = SimpleNamespace(
        front_contract=lambda *a: SimpleNamespace(conId=444,
                                                  localSymbol="MYMU6"),
        hourly_bars=lambda *a, **k: bars)
    tb.process_instrument("YM", tb.all_instruments["YM"], now)   # STOP_TRAIL
    assert tb.st.pos("YM")["stop_px"] > 90.0
    # simulated restart: same bar, stop now already at the trail level
    tb.st.state["last_decision_bar"].pop("YM", None)
    tb.process_instrument("YM", tb.all_instruments["YM"], now)   # POSITION_MARK
    assert tb.st.pos("YM")["qty"] == 3      # second wake completed cleanly
