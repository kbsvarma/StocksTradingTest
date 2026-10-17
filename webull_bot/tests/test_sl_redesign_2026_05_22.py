"""Tests for the 2026-05-22 SL redesign.

Covers:
  - arm_sl_from_broker_combo (the single helper used everywhere to arm SL)
  - find_new_spxw_combo (fill-detection diff logic)
  - PositionSnapshotter.snapshot_combos (parses account_v2 response)
  - has_live_position_or_order pre-place check (state + broker)
  - Watchdog clock-gating (_should_run_now)
  - Event queue read/write atomicity
  - safe_api_call 429 backoff

These tests target the NEW code only. The old test_position_based_fill.py tests
the legacy strike_resolver path which is now intentionally broken (removed).
"""
from __future__ import annotations

import json
import os
import time
import tempfile
from datetime import datetime, time as dtime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webull_bot.execution_v2 import (
    arm_sl_from_broker_combo,
    find_new_spxw_combo,
    PositionSnapshotter,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: realistic account_v2 combo response shapes
# ─────────────────────────────────────────────────────────────────────────────

def make_spxw_combo(short_strike=7420, long_strike=7370, qty=1, cost=2.00, last=1.30,
                    position_id="POS123", expiry="2026-05-22"):
    """Construct a realistic SPXW vertical put combo as returned by account_v2."""
    return {
        "currency": "USD",
        "quantity": str(qty),
        "cost": str(-cost * 100 * qty),  # negative for credit
        "position_id": position_id,
        "symbol": "SPXW",
        "option_strategy": "VERTICAL",
        "instrument_type": "OPTION",
        "cost_price": str(cost),
        "last_price": str(last),
        "market_value": str(-last * 100 * qty),
        "unrealized_profit_loss": str((cost - last) * 100 * qty),
        "legs": [
            {
                "symbol": "SPXW",
                "instrument_type": "OPTION",
                "option_type": "PUT",
                "option_expire_date": expiry,
                "option_exercise_price": f"{long_strike:.2f}",
                "leg_id": position_id,
                "cost": "0.50",
                "last_price": "0.20",
            },
            {
                "symbol": "SPXW",
                "instrument_type": "OPTION",
                "option_type": "PUT",
                "option_expire_date": expiry,
                "option_exercise_price": f"{short_strike:.2f}",
                "leg_id": position_id,
                "cost": "2.50",
                "last_price": "1.50",
            },
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# arm_sl_from_broker_combo
# ─────────────────────────────────────────────────────────────────────────────

class TestArmSLFromBrokerCombo:
    def test_basic_spxw_vertical(self):
        combo = make_spxw_combo(short_strike=7420, long_strike=7370, cost=2.00)
        op = arm_sl_from_broker_combo(combo, stop_multiplier=2.0)
        assert op["short_strike"] == 7420.0
        assert op["long_strike"] == 7370.0
        assert op["entry_credit"] == 2.00
        assert op["stop_price"] == 4.00
        assert op["quantity"] == 1
        assert op["symbol"] == "SPXW"
        assert op["expiry"] == "2026-05-22"

    def test_stop_multiplier_3x(self):
        combo = make_spxw_combo(cost=1.50)
        op = arm_sl_from_broker_combo(combo, stop_multiplier=3.0)
        assert op["stop_price"] == 4.50  # 1.50 × 3.0

    def test_iids_use_combo_position_id(self):
        """account_v2 doesn't expose per-leg iids; we use combo position_id."""
        combo = make_spxw_combo(position_id="ABCDEFGHIJ")
        op = arm_sl_from_broker_combo(combo)
        assert op["short_iid"] == "ABCDEFGHIJ"
        assert op["long_iid"] == "ABCDEFGHIJ"

    def test_short_higher_strike_long_lower_strike(self):
        """For bull put spread: short = higher strike, long = lower."""
        # Reversed leg order in combo — function must sort by strike
        combo = make_spxw_combo(short_strike=7420, long_strike=7370)
        op = arm_sl_from_broker_combo(combo)
        assert op["short_strike"] > op["long_strike"]
        assert op["short_strike"] == 7420
        assert op["long_strike"] == 7370

    def test_raises_on_wrong_leg_count(self):
        combo = make_spxw_combo()
        combo["legs"] = combo["legs"][:1]  # only 1 leg
        with pytest.raises(ValueError, match="2-leg combo"):
            arm_sl_from_broker_combo(combo)

    def test_raises_on_missing_strike(self):
        combo = make_spxw_combo()
        del combo["legs"][0]["option_exercise_price"]
        with pytest.raises(ValueError, match="missing strike"):
            arm_sl_from_broker_combo(combo)

    def test_negative_cost_handled(self):
        """account_v2 may report cost_price as negative for credit spreads.
        We use abs() to always get positive credit."""
        combo = make_spxw_combo(cost=2.00)
        combo["cost_price"] = "-2.00"
        op = arm_sl_from_broker_combo(combo)
        assert op["entry_credit"] == 2.00  # absolute value


# ─────────────────────────────────────────────────────────────────────────────
# find_new_spxw_combo
# ─────────────────────────────────────────────────────────────────────────────

class TestFindNewSPXWCombo:
    def test_finds_new_combo(self):
        pre = []
        post = [make_spxw_combo(position_id="NEW123")]
        result = find_new_spxw_combo(pre, post)
        assert result is not None
        assert result["position_id"] == "NEW123"

    def test_ignores_pre_existing_combos(self):
        existing = make_spxw_combo(position_id="OLD")
        pre = [existing]
        post = [existing]  # same combo, not new
        assert find_new_spxw_combo(pre, post) is None

    def test_finds_new_among_existing(self):
        old = make_spxw_combo(position_id="OLD")
        new = make_spxw_combo(position_id="NEW", short_strike=7425)
        pre = [old]
        post = [old, new]
        result = find_new_spxw_combo(pre, post)
        assert result is not None
        assert result["position_id"] == "NEW"

    def test_filters_by_symbol(self):
        # NDXP combo should not match SPXW search
        ndxp_combo = make_spxw_combo(position_id="NDXP1")
        ndxp_combo["symbol"] = "NDXP"
        pre = []
        post = [ndxp_combo]
        assert find_new_spxw_combo(pre, post, expected_symbol="SPXW") is None

    def test_filters_by_expiry(self):
        wrong_day = make_spxw_combo(position_id="W", expiry="2026-05-23")
        pre = []
        post = [wrong_day]
        assert find_new_spxw_combo(pre, post, expected_expiry="2026-05-22") is None

    def test_filters_by_strategy(self):
        single = make_spxw_combo(position_id="S")
        single["option_strategy"] = "SINGLE"
        pre = []
        post = [single]
        assert find_new_spxw_combo(pre, post) is None


# ─────────────────────────────────────────────────────────────────────────────
# PositionSnapshotter.snapshot_combos
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotCombos:
    def _mock_client(self, response_json, status_code=200):
        tc = MagicMock()
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = response_json
        tc.account_v2.get_account_position.return_value = resp
        return tc

    def test_returns_spxw_verticals(self):
        body = [make_spxw_combo(position_id="P1"), make_spxw_combo(position_id="P2", short_strike=7400)]
        tc = self._mock_client(body)
        snap = PositionSnapshotter(tc, "test_acct")
        combos = snap.snapshot_combos()
        assert len(combos) == 2
        assert {c["position_id"] for c in combos} == {"P1", "P2"}

    def test_filters_out_non_allowed_symbols(self):
        spxw = make_spxw_combo()
        msft = {"symbol": "MSFT", "option_strategy": "SINGLE", "instrument_type": "EQUITY"}
        tc = self._mock_client([spxw, msft])
        snap = PositionSnapshotter(tc, "test_acct")
        combos = snap.snapshot_combos()
        assert len(combos) == 1
        assert combos[0]["symbol"] == "SPXW"

    def test_handles_429_with_backoff(self):
        tc = MagicMock()
        responses = [
            MagicMock(status_code=429),
            MagicMock(status_code=429),
            MagicMock(status_code=200, json=lambda: [make_spxw_combo()]),
        ]
        tc.account_v2.get_account_position.side_effect = responses
        snap = PositionSnapshotter(tc, "test_acct")
        with patch("time.sleep"):  # don't actually sleep in tests
            combos = snap.snapshot_combos()
        assert len(combos) == 1
        assert tc.account_v2.get_account_position.call_count == 3

    def test_returns_empty_on_persistent_429(self):
        tc = MagicMock()
        tc.account_v2.get_account_position.return_value = MagicMock(status_code=429)
        snap = PositionSnapshotter(tc, "test_acct")
        with patch("time.sleep"):
            combos = snap.snapshot_combos()
        assert combos == []

    def test_response_as_dict_with_data_key(self):
        """Some responses come as {"data": [...]} instead of bare list."""
        body = {"data": [make_spxw_combo()]}
        tc = self._mock_client(body)
        snap = PositionSnapshotter(tc, "test_acct")
        combos = snap.snapshot_combos()
        assert len(combos) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog clock-gating
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchdogClockGating:
    """Tests use the explicit now_et parameter on _should_run_now()."""

    def _now_et_on(self, hour, minute, weekday=4):
        """Return a datetime in ET at the given clock time + weekday.
        Uses May 18-24 2026 (Mon-Sun) so weekday=0 is Mon May 18, weekday=4 is Fri May 22."""
        from webull_bot.reconcile_watchdog_v2 import ET
        # May 18 2026 = Monday → date for any weekday = 18 + weekday
        day = 18 + weekday
        return datetime(2026, 5, day, hour, minute, tzinfo=ET)

    def _setup_last_run(self, now_et, seconds_ago, tmp_path):
        from webull_bot import reconcile_watchdog_v2 as wd
        from datetime import timedelta
        last_run = tmp_path / "last_run.json"
        if seconds_ago is not None:
            last_ts = now_et - timedelta(seconds=seconds_ago)
            last_run.write_text(json.dumps({"ts": last_ts.isoformat()}))
        return last_run

    def test_runs_during_market_hours_when_not_throttled(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(10, 30)
        last = self._setup_last_run(now, 120, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert ok, f"Expected run, got: {reason}"

    def test_throttled_during_market_hours(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(10, 30)
        last = self._setup_last_run(now, 30, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert not ok
        assert "throttled" in reason.lower()

    def test_skip_outside_active_windows(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(17, 0)  # after settlement
        last = self._setup_last_run(now, 10000, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert not ok
        assert "outside" in reason.lower()

    def test_skip_weekends(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(10, 30, weekday=5)  # Saturday
        last = self._setup_last_run(now, 10000, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert not ok
        assert "weekend" in reason.lower()

    def test_pre_market_window(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(8, 30)
        last = self._setup_last_run(now, 400, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert ok, f"Expected pre-market run, got: {reason}"

    def test_settlement_window(self, tmp_path, monkeypatch):
        from webull_bot import reconcile_watchdog_v2 as wd
        now = self._now_et_on(16, 15)
        last = self._setup_last_run(now, 130, tmp_path)
        monkeypatch.setattr(wd, "LAST_RUN_FILE", last)
        ok, reason = wd._should_run_now(now_et=now)
        assert ok, f"Expected settlement run, got: {reason}"


# ─────────────────────────────────────────────────────────────────────────────
# Event queue read/write
# ─────────────────────────────────────────────────────────────────────────────

class TestEventQueue:
    def test_write_then_read(self, tmp_path, monkeypatch):
        from webull_bot import event_queue as eq
        queue_path = tmp_path / "queue.jsonl"
        monkeypatch.setattr(eq, "EVENT_QUEUE", queue_path)

        eid = eq.queue_event("info", "test", "subject1", "body1", "dedup1")
        assert eid

        events = eq.read_events()
        assert len(events) == 1
        assert events[0]["subject"] == "subject1"
        assert events[0]["dedup_key"] == "dedup1"
        assert events[0]["severity"] == "info"

    def test_multiple_writes_preserve_order(self, tmp_path, monkeypatch):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        ids = [eq.queue_event("info", "t", f"sub{i}", "b") for i in range(5)]
        events = eq.read_events()
        assert [e["id"] for e in events] == ids

    def test_invalid_severity_defaults_to_info(self, tmp_path, monkeypatch):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        eq.queue_event("bogus", "t", "s")
        events = eq.read_events()
        assert events[0]["severity"] == "info"

    def test_read_after_id_skips_earlier(self, tmp_path, monkeypatch):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        ids = [eq.queue_event("info", "t", f"sub{i}", "b") for i in range(3)]
        events = eq.read_events(after_id=ids[0])
        assert len(events) == 2
        assert events[0]["id"] == ids[1]

    def test_malformed_line_skipped(self, tmp_path, monkeypatch):
        from webull_bot import event_queue as eq
        queue_path = tmp_path / "queue.jsonl"
        monkeypatch.setattr(eq, "EVENT_QUEUE", queue_path)
        # Manually inject malformed line between valid ones
        eq.queue_event("info", "t", "first")
        with queue_path.open("a") as f:
            f.write("NOT VALID JSON\n")
        eq.queue_event("info", "t", "second")
        events = eq.read_events()
        assert len(events) == 2
        assert events[0]["subject"] == "first"
        assert events[1]["subject"] == "second"


# ─────────────────────────────────────────────────────────────────────────────
# safe_api_call
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeApiCall:
    def test_returns_immediately_on_200(self):
        from webull_bot.safe_api import safe_call
        fn = MagicMock(return_value=MagicMock(status_code=200))
        with patch("time.sleep") as ms:
            r = safe_call(fn)
        assert r.status_code == 200
        assert fn.call_count == 1
        assert ms.call_count == 0

    def test_retries_on_429(self):
        from webull_bot.safe_api import safe_call
        responses = [
            MagicMock(status_code=429),
            MagicMock(status_code=429),
            MagicMock(status_code=200),
        ]
        fn = MagicMock(side_effect=responses)
        with patch("time.sleep"):
            r = safe_call(fn)
        assert r.status_code == 200
        assert fn.call_count == 3

    def test_returns_last_response_after_exhausting_retries(self):
        from webull_bot.safe_api import safe_call
        fn = MagicMock(return_value=MagicMock(status_code=429))
        with patch("time.sleep"):
            r = safe_call(fn, retries=3)
        assert r.status_code == 429
        assert fn.call_count == 3

    def test_raises_on_fail_when_requested(self):
        from webull_bot.safe_api import safe_call
        fn = MagicMock(return_value=MagicMock(status_code=429))
        with patch("time.sleep"):
            with pytest.raises(RuntimeError):
                safe_call(fn, retries=2, raise_on_fail=True)

    def test_non_429_exception_propagates(self):
        from webull_bot.safe_api import safe_call
        fn = MagicMock(side_effect=ValueError("not a rate limit"))
        with pytest.raises(ValueError):
            safe_call(fn)

    def test_429_exception_string_triggers_retry(self):
        from webull_bot.safe_api import safe_call
        responses = [
            Exception("TOO_MANY_REQUESTS something"),
            MagicMock(status_code=200),
        ]
        fn = MagicMock(side_effect=responses)
        with patch("time.sleep"):
            r = safe_call(fn, retries=2)
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Pre-place check (has_live_position_or_order)
# ─────────────────────────────────────────────────────────────────────────────

class TestPrePlaceCheck:
    def _make_v2(self, broker_combos=None, state_dict=None, tmp_path=None):
        """Build an ExecutionEngineV2 with mocked snapshot + state file."""
        from webull_bot.execution_v2 import ExecutionEngineV2
        tc = MagicMock()
        state_path = tmp_path / "state.json"
        if state_dict is not None:
            state_path.write_text(json.dumps(state_dict))
        v2 = ExecutionEngineV2(tc, "test_acct", state_path=str(state_path))
        # Inject combo response
        v2.snapshotter = MagicMock()
        v2.snapshotter.snapshot_combos.return_value = broker_combos or []
        return v2

    def test_allows_when_clean(self, tmp_path):
        v2 = self._make_v2(broker_combos=[], state_dict={"open_position": None}, tmp_path=tmp_path)
        blocked, reason = v2.has_live_position_or_order()
        assert not blocked
        assert reason == ""

    def test_blocks_when_state_has_open_position(self, tmp_path):
        state = {"open_position": {"short_strike": 7420, "long_strike": 7370,
                                    "entry_credit": 2.0, "stop_price": 4.0}}
        v2 = self._make_v2(broker_combos=[], state_dict=state, tmp_path=tmp_path)
        blocked, reason = v2.has_live_position_or_order()
        assert blocked
        assert "7420" in reason and "7370" in reason

    def test_blocks_when_broker_has_active_vertical(self, tmp_path):
        active = make_spxw_combo(last=1.30)  # active (above 0.05 threshold)
        v2 = self._make_v2(broker_combos=[active], state_dict={"open_position": None}, tmp_path=tmp_path)
        blocked, reason = v2.has_live_position_or_order()
        assert blocked
        assert "7420" in reason

    def test_allows_when_broker_only_has_expired_remnant(self, tmp_path):
        expired = make_spxw_combo(last=0.02)  # below $0.05 threshold = filtered
        v2 = self._make_v2(broker_combos=[expired], state_dict={"open_position": None}, tmp_path=tmp_path)
        blocked, reason = v2.has_live_position_or_order()
        assert not blocked

    def test_state_check_runs_before_broker_check(self, tmp_path):
        """If state is populated, broker call shouldn't even matter (faster)."""
        state = {"open_position": {"short_strike": 7420, "long_strike": 7370,
                                    "entry_credit": 2.0, "stop_price": 4.0}}
        v2 = self._make_v2(broker_combos=[], state_dict=state, tmp_path=tmp_path)
        blocked, reason = v2.has_live_position_or_order()
        assert blocked
        assert "state.open_position" in reason


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end smoke: realistic combo response → arm_sl produces correct state
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueHandler:
    """Tests for bot's _handle_orphan_hint — the auto-recovery path."""

    def _make_state(self, with_open_pos=False):
        from webull_bot.state import BotState, OpenPosition
        state = BotState(trading_date="2026-05-22", trade_taken_today=False)
        if with_open_pos:
            state.open_position = OpenPosition(
                symbol="SPXW", expiry="2026-05-22",
                short_strike=7420.0, long_strike=7370.0, quantity=1,
                entry_credit=2.0, stop_price=4.0,
                entry_spx=7480.0, entry_vix=17.0, entry_ts="2026-05-22T10:30:00",
                client_order_id="X",
            )
        return state

    def _make_execution(self, broker_combos):
        execution = MagicMock()
        execution.snapshotter.snapshot_combos.return_value = broker_combos
        return execution

    def _make_store(self):
        store = MagicMock()
        store.save = MagicMock()
        return store

    def test_recovers_orphan_when_state_null_and_broker_has_active(self, tmp_path, monkeypatch):
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=False)
        store = self._make_store()
        broker_combo = make_spxw_combo(short_strike=7420, long_strike=7370, cost=2.00, last=1.30)
        execution = self._make_execution([broker_combo])
        cfg = {"stop_multiplier": 2.0}
        ev = {"id": "test1", "subject": "ORPHAN DETECTED", "body": "..."}
        _handle_orphan_hint(ev, state, store, cfg, execution)
        assert state.open_position is not None
        assert state.open_position.short_strike == 7420.0
        assert state.open_position.long_strike == 7370.0
        assert state.open_position.entry_credit == 2.0
        assert state.open_position.stop_price == 4.0
        assert state.trade_taken_today is True
        store.save.assert_called_once()

    def test_no_op_when_state_already_has_open_position(self, tmp_path, monkeypatch):
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=True)
        store = self._make_store()
        broker_combo = make_spxw_combo()
        execution = self._make_execution([broker_combo])
        cfg = {"stop_multiplier": 2.0}
        ev = {"id": "test2", "subject": "ORPHAN DETECTED"}
        _handle_orphan_hint(ev, state, store, cfg, execution)
        # State unchanged, store not called
        store.save.assert_not_called()
        # Broker also not queried (idempotency check happened first)
        execution.snapshotter.snapshot_combos.assert_not_called()

    def test_no_op_when_broker_actually_has_no_active_position(self, tmp_path, monkeypatch):
        """Watchdog might have stale info; bot re-verifies and finds nothing → no-op."""
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=False)
        store = self._make_store()
        # Broker returns only expired/worthless remnant (filtered out)
        expired = make_spxw_combo(last=0.02)
        execution = self._make_execution([expired])
        cfg = {"stop_multiplier": 2.0}
        ev = {"id": "test3", "subject": "ORPHAN DETECTED"}
        _handle_orphan_hint(ev, state, store, cfg, execution)
        assert state.open_position is None
        store.save.assert_not_called()

    def test_no_op_when_broker_query_fails(self, tmp_path, monkeypatch):
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=False)
        store = self._make_store()
        execution = MagicMock()
        execution.snapshotter.snapshot_combos.side_effect = Exception("API down")
        cfg = {"stop_multiplier": 2.0}
        ev = {"id": "test4", "subject": "ORPHAN DETECTED"}
        _handle_orphan_hint(ev, state, store, cfg, execution)  # should not raise
        assert state.open_position is None
        store.save.assert_not_called()

    def test_uses_configured_stop_multiplier(self, tmp_path, monkeypatch):
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=False)
        store = self._make_store()
        broker_combo = make_spxw_combo(cost=1.50)
        execution = self._make_execution([broker_combo])
        cfg = {"stop_multiplier": 3.0}  # not default 2.0
        ev = {"id": "test5", "subject": "ORPHAN DETECTED"}
        _handle_orphan_hint(ev, state, store, cfg, execution)
        assert state.open_position.stop_price == 4.50  # 1.50 × 3.0

    def test_queues_confirmation_event_after_recovery(self, tmp_path, monkeypatch):
        from webull_bot.main import _handle_orphan_hint
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        state = self._make_state(with_open_pos=False)
        store = self._make_store()
        broker_combo = make_spxw_combo()
        execution = self._make_execution([broker_combo])
        cfg = {"stop_multiplier": 2.0}
        ev = {"id": "test6", "subject": "ORPHAN DETECTED"}
        _handle_orphan_hint(ev, state, store, cfg, execution)
        # A new "SL ARMED (recovered)" event should be in the queue
        from webull_bot.event_queue import read_events
        events = read_events(limit=10)
        recovered = [e for e in events if "RECOVERED" in e.get("subject", "").upper() or "RECOVERED" in e.get("body", "").upper() or "recovered" in e.get("subject", "")]
        assert len(recovered) >= 1, f"Expected recovery event, got: {[e['subject'] for e in events]}"


@pytest.fixture
def real_send_alert(monkeypatch):
    """Restore the real send_alert (conftest stubs it via autouse)."""
    # Re-import the actual function from the module
    import importlib
    import webull_bot.alerts as _alerts
    importlib.reload(_alerts)
    # Re-apply ourselves to the module so other tests see our restore
    yield _alerts.send_alert


class TestAlertsRouting:
    """Verify alerts.send_alert routes through event queue with correct severity.
    Uses real_send_alert fixture to bypass conftest's autouse stub."""

    def test_critical_emoji_classified_correctly(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("🚨 ORPHAN DETECTED — check now")
        events = eq.read_events(limit=10)
        assert len(events) == 1
        assert events[0]["severity"] == "critical"

    def test_warn_emoji_classified_correctly(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("⚠️ heartbeat stale 45s")
        events = eq.read_events(limit=10)
        assert events[0]["severity"] == "warn"

    def test_info_default_severity(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("Routine status message with no emoji")
        events = eq.read_events(limit=10)
        assert events[0]["severity"] == "info"

    def test_critical_text_classified_correctly(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("STOP LOSS TRIGGERED")
        events = eq.read_events(limit=10)
        assert events[0]["severity"] == "critical"

    def test_subject_body_split(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("Subject line\nLong body\nwith multiple lines")
        events = eq.read_events(limit=10)
        assert events[0]["subject"] == "Subject line"
        assert "Long body" in events[0]["body"]
        assert "multiple lines" in events[0]["body"]

    def test_source_marked_as_alerts(self, tmp_path, monkeypatch, real_send_alert):
        from webull_bot import event_queue as eq
        monkeypatch.setattr(eq, "EVENT_QUEUE", tmp_path / "queue.jsonl")
        real_send_alert("Anything")
        events = eq.read_events(limit=10)
        assert events[0]["source"] == "alerts.send_alert"

    def test_empty_text_returns_false(self, real_send_alert):
        assert real_send_alert("") is False


class TestEndToEndSmoke:
    def test_today_real_scenario(self):
        """Today's 2026-05-22 fill: 7420/7370P credit=$2 → stop should be $4."""
        # Real shape captured from live broker today
        combo = {
            "currency": "USD",
            "quantity": "1",
            "cost": "-200.00",
            "position_id": "AUJ0IRDSSEE8AN2HB8U1K98MC8",
            "symbol": "SPXW",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "cost_price": "2.00",
            "last_price": "1.30",
            "legs": [
                {
                    "symbol": "SPXW",
                    "option_type": "PUT",
                    "option_expire_date": "2026-05-22",
                    "option_exercise_price": "7370.00",
                    "leg_id": "AUJ0IRDSSEE8AN2HB8U1K98MC8",
                },
                {
                    "symbol": "SPXW",
                    "option_type": "PUT",
                    "option_expire_date": "2026-05-22",
                    "option_exercise_price": "7420.00",
                    "leg_id": "AUJ0IRDSSEE8AN2HB8U1K98MC8",
                },
            ],
        }
        op = arm_sl_from_broker_combo(combo, stop_multiplier=2.0)
        # Verify all fields the bot's monitor and state file expect
        assert op["symbol"] == "SPXW"
        assert op["expiry"] == "2026-05-22"
        assert op["short_strike"] == 7420.0
        assert op["long_strike"] == 7370.0
        assert op["quantity"] == 1
        assert op["entry_credit"] == 2.0
        assert op["stop_price"] == 4.0
        # iids both = position_id (account_v2 doesn't expose per-leg)
        assert op["short_iid"] == "AUJ0IRDSSEE8AN2HB8U1K98MC8"
        assert op["long_iid"] == "AUJ0IRDSSEE8AN2HB8U1K98MC8"
