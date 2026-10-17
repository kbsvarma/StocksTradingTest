"""Regression tests for every bug that has actually shipped.

Each test name maps to a real incident. If any of these fail in CI, you are
about to re-ship a bug we've already paid for.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from webull_bot.execution import (
    ExecutionEngine, FillResult, round_down_to_tick,
)
from webull_bot.tests.conftest import FakeOrder


# ──────────────────────────────────────────────────────────────────────────────
# BUG #1: orphan-positions RCA (2026-05-20)
#   Polling timeout was treated as "didn't fill". Bot returned not_filled,
#   released day-lock, but order had actually filled at broker. 3 positions
#   ran for hours with no SL monitor.
# Fix: post-order broker reconciliation in place_spread.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_OrphanPositionPostTimeout:
    def test_timeout_with_broker_filled_returns_filled(self, engine, mock_tc):
        """If LIMIT 'didn't fill' per polling but broker holdings show the
        spread, place_spread must return filled=True (not TIMEOUT) so caller
        records state and starts the SL monitor.
        """
        # Don't fill via polling
        mock_tc.poll_calls_before_fill = 0
        # But pre-populate post-fill holdings to simulate broker fill
        # We need to mutate holdings AFTER the snapshot_pre call.
        # Trick: monkeypatch _snapshot_spxw_holdings to return {} the first
        # time, then the filled holdings on the second call.
        snap_calls = {"n": 0}
        from_iid_short = "iid-S-orphan"
        from_iid_long = "iid-L-orphan"

        def _fake_snapshot():
            snap_calls["n"] += 1
            if snap_calls["n"] == 1:
                return {}
            return {
                from_iid_short: {"instrument_id": from_iid_short, "qty": -1,
                                 "cost_price": "1.50"},
                from_iid_long: {"instrument_id": from_iid_long, "qty": 1,
                                "cost_price": "0.55"},
            }

        engine._snapshot_spxw_holdings = _fake_snapshot
        mock_tc.today_strike_map = {
            from_iid_short: 5800.0,
            from_iid_long: 5750.0,
        }

        r = engine.place_spread(
            symbol="SPXW", expiry="2026-05-20",
            short_strike=5800, long_strike=5750,
            quantity=1, limit_price=1.50,
            retry_wait_seconds=60, walk_down_interval_s=15,
        )
        assert r.filled is True, \
            "REGRESSION: orphan position not detected by reconciliation"
        # Reconciled credit = 1.50 - 0.55 = 0.95
        assert r.fill_price == pytest.approx(0.95)
        assert "RECONCILED" in r.detail


# ──────────────────────────────────────────────────────────────────────────────
# BUG #2: HTTP 417 cancel → MARKET fallback → doubled position (2026-05-20)
#   force_place_7355_7305.py ran twice. Cancel failed 417 (order had filled).
#   Old _cancel_with_safety didn't exist; cancel-fail treated as "still open"
#   → MARKET fallback fired on top → 2 positions instead of 1.
# Fix: _cancel_with_safety rechecks status, MARKET fallback hard-disabled.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_Http417CancelDoublePosition:
    def test_417_cancel_with_filled_order_returns_filled_no_market(
        self, engine, mock_tc,
    ):
        cid = "doomed"
        mock_tc.orders[cid] = FakeOrder(
            client_order_id=cid, limit_price=1.50, quantity=1,
            short_strike=5800, long_strike=5750,
            status="FILLED", fill_price=1.50,
            cancel_should_fail_with=417)
        result = engine._cancel_with_safety(cid)
        assert result["filled"] is True, \
            "REGRESSION: 417 + filled state must be caught — not treated as still-open"
        assert result["fill_price"] == 1.50
        # No second order placement should ever happen — _cancel_with_safety is pure
        assert len(mock_tc.orders) == 1


# ──────────────────────────────────────────────────────────────────────────────
# BUG #3: yfinance-only find_best_spread in live mode (visible weeks; caught 5/20)
#   market_data.find_best_spread used yfinance (15min stale) even though
#   IBKR was up. Limit prices were off-market → fills failed first try.
# Fix: IBKR-first in find_best_spread (mirrors find_top_spreads).
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_FindBestSpreadIBKRFirst:
    def test_find_best_spread_tries_ibkr_before_yfinance(self, monkeypatch):
        called = {"ibkr": False, "yf": False}

        def _fake_ibkr(**kw):
            called["ibkr"] = True
            return [{"short_strike": 5800.0, "long_strike": 5750.0,
                     "expiry": "2026-05-20",
                     "mid": 1.50, "bid": 1.45, "ask": 1.55}]

        import webull_bot.market_data as md
        monkeypatch.setattr("webull_bot.ibkr_market_data.get_top_spreads_ibkr",
                            _fake_ibkr)
        # Sabotage yfinance so we know it wasn't called
        monkeypatch.setattr(md, "yf",
                            type("X", (), {"Ticker": lambda *a: (_ for _ in ()).throw(AssertionError("yfinance must not be called when IBKR is up"))})())

        result = md.find_best_spread(
            spx_price=5850.0, otm_pct=0.01, spread_width=50,
            min_credit=1.20, yf_options_symbol="^SPX",
            expiry="2026-05-20",
        )
        assert called["ibkr"] is True
        assert result is not None
        assert md.last_chain_source() == "IBKR"

    def test_find_best_spread_ibkr_empty_does_not_fallback(self, monkeypatch):
        """If IBKR responds (no exception) but returns empty, we trust it
        and return None — do NOT silently fall back to stale yfinance."""
        import webull_bot.market_data as md
        monkeypatch.setattr("webull_bot.ibkr_market_data.get_top_spreads_ibkr",
                            lambda **kw: [])
        # Sabotage yfinance
        monkeypatch.setattr(md, "yf",
                            type("X", (), {"Ticker": lambda *a: (_ for _ in ()).throw(AssertionError("yfinance must not be called"))})())
        result = md.find_best_spread(
            spx_price=5850, otm_pct=0.01, spread_width=50,
            min_credit=1.20, yf_options_symbol="^SPX",
            expiry="2026-05-20",
        )
        assert result is None
        assert md.last_chain_source() == "IBKR"


# ──────────────────────────────────────────────────────────────────────────────
# BUG #4: tick-size unaligned LIMIT (2026-05-12 silent fail; tick-size invariant)
#   Webull rejects $0.05-unaligned limits with FIX_UN_DFD_CANCEL. Bot lost
#   trades for 2 days before discovery.
# Fix: round_down_to_tick on every limit in _build_order_dict.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_TickSizeAlignment:
    def test_build_order_dict_always_tick_aligned(self):
        from webull_bot.execution import ExecutionEngine
        for raw_limit in [1.93, 0.43, 2.07, 0.81, 1.37]:
            order = ExecutionEngine._build_order_dict(
                symbol="SPXW", expiry="2026-05-20",
                short_strike=5800, long_strike=5750,
                quantity=1, limit_price=raw_limit,
            )
            limit_str = order["limit_price"]
            # Should always end in .X0 or .X5
            cents = round(float(limit_str) * 100) % 5
            assert cents == 0, f"REGRESSION: ${limit_str} not $0.05-aligned"

    def test_walk_down_replace_orders_also_tick_aligned(self, engine, mock_tc):
        """Walk-down generates new limits; they must remain tick-aligned."""
        mock_tc.fill_on_walk_step = 3  # force all walks
        r = engine.place_spread(
            symbol="SPXW", expiry="2026-05-20",
            short_strike=5800, long_strike=5750,
            quantity=1, limit_price=1.50,
            retry_wait_seconds=60, walk_down_interval_s=15,
            walk_down_step=0.05, walk_down_max_steps=3,
        )
        # final price after 3 walks from 1.50 = 1.35
        cid = r.client_order_id
        final = mock_tc.orders[cid].limit_price
        cents = round(final * 100) % 5
        assert cents == 0


# ──────────────────────────────────────────────────────────────────────────────
# BUG #5: combo MARKET 50-77% slippage (2026-05-20 — $0.45 vs $1.93 expected)
#   entry_market_fallback=True caused MARKET on a combo. Webull executes
#   leg-by-leg at NBBO → massive slippage.
# Fix: hard-disable MARKET fallback in code; ignore config.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_ComboMarketSlippage:
    def test_market_fallback_param_is_ignored(self, engine, mock_tc):
        """Even when caller explicitly enables, MARKET must NOT fire."""
        r = engine.place_spread(
            symbol="SPXW", expiry="2026-05-20",
            short_strike=5800, long_strike=5750,
            quantity=1, limit_price=1.50,
            retry_wait_seconds=60,
            entry_market_fallback=True,
        )
        # No fill in mock = TIMEOUT, not MARKET success
        assert r.status == "TIMEOUT"
        assert "MARKET unsafe" in r.detail or "DISABLED" in r.detail


# ──────────────────────────────────────────────────────────────────────────────
# BUG #6: force-entry path left "Run bot normally to monitor" (2026-05-20)
#   force_entry_now() saved state but never started the monitor — operator
#   had to manually restart bot. 3 positions had no SL coverage today.
# Fix: PositionMonitor.run_until_closed() invoked inline.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_ForceEntryStartsMonitorInline:
    def test_force_entry_code_path_calls_monitor(self):
        """Static check: the force-entry function MUST call run_until_closed."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.force_entry_now)
        assert "run_until_closed" in src, \
            "REGRESSION: force-entry no longer starts SL monitor inline"
        assert "PositionMonitor" in src


