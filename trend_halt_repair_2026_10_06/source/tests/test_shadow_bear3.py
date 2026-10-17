"""Causality regressions for the SHADOW-ONLY prod+bear3 report line.

Three paths, each an incident class this program actually had:
  1. a core NQ entry force-closes an open shadow short at that bar's open
     (core priority — the long path must never wait on the short);
  2. a pending short is cancelled when the core is active at the entry bar;
  3. the ret5 gate must lag to D-1 daily closes — the C1 look-ahead class
     (the leak that fabricated 77% of the original V2 bear edge).
"""
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

import shadow_report as sr  # noqa: E402


def _bars(start="2026-07-01", periods=30, break_at=25):
    """Flat closes at 100, breaking down to 90 from `break_at` on —
    the 20-bar close-low first breaches after bar 20 as production does."""
    ix = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    B = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5,
                      "close": 100.0, "roll": 0}, index=ix)
    for col, val in (("open", 90.0), ("high", 90.5),
                     ("low", 89.0), ("close", 90.0)):
        B.iloc[break_at:, B.columns.get_loc(col)] = val
    return B


def _frame(B):
    return pd.DataFrame({"atr": 1.0, "vol_ok": True, "session_ok": True},
                        index=B.index)


def _daily_falling(B):
    """Strictly falling daily closes covering the bar window, so the
    D-1-lagged 5-day-return gate is True on every bar date."""
    days = pd.date_range(B.index[0].normalize() - pd.Timedelta(days=15),
                         periods=40, freq="D", tz="UTC")
    return pd.Series(np.linspace(120.0, 80.0, len(days)), index=days)


def _no_core():
    return pd.DataFrame({"entry_time": pd.Series(dtype="datetime64[ns, UTC]"),
                         "exit_time": pd.Series(dtype="datetime64[ns, UTC]")})


def test_core_entry_force_closes_short_at_that_open():
    B = _bars()
    core = pd.DataFrame({"entry_time": [B.index[28]],
                         "exit_time": [B.index[29]]})
    with patch.object(sr.strategy, "signal_frame", return_value=_frame(B)):
        T = sr.bear3_short_trades(B, _daily_falling(B), core)
    assert len(T) == 1
    tr = T.iloc[0]
    assert tr["reason"] == "core_long"
    assert tr["entry_time"] == B.index[26]      # signal bar 25, next open
    assert tr["exit_time"] == B.index[28]       # the core entry bar
    assert tr["points"] == 0.0                  # entered 90, exited open 90


def test_pending_short_cancelled_when_core_active_at_entry_bar():
    B = _bars()
    core = pd.DataFrame({"entry_time": [B.index[26]],
                         "exit_time": [B.index[29]]})
    with patch.object(sr.strategy, "signal_frame", return_value=_frame(B)):
        T = sr.bear3_short_trades(B, _daily_falling(B), core)
    assert T.empty


def test_ret5_gate_lags_to_prior_day_close():
    # Daily closes RISE through the day before the bars and crash ON the
    # bar day. An honest D-1 gate sees only the rising history -> no
    # shorts. A same-day leak (the C1 class) would open one. The
    # breakdown bar (22) sits on the crash day itself.
    B = _bars(start="2026-07-02", break_at=22)
    days = pd.date_range(B.index[0].normalize() - pd.Timedelta(days=30),
                         B.index[0].normalize(), freq="D", tz="UTC")
    daily = pd.Series(np.linspace(80.0, 120.0, len(days)), index=days)
    daily.iloc[-1] = 60.0                       # crash on the bar day itself
    with patch.object(sr.strategy, "signal_frame", return_value=_frame(B)):
        T = sr.bear3_short_trades(B, daily, _no_core())
    assert T.empty


def test_gap_honest_stop_fills_at_worse_open():
    # No core. Short enters at 90 (stop 92, trailed to 91), then the
    # market gaps to 95: the fill must be the WORSE open, not the stop.
    B = _bars()
    for col, val in (("open", 95.0), ("high", 95.5),
                     ("low", 94.0), ("close", 95.0)):
        B.iloc[28:, B.columns.get_loc(col)] = val
    with patch.object(sr.strategy, "signal_frame", return_value=_frame(B)):
        T = sr.bear3_short_trades(B, _daily_falling(B), _no_core())
    assert len(T) == 1
    tr = T.iloc[0]
    assert tr["reason"] == "stop"
    assert tr["exit_time"] == B.index[28]
    assert tr["points"] == 90.0 - 95.0          # gap fill, not stop px
