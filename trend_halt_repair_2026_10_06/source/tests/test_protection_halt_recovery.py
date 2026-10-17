"""Regression for the Oct 5 ES_S protection halt surviving its closed trade."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))
from test_failure_injection import tb, _attach_durable_db  # noqa: F401,E402


@pytest.fixture
def halted(tb):
    tb.all_instruments["ES_S"] = dict(tb.all_instruments["NQ"])
    tb.st.pos("ES_S")
    tb.st.state.update(halt_entries=True, halt_reason="ES_S position unprotected")
    p = tb.st.pos("NQ")
    p.update(position=1, qty=3, con_id=555, stop_order_id=53, stop_px=31495.5)
    tb.ex.positions_map = {555: 3.0}
    tb.ex.is_connected = lambda: True
    trade = SimpleNamespace(
        contract=SimpleNamespace(conId=555),
        order=SimpleNamespace(orderId=53, orderRef="trendbot:NQ:stop",
                              action="SELL", orderType="STP", account="DTEST",
                              totalQuantity=3, auxPrice=31495.5),
        orderStatus=SimpleNamespace(status="PreSubmitted", filled=0))
    tb.ex.open_orders = [dict(ref="trendbot:NQ:stop", trade=trade)]
    _attach_durable_db(tb)
    for key in ("provenance", "broker", "data", "accounting", "protection",
                "reconciliation"):
        tb.db.set_readiness(key, "OK")
    return tb


def test_closed_es_with_other_protected_position_recovers(halted):
    assert halted._recover_protection_halt()
    saved = json.loads((halted.st.dir / "state.json").read_text())
    assert saved["halt_entries"] is False
    assert saved["halt_reason"] is None
    assert halted.ex.calls == []  # Recovery itself changes no orders.
    assert not halted._recover_protection_halt()  # Idempotent.


def test_protected_position_can_recover_without_closing(halted):
    halted.st.state["halt_reason"] = "NQ position unprotected"
    assert halted._recover_protection_halt()
    assert halted.st.pos("NQ")["qty"] == 3


def test_flat_account_can_recover(halted):
    halted.st.pos("NQ").update(position=0, qty=0)
    halted.ex.positions_map = {}
    halted.ex.open_orders = []
    assert halted._recover_protection_halt()


@pytest.mark.parametrize("field,value", [
    ("action", "BUY"), ("orderType", "LMT"), ("account", "OTHER"),
    ("orderId", 99), ("totalQuantity", 2), ("totalQuantity", 4),
    ("auxPrice", 31400.0), ("auxPrice", float("nan")),
])
def test_stop_identity_must_match(halted, field, value):
    setattr(halted.ex.open_orders[0]["trade"].order, field, value)
    assert not halted._recover_protection_halt()
    assert halted.st.state["halt_entries"]


@pytest.mark.parametrize("case", [
    "missing", "duplicate", "foreign", "orphan", "entry", "wrong_contract",
    "pending_cancel", "unbooked_partial", "cache_missing", "position_mismatch",
    "unmanaged_position", "duplicate_position", "unknown_schema",
])
def test_broker_ambiguity_keeps_halt(halted, case):
    orders = halted.ex.open_orders
    if case == "missing":
        orders.clear()
    elif case == "duplicate":
        orders.append(dict(orders[0]))
    elif case in ("foreign", "orphan", "entry"):
        orders[0]["ref"] = {"foreign": "manual", "orphan": "trendbot:ES_S:stop",
                            "entry": "trendbot:NQ:entry"}[case]
    elif case == "wrong_contract":
        orders[0]["trade"].contract.conId = 777
    elif case == "pending_cancel":
        orders[0]["trade"].orderStatus.status = "PendingCancel"
    elif case == "unbooked_partial":
        orders[0]["trade"].orderStatus.filled = 1
    elif case == "cache_missing":
        halted.ex.stop_working = False
    elif case == "position_mismatch":
        halted.ex.positions_map = {555: 2}
    elif case == "unmanaged_position":
        halted.ex.positions_map[999] = -1
    elif case == "duplicate_position":
        halted.st.pos("ES_S").update(position=1, qty=3, con_id=555)
    else:
        orders[0].pop("trade")
    assert not halted._recover_protection_halt()
    assert halted.st.state["halt_entries"]


@pytest.mark.parametrize("reason", [
    "manual halt", "KILL_SWITCH", "CODE_DRIFT: dirty tree",
    "ES_S entry state unresolved", "unknown position unprotected",
])
def test_other_halts_are_never_cleared(halted, reason):
    halted.st.state["halt_reason"] = reason
    assert not halted._recover_protection_halt()
    assert halted.st.state["halt_reason"] == reason


@pytest.mark.parametrize("guard", [
    "degraded", "startup", "reconcile", "kill", "intents", "kill_unresolved",
    "duplicate_stops", "disconnected", "no_db", "pending_journal", "unapplied",
    "broker_exception", "journal_exception", "callback_halt",
])
def test_operational_guards_keep_halt(halted, guard):
    def fail():
        raise RuntimeError("injected API failure")
    if guard == "degraded":
        halted._degraded.add("journal")
    elif guard == "startup":
        halted._startup_done = False
    elif guard == "reconcile":
        halted._reconcile_ok = False
    elif guard == "kill":
        (halted.st.dir / "KILL_SWITCH").touch()
    elif guard in ("intents", "kill_unresolved"):
        halted.st.state["order_intents" if guard == "intents" else guard] = {"ES_S": {}}
    elif guard == "duplicate_stops":
        halted.ex.unresolved_duplicate_stops = {"NQ": [54]}
    elif guard == "disconnected":
        halted.ex.is_connected = lambda: False
    elif guard == "no_db":
        halted.db = None
    elif guard == "pending_journal":
        halted.db.pending_events = lambda: [{"event_type": "ENTRY"}]
    elif guard == "unapplied":
        halted.db.unapplied_execution_count = lambda: 1
    elif guard == "broker_exception":
        halted.ex.open_account_orders = fail
    elif guard == "journal_exception":
        halted.db.pending_events = fail
    elif guard == "callback_halt":
        def callback():
            halted.st.state["halt_reason"] = "manual halt"
            return halted.ex.open_orders
        halted.ex.open_account_orders = callback
    assert not halted._recover_protection_halt()
    assert halted.st.state["halt_entries"]


@pytest.mark.parametrize("component", [
    "provenance", "broker", "data", "accounting", "protection", "reconciliation",
])
def test_failed_readiness_keeps_halt(halted, component):
    halted.db.set_readiness(component, "FAILED")
    assert not halted._recover_protection_halt()


def test_queued_unsubmitted_signal_does_not_deadlock_recovery(halted):
    halted.st.state["pending_entries"] = {"ES_S": {"created": "signal only"}}
    assert halted._recover_protection_halt()


@pytest.mark.parametrize("accounting_failure", [False, True])
def test_full_wake_recovers_only_after_accounting_succeeds(halted, monkeypatch,
                                                         accounting_failure):
    # Exercise production wake wiring, while isolating strategy/data fetching.
    halted.data = SimpleNamespace(connect=lambda: None, vix_snapshot=lambda: 20)
    halted.ex.connect = lambda: False
    for method in ("_check_code_drift", "maybe_rebalance", "process_instrument",
                   "process_satellite", "reconcile_positions", "_risk_alerts",
                   "mark_equity"):
        monkeypatch.setattr(halted, method, lambda *a, **kw: None)
    import oi_state
    monkeypatch.setattr(oi_state, "get_states", lambda: {})
    if accounting_failure:
        def fail(*a):
            raise RuntimeError("injected snapshot failure")
        monkeypatch.setattr(halted.db, "snapshot_positions", fail)
    halted.wake()
    assert halted.st.state["halt_entries"] is accounting_failure