# ──────────────────────────────────────────────────────────────────────────────
# BUG #7: SL MONITOR ACTIVE alert (2026-05-20 — would have caught orphans)
#   No positive confirmation that the monitor was actually running.
# Fix: send_alert("🟢 SL MONITOR ACTIVE...") on monitor entry.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_SLMonitorActiveAlert:
    def test_monitor_emits_active_alert_on_start(self, monkeypatch, _disable_alerts):
        """First thing run_until_closed does should be alert the user."""
        import inspect
        from webull_bot import monitor as mon
        src = inspect.getsource(mon.PositionMonitor.run_until_closed)
        assert "SL MONITOR ACTIVE" in src, \
            "REGRESSION: monitor no longer emits SL-active confirmation"


# ──────────────────────────────────────────────────────────────────────────────
# BUG #8: Telegram non-blocking invariant (long-standing)
#   Alert failure must NEVER block trading. send_alert raising must not
#   propagate from monitor / execution code paths.
# Fix: every send_alert call wrapped in try/except.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_TelegramNonBlocking:
    def test_monitor_runs_when_telegram_raises(self):
        """The SL MONITOR ACTIVE alert must be wrapped so a failing send_alert
        cannot crash the monitor or block trading.
        """
        import inspect
        from webull_bot import monitor as mon
        src = inspect.getsource(mon.PositionMonitor.run_until_closed)
        assert "SL MONITOR ACTIVE" in src
        # The alert text appears inside an f-string; find the send_alert call
        # that contains it, then walk backwards to confirm it's in a try block.
        idx = src.find("send_alert(")
        assert idx != -1, "send_alert call missing"
        # Look back from the send_alert call (any whitespace) for the nearest
        # try: keyword.
        preceding = src[:idx]
        last_try = preceding.rfind("try:")
        last_except = preceding.rfind("except ")
        assert last_try > last_except, \
            "SL MONITOR ACTIVE send_alert not protected by try/except"


