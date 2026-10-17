"""Unit tests for _check_vix_gate in webull_bot_atm.main.

Validates the ATM-specific VIX gate logic:
  - vix_open < vix_open_max
  - vix_overnight_gap < vix_overnight_gap_max
  Both forward-looking (no hindsight).
"""
from __future__ import annotations
from datetime import date
from unittest.mock import patch
import pytest

from webull_bot_atm.main import _check_vix_gate


class _StubLogger:
    def info(self, *_args, **_kw): pass
    def warning(self, *_args, **_kw): pass
    def error(self, *_args, **_kw): pass


def _atm_cfg(**overrides):
    """Default ATM config dict."""
    base = {
        "vix_open_max": 25.0,
        "vix_overnight_gap_max": 1.0,
        "vix_min": 0.0,
        "vix_max": 100.0,
    }
    base.update(overrides)
    return base


def test_normal_day_passes_gate():
    """vix_open=15, vix_prev=15.5 (gap=-0.5) → should pass."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=15.5), \
         patch("webull_bot_atm.main.get_vix_open", return_value=15.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=15.5):
        ok, reason, vix = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is True, f"Expected pass, got fail: {reason}"
    assert reason == ""


def test_vix_open_above_max_fails():
    """vix_open=26 ≥ 25 → fail."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=26.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=26.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=22.0):
        ok, reason, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False
    assert "vix_open" in reason and ">=" in reason


def test_vix_open_just_at_max_fails():
    """vix_open=25 == 25 → fail (strict <)."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=25.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=25.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=23.0):
        ok, _, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False


def test_overnight_gap_above_max_fails():
    """vix_open=18, prev=16 → gap=+2 ≥ 1 → fail."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=18.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=18.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=16.0):
        ok, reason, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False
    assert "overnight_gap" in reason


def test_overnight_gap_exactly_at_max_fails():
    """gap=1.0 == 1.0 → fail (strict <)."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=17.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=17.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=16.0):
        ok, _, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False


def test_overnight_gap_negative_passes():
    """vix_open=14, prev=16 → gap=-2 (vol declining) → pass."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=14.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=14.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=16.0):
        ok, _, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is True


def test_vix_open_unavailable_fails_safe():
    """If vix_open=0 (data unavailable), gate fails (fail-safe)."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=15.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=0.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=14.0):
        ok, reason, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False
    assert "unavailable" in reason


def test_vix_prev_unavailable_fails_safe():
    """If vix_prev=0 (data unavailable), gate fails (fail-safe)."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=15.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=15.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=0.0):
        ok, reason, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is False
    assert "unavailable" in reason


def test_both_pass_at_boundary():
    """vix_open=24.99, gap=0.99 → both just barely pass."""
    with patch("webull_bot_atm.main.get_vix_price", return_value=24.99), \
         patch("webull_bot_atm.main.get_vix_open", return_value=24.99), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=24.01):
        ok, _, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is True


def test_vix_now_doesnt_matter_for_gate():
    """The live vix_now value is NOT used by the gate (only for display).
    A high vix_now with low vix_open should still pass."""
    # vix_now=30 (vol spiked DURING the day) but vix_open=15 (calm at open)
    # Bot at 9:45 only saw vix_open=15 → should have taken the trade
    with patch("webull_bot_atm.main.get_vix_price", return_value=30.0), \
         patch("webull_bot_atm.main.get_vix_open", return_value=15.0), \
         patch("webull_bot_atm.main.get_vix_prev_close", return_value=14.5):
        ok, _, _ = _check_vix_gate(_atm_cfg(), _StubLogger(), date(2026, 5, 22))
    assert ok is True, "vix_now should NOT block — only vix_open and vix_overnight_gap"


def test_legacy_vix_max_still_works_if_configured():
    """Backward compat: if vix_open_max not set, falls back to vix_max behavior."""
    cfg = {
        "vix_min": 12,
        "vix_max": 25,
        "vix_max_daily_rise": 0,  # don't trigger that path
        # vix_open_max NOT set
        # vix_overnight_gap_max NOT set
    }
    with patch("webull_bot_atm.main.get_vix_price", return_value=30.0):
        ok, reason, _ = _check_vix_gate(cfg, _StubLogger(), date(2026, 5, 22))
    assert ok is False
    assert "outside" in reason
