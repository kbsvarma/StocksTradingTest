"""Tests for startup_reconcile_v2.decide_action — every case from design matrix."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from webull_bot.startup_reconcile_v2 import decide_action, _is_pending_stale, PENDING_STALE_SECONDS

ET = ZoneInfo("America/New_York")


def _fresh_ts() -> str:
    return datetime.now(ET).isoformat()


def _stale_ts(seconds_ago: int = 300) -> str:
    return (datetime.now(ET) - timedelta(seconds=seconds_ago)).isoformat()


def _make_open_pos(short=7360, long=7310, qty=1, credit=2.10):
    return {
        "symbol": "SPXW", "expiry": "2026-05-21",
        "short_strike": short, "long_strike": long, "quantity": qty,
        "entry_credit": credit, "stop_price": credit * 2.0,
        "short_iid": "IID-S", "long_iid": "IID-L",
    }


def _make_pending(short=7360, long=7310, qty=1, ts=None):
    return {
        "ts": ts or _fresh_ts(),
        "symbol": "SPXW", "expiry": "2026-05-21",
        "short_strike": short, "long_strike": long, "quantity": qty,
        "intended_credit": 2.00, "intended_limit": 2.00,
        "client_order_id": "cid-abc",
    }


def _make_broker_holding(strike, qty, cost, iid=None):
    return {
        "instrument_id": iid or f"IID-{strike}-{qty}",
        "symbol": "SPXW",
        "instrument_type": "OPTION",
        "qty": str(qty),
        "unit_cost": str(cost),
        "strike_price": str(strike),
    }


# ── Decision matrix cases ───────────────────────────────────────────────────

class TestCase1_StateOpenPosition:
    def test_open_pos_matches_broker_NORMAL_RESUME(self):
        state = {"open_position": _make_open_pos()}
        broker = [
            _make_broker_holding(7360, -1, 2.52),
            _make_broker_holding(7310, 1, 0.42),
        ]
        d = decide_action(state, broker)
        assert d.action == "NORMAL_RESUME"

    def test_open_pos_broker_empty_CLEAR_STATE(self):
        state = {"open_position": _make_open_pos()}
        d = decide_action(state, broker_holdings=[])
        assert d.action == "CLEAR_STATE"
        assert "closed externally" in d.reason.lower()
        assert d.alert_message is not None

    def test_open_pos_different_broker_HALT(self):
        state = {"open_position": _make_open_pos(short=7360, long=7310)}
        broker = [
            _make_broker_holding(7400, -1, 1.50),  # WRONG strike
            _make_broker_holding(7350, 1, 0.50),
        ]
        d = decide_action(state, broker)
        assert d.action == "HALT"
        assert "different" in d.reason.lower() or "manual review" in d.alert_message.lower()


class TestCase2_StateEmpty:
    def test_empty_state_empty_broker_NORMAL_START(self):
        d = decide_action(state={}, broker_holdings=[])
        assert d.action == "NORMAL_START"

    def test_empty_state_stale_pending_no_broker_CLEAR_PENDING(self):
        state = {"pending_order": _make_pending(ts=_stale_ts(300))}
        d = decide_action(state, broker_holdings=[])
        assert d.action == "CLEAR_PENDING"
        assert "place failed" in d.reason.lower() or "no matching" in d.reason.lower()

    def test_empty_state_broker_has_no_pending_HALT(self):
        """Orphan with no pending = can't auto-recover, halt."""
        broker = [
            _make_broker_holding(7360, -1, 2.52),
            _make_broker_holding(7310, 1, 0.42),
        ]
        d = decide_action(state={}, broker_holdings=broker)
        assert d.action == "HALT"
        assert "orphan" in d.reason.lower() or "no pending" in d.reason.lower()

    def test_stale_pending_matches_broker_PROMOTE(self):
        """Auto-recovery: stale pending + matching broker → promote."""
        state = {"pending_order": _make_pending(ts=_stale_ts(300))}
        broker = [
            _make_broker_holding(7360, -1, 2.52),
            _make_broker_holding(7310, 1, 0.42),
        ]
        d = decide_action(state, broker)
        assert d.action == "PROMOTE_PENDING"
        assert d.new_open_position is not None
        # Verify the recovered position uses BROKER's actual cost basis, not pending's intended
        assert d.new_open_position["entry_credit"] == pytest.approx(2.10)  # 2.52 - 0.42
        assert d.new_open_position["short_strike"] == 7360
        assert d.new_open_position["long_strike"] == 7310
        assert d.new_open_position.get("_auto_recovered") is True

    def test_stale_pending_wrong_strikes_HALT(self):
        """Pending says 7360/7310 but broker has 7400/7350 → halt."""
        state = {"pending_order": _make_pending(short=7360, long=7310, ts=_stale_ts(300))}
        broker = [
            _make_broker_holding(7400, -1, 1.50),
            _make_broker_holding(7350, 1, 0.50),
        ]
        d = decide_action(state, broker)
        assert d.action == "HALT"

    def test_fresh_pending_matches_broker_PROMOTE(self):
        """Fresh pending + matching broker → trust the fill, promote.
        (Bot may have crashed right after place but before recording)"""
        state = {"pending_order": _make_pending(ts=_fresh_ts())}
        broker = [
            _make_broker_holding(7360, -1, 2.52),
            _make_broker_holding(7310, 1, 0.42),
        ]
        d = decide_action(state, broker)
        # Either PROMOTE_PENDING (if we trust the match) or DEFER_TO_BOT
        # Current code says: fresh pending + match → DEFER (bot still placing)
        assert d.action in ("PROMOTE_PENDING", "DEFER_TO_BOT")

    def test_fresh_pending_no_broker_DEFER(self):
        """Bot is in middle of place_order, hasn't filled yet → don't touch."""
        state = {"pending_order": _make_pending(ts=_fresh_ts())}
        d = decide_action(state, broker_holdings=[])
        assert d.action in ("NORMAL_START", "DEFER_TO_BOT")
        # Either acceptable — we don't crash a fresh-pending state


class TestPendingStaleCheck:
    def test_fresh_pending_not_stale(self):
        assert not _is_pending_stale({"ts": _fresh_ts()})

    def test_stale_pending_detected(self):
        assert _is_pending_stale({"ts": _stale_ts(PENDING_STALE_SECONDS + 30)})

    def test_no_ts_treated_as_not_stale(self):
        # If we can't tell, don't act
        assert not _is_pending_stale({})

    def test_invalid_ts_treated_as_stale(self):
        assert _is_pending_stale({"ts": "not-a-timestamp"})


class TestAutoRecoveryUsesBrokerData:
    """The actual cost basis must come from broker, not pending's intended value."""

    def test_credit_from_broker_not_pending(self):
        state = {"pending_order": _make_pending(ts=_stale_ts(300))}
        # Broker shows DIFFERENT credit than pending's intended_credit (2.00)
        broker = [
            _make_broker_holding(7360, -1, 2.75),  # better fill
            _make_broker_holding(7310, 1, 0.45),
        ]
        d = decide_action(state, broker)
        assert d.action == "PROMOTE_PENDING"
        assert d.new_open_position["entry_credit"] == pytest.approx(2.30)  # 2.75 - 0.45
        assert d.new_open_position["stop_price"] == pytest.approx(4.60)  # 2.30 × 2