# ──────────────────────────────────────────────────────────────────────────────
# BUG #9: stale data warning visibility (T1.6 design)
#   When IBKR is down and yfinance falls back, the bot must announce it
#   loudly so the operator knows fill quality will suffer.
# Fix: T1.6 stale-data warning in main.py.
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_StaleDataObservability:
    def test_main_warns_on_yfinance_chain_source(self):
        import inspect
        from webull_bot import main
        src = inspect.getsource(main)
        assert "STALE DATA WARNING" in src, \
            "REGRESSION: T1.6 stale-data warning removed from main.py"
        assert "last_chain_source" in src

    def test_stale_data_gate_is_opt_in(self):
        """The hard-block must be opt-in via require_realtime_chain config —
        default behavior must not change (no filter tightening)."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main)
        # Find the require_realtime_chain reference and confirm default=False
        assert 'require_realtime_chain", False' in src, \
            "REGRESSION: stale-data hard-block is no longer opt-in (default must be False)"

    def test_stale_data_alert_suppressed_on_paper_instance(self):
        """EC2 paper bot has no IBKR Gateway → falls back to yfinance every
        entry. Telegram alert must be suppressed there or the user gets
        spammed daily for a non-issue (paper fill quality is irrelevant)."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main)
        assert "_is_paper_instance" in src, \
            "REGRESSION: paper-instance check removed from stale-data path"
        assert 'WEBULL_INSTANCE_NAME' in src, \
            "REGRESSION: instance detection removed"
        # The actual gating: alert is inside `if not _is_paper_instance:`
        assert "if not _is_paper_instance" in src, \
            "REGRESSION: stale-data alert no longer guarded by paper check"


# ──────────────────────────────────────────────────────────────────────────────
# Filter-bloat regression — ensures no new entry conditions snuck in
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# BUG #10: User-stated invariants (2026-05-20 night)
#   "check you place only one trade and dont place a duplicate order
#    check again every option open by you should have a SL monitor"
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_UserInvariants_2026_05_20_Night:
    def test_one_trade_per_day_gate_present(self):
        """trade_once_per_day must gate the entry loop."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        assert "trade_taken_today" in src
        assert "trade_once_per_day" in src
        # The check must sleep+continue (not just log) when already taken
        assert "sleeping until next day" in src

    def test_trade_taken_locked_before_place_order(self):
        """Lock state BEFORE place_spread so a crash mid-fill can't double-place."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        idx_lock = src.find("state.trade_taken_today = True")
        idx_place = src.find("execution.place_spread(")
        assert 0 < idx_lock < idx_place, \
            "REGRESSION: day lock must be set BEFORE place_spread (crash safety)"

    def test_has_live_position_or_order_runs_unconditionally(self):
        """The hard fence against duplicates must NOT be bypassable."""
        import inspect
        from webull_bot import execution
        src = inspect.getsource(execution.ExecutionEngine.place_spread)
        # The check must run AFTER dry-run gate but BEFORE any order placement
        idx_check = src.find("has_live_position_or_order()")
        idx_place = src.find("self.trade.order_v3.place_order(")
        assert 0 < idx_check < idx_place, \
            "REGRESSION: has_live_position_or_order check must precede place_order"

    def test_blocked_state_prevents_order(self, engine, mock_tc):
        """Live test: when broker has existing SPXW position, no order fires."""
        mock_tc.holdings = [{"instrument_id": "iid", "symbol": "SPXW",
                             "instrument_type": "OPTION", "qty": -1}]
        r = engine.place_spread(
            symbol="SPXW", expiry="2026-05-20",
            short_strike=5800, long_strike=5750,
            quantity=1, limit_price=1.0,
        )
        assert r.status == "BLOCKED"
        # And no orders were placed at all
        assert len(mock_tc.orders) == 0

    def test_main_path_filled_immediately_starts_monitor(self):
        """Order fill → record state → start monitor. No gap."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        # Find the order of: state.open_position = pos, store.save, monitor.run_until_closed
        idx_pos = src.find("state.open_position = pos")
        idx_save = src.find("store.save(state)", idx_pos)
        idx_monitor = src.find("monitor.run_until_closed(state)", idx_save)
        assert 0 < idx_pos < idx_save < idx_monitor, \
            "REGRESSION: order of fill→save→monitor broken"

    def test_force_entry_path_starts_monitor(self):
        """Force-entry MUST start monitor inline (no 'restart bot to monitor')."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.force_entry_now)
        assert "PositionMonitor" in src
        assert "run_until_closed" in src
        # Must NOT actually PRINT the legacy "Run bot normally to monitor"
        # message (it's allowed to appear in a comment as historical context).
        assert 'print(f"[FORCE-ENTRY] Position saved. Run bot normally' not in src
        assert 'print("[FORCE-ENTRY] Position saved. Run bot normally' not in src

    def test_resume_path_starts_monitor(self):
        """On bot restart with state.open_position set, monitor must auto-resume."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        assert "resuming monitoring of existing open position" in src
        # And it actually calls the monitor
        idx_resume = src.find("resuming monitoring")
        assert "monitor.run_until_closed" in src[idx_resume:idx_resume+500]

    def test_main_monitor_call_wrapped_in_try_except(self):
        """If monitor.run_until_closed crashes, SL is dead. Must alert loudly,
        not silently exit."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        idx_monitor = src.find("monitor.run_until_closed(state)")
        # walk back to find the wrapping try
        preceding = src[:idx_monitor]
        last_try = preceding.rfind("try:")
        last_except = preceding.rfind("except ")
        assert last_try > last_except, \
            "REGRESSION: main-path monitor call not protected by try/except"

    def test_resume_monitor_call_wrapped_in_try_except(self):
        """Resume path also needs crash protection."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        # find the FIRST run_until_closed call (the resume one)
        idx_first = src.find("monitor.run_until_closed(state)")
        preceding = src[:idx_first]
        last_try = preceding.rfind("try:")
        last_except = preceding.rfind("except ")
        assert last_try > last_except, \
            "REGRESSION: resume-path monitor call not protected by try/except"


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2.3 paper-validation config loading (2026-05-21)
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_ConfigPathEnvVar:
    def test_default_config_path_unchanged(self, monkeypatch, tmp_path):
        """When WEBULL_CONFIG_PATH is NOT set, Mac live MUST load
        webull_bot/config.yaml (default behavior preserved)."""
        monkeypatch.delenv("WEBULL_CONFIG_PATH", raising=False)
        from webull_bot import main
        import inspect
        src = inspect.getsource(main.load_config)
        assert 'webull_bot/config.yaml' in src, \
            "REGRESSION: default config path changed — Mac live could load wrong file"

    def test_env_var_overrides_default(self, monkeypatch, tmp_path):
        """When WEBULL_CONFIG_PATH is set, load_config must use that path."""
        custom = tmp_path / "custom.yaml"
        custom.write_text("quantity: 5\nsymbol: SPXW\n")
        monkeypatch.setenv("WEBULL_CONFIG_PATH", str(custom))
        from webull_bot.main import load_config
        cfg = load_config()
        assert cfg["quantity"] == 5
        assert cfg["symbol"] == "SPXW"

    def test_explicit_path_beats_env_var(self, monkeypatch, tmp_path):
        """Explicit path argument always wins over env var."""
        env_file = tmp_path / "env.yaml"
        env_file.write_text("quantity: 99\n")
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text("quantity: 7\n")
        monkeypatch.setenv("WEBULL_CONFIG_PATH", str(env_file))
        from webull_bot.main import load_config
        cfg = load_config(str(explicit))
        assert cfg["quantity"] == 7

    def test_phase23_paper_config_file_exists_and_valid(self):
        """The Phase 2.3 paper-validation config file must exist with the
        expected critical parameters."""
        from pathlib import Path
        import yaml
        cfg_path = Path(__file__).parent.parent / "config_phase23_paper.yaml"
        assert cfg_path.exists(), "config_phase23_paper.yaml is missing"
        cfg = yaml.safe_load(cfg_path.read_text())
        # The critical Phase 2.3 parameters
        assert cfg["quantity"] == 5, "Phase 2.3 must be 5 contracts"
        assert cfg["spread_width"] == 10, "Phase 2.3 must be 10pt wide"
        assert cfg["otm_pct"] == 0.0075, "Phase 2.3 must be 0.75% OTM"
        # Critical: paper paths must be ISOLATED from live data
        assert "webull_paper" in cfg["data_dir"], \
            "REGRESSION: paper config could overwrite live data files"
        assert "webull_paper" in cfg["state_file"], \
            "REGRESSION: paper config could overwrite live state.json"
        assert "webull_paper" in cfg["trade_csv"], \
            "REGRESSION: paper config could overwrite live trades.csv"


# ──────────────────────────────────────────────────────────────────────────────
# Shadow log Phase 2.3 — never raises, never touches live data (2026-05-21)
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression_ShadowLog:
    def test_shadow_log_entry_never_raises_on_ibkr_failure(self, monkeypatch):
        """Even if find_best_spread blows up, shadow_log.log_entry must not
        propagate the exception. The live bot's trade flow MUST continue."""
        import webull_bot.market_data as md
        def _boom(*a, **kw):
            raise RuntimeError("IBKR down")
        monkeypatch.setattr(md, "find_best_spread", _boom, raising=False)
        from webull_bot import shadow_log
        # Should NOT raise:
        shadow_log.log_entry(
            spx_at_entry=7400, vix_at_entry=18.0, expiry="2026-05-21",
            yf_options_symbol="^SPX",
            live_short=7320, live_long=7270, live_credit=1.50,
        )
        # If we got here, the function didn't propagate — test passes

    def test_shadow_log_exit_never_raises_on_no_open(self, monkeypatch, tmp_path):
        """If exit is called without a prior entry (e.g. resume after crash),
        must not raise."""
        from webull_bot import shadow_log
        monkeypatch.setattr(shadow_log, "SHADOW_DATA_DIR", str(tmp_path))
        # No open.json exists — exit should handle gracefully
        shadow_log.log_exit(live_exit_reason="EXPIRED", live_pnl_usd=150.0)

    def test_shadow_log_full_lifecycle_writes_csv(self, monkeypatch, tmp_path):
        """Entry → exit produces a single completed row in trades.csv."""
        import webull_bot.market_data as md
        from webull_bot import shadow_log
        monkeypatch.setattr(shadow_log, "SHADOW_DATA_DIR", str(tmp_path))

        class FakeSpread:
            short_strike = 7375.0
            long_strike = 7365.0
            mid = 1.30
            bid = 1.25
            ask = 1.35
            expiry = "2026-05-21"
        monkeypatch.setattr(md, "find_best_spread",
                            lambda **kw: FakeSpread(), raising=False)
        import webull_bot.ibkr_market_data as ibkr_md
        monkeypatch.setattr(ibkr_md, "get_spread_mark_ibkr",
                            lambda **kw: 0.10, raising=False)

        # Lifecycle
        shadow_log.log_entry(
            spx_at_entry=7432.97, vix_at_entry=17.5, expiry="2026-05-21",
            yf_options_symbol="^SPX",
            live_short=7320, live_long=7270, live_credit=1.50,
        )
        shadow_log.log_exit(live_exit_reason="EXPIRED", live_pnl_usd=150.0)

        # Verify CSV
        import csv
        csv_path = tmp_path / "trades.csv"
        assert csv_path.exists()
        rows = list(csv.DictReader(open(csv_path)))
        assert len(rows) == 1
        r = rows[0]
        assert int(float(r["shadow_short"])) == 7375
        assert int(float(r["shadow_long"])) == 7365
        # Shadow limit = mid (1.30) - offset (0.10) = 1.20
        assert abs(float(r["shadow_limit_at_entry"]) - 1.20) < 0.01
        # Shadow credit total = 1.20 × 5 × 100 = 600
        assert abs(float(r["shadow_credit_total"]) - 600) < 0.01
        # Exit at 0.10, qty 5 → pnl = (1.20 - 0.10) × 5 × 100 = 550
        assert abs(float(r["shadow_pnl_usd"]) - 550) < 0.01
        assert r["shadow_data_quality"] == "accurate"
        # open.json should be cleared
        assert not (tmp_path / "open.json").exists()

    def test_shadow_log_main_integration_present(self):
        """The two integration points in main.py must call shadow_log."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        # Entry hook
        assert "shadow_log.log_entry" in src, \
            "REGRESSION: shadow_log.log_entry call removed from entry path"
        # Exit hook
        assert "shadow_log.log_exit" in src, \
            "REGRESSION: shadow_log.log_exit call removed from exit path"
        # Both must be in try/except wrappers
        idx_entry = src.find("shadow_log.log_entry")
        idx_exit = src.find("shadow_log.log_exit")
        for label, idx in [("entry", idx_entry), ("exit", idx_exit)]:
            preceding = src[:idx]
            last_try = preceding.rfind("try:")
            last_except = preceding.rfind("except ")
            assert last_try > last_except, \
                f"REGRESSION: shadow_log.{label} call not wrapped in try/except"


class TestRegression_PreWindowObserve:
    def test_pre_window_observe_does_NOT_call_place_spread(self):
        """Observability must never trigger a trade. The pre-window code path
        must not contain place_spread or any execution.place_* call."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        # Find the pre-window observe block
        idx = src.find("PRE-WINDOW OBSERVABILITY")
        assert idx != -1, "REGRESSION: pre-window observe block missing"
        # Look at the NEXT 2000 chars (the block scope)
        block = src[idx:idx + 2500]
        # CRITICAL: no order placement / no trade actions in this block
        assert "place_spread" not in block, \
            "REGRESSION: pre-window observe contains place_spread — must be read-only"
        assert "place_order" not in block, \
            "REGRESSION: pre-window observe contains place_order — must be read-only"
        assert "execution." not in block, \
            "REGRESSION: pre-window observe touches execution module — must be read-only"
        # Must be wrapped in try/except — observability must never crash bot
        assert "try:" in block and "except" in block, \
            "REGRESSION: pre-window observe not wrapped in try/except"

    def test_pre_window_observe_gated_by_config_flag(self):
        """Default behavior preserved — only fires when cfg.pre_window_observe=true."""
        import inspect
        from webull_bot import main
        src = inspect.getsource(main.run)
        assert 'cfg.get("pre_window_observe", False)' in src, \
            "REGRESSION: pre-window observe must be opt-in (default False)"


class TestRegression_NoNewEntryFilters:
    def test_no_new_blocking_gates_in_entry_path(self):
        """The known entry gates should be: VIX range, VIX daily-rise,
        direction filter, entry time window, trade_once_per_day,
        has_live_position_or_order. Anything else is filter creep.
        """
        import inspect
        from webull_bot import main
        src = inspect.getsource(main)
        # Sanity: known gates still present in main.py code (some live in
        # execution.py — checked via that module separately).
        known_main_gates = ["vix_max", "vix_min", "direction_filter_enabled",
                            "trade_once_per_day"]
        for g in known_main_gates:
            assert g in src, f"known main.py gate missing: {g}"
        # has_live_position_or_order lives in execution.py
        from webull_bot import execution
        exsrc = inspect.getsource(execution)
        assert "has_live_position_or_order" in exsrc
        # T1.6 stale-data gate must remain opt-in (default off)
        assert 'require_realtime_chain", False' in src
