"""Regressions for the SHADOW-ONLY breadth satellite line.

Encodes the 2026-08-03 daysat lessons: session must be a [09:00, 16:00) ET
window (my original hour>=9 admitted 387 evening entries), entries only
before 14:00 ET, and the core-confirmation gate is load-bearing.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

import shadow_report as sr  # noqa: E402


def _bars(closes_by_hour, day="2026-07-21", sym_seed=0):
    """One ET trading day of hourly bars incl. evening session."""
    rows = []
    for h, px in closes_by_hour:
        ts = pd.Timestamp(f"{day} {h:02d}:00", tz="America/New_York")
        rows.append((ts.tz_convert("UTC"), px))
    idx = pd.DatetimeIndex([r[0] for r in rows])
    px = np.array([r[1] for r in rows], dtype=float)
    return pd.DataFrame({"open": px, "high": px + 1.0, "low": px - 1.0,
                         "close": px, "roll": 0}, index=idx)


def _history(sym_px=100.0, days=80, start="2026-04-01", seed=3):
    """Warmup history with VARIABLE ranges: a constant-TR history makes the
    95th-pctile vol gate reject any move at all (found writing this test)."""
    idx = pd.date_range(start=start, periods=days * 23, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    px = sym_px + rng.normal(0, 0.4, len(idx)).cumsum() * 0.05
    w = rng.uniform(0.5, 3.0, len(idx))
    return pd.DataFrame({"open": px, "high": px + w, "low": px - w,
                         "close": px, "roll": 0}, index=idx)


def _setup(breakout_hour=12, core_active=True, evening=False):
    hist = {s: _history() for s in ("NQ", "ES", "YM")}
    day = "2026-07-21"
    hours = [(h, 100.0) for h in range(9, 16)]
    if evening:
        hours += [(h, 100.0) for h in (18, 19, 20)]
    bars = {}
    for s in ("NQ", "ES", "YM"):
        base = pd.concat([hist[s], _bars(hours, day)])
        bars[s] = base
    # ES = benched breakout candidate: pops at breakout_hour
    B = bars["ES"].copy()
    et = B.index.tz_convert("America/New_York")
    tgt = (et.strftime("%Y-%m-%d") == day) & (et.hour >= breakout_hour)
    B.loc[tgt, ["open", "high", "low", "close"]] += 4.0
    bars["ES"] = B
    # YM strongly up over the ranking window => weekly leader (3-0-0)
    Y = bars["YM"].copy()
    Y.loc[:, ["open", "high", "low", "close"]] += np.linspace(
        0, 60, len(Y))[:, None]
    bars["YM"] = Y
    core = pd.DataFrame({
        "entry_time": [pd.Timestamp(f"{day} 10:00",
                                    tz="America/New_York").tz_convert("UTC")],
        "exit_time": [pd.Timestamp(f"{day} 15:30",
                                   tz="America/New_York").tz_convert("UTC")],
        "qty": [3 if core_active else 0]})
    core = core[core["qty"] > 0]
    return bars, core


def test_satellite_takes_confirmed_benched_breakout():
    bars, core = _setup(breakout_hour=12)
    T = sr.satellite_trades(bars, core)
    es = T[T["sym"] == "ES"]
    assert len(es) == 1
    hin = es["entry_time"].dt.tz_convert("America/New_York").dt.hour.iloc[0]
    assert hin == 13                       # signal 12:00 bar -> next open
    hout = es["exit_time"].dt.tz_convert("America/New_York").dt.hour.iloc[0]
    assert hout <= 15                      # flat by the US close


def test_satellite_requires_core_confirmation():
    bars, core = _setup(breakout_hour=12, core_active=False)
    T = sr.satellite_trades(bars, core)
    assert len(T[T["sym"] == "ES"]) == 0


def test_satellite_never_enters_evening_session():
    """The original bug: hour>=9 admitted 18:00+ entries."""
    bars, core = _setup(breakout_hour=18, evening=True)
    T = sr.satellite_trades(bars, core)
    assert len(T[T["sym"] == "ES"]) == 0


def test_satellite_entry_cutoff_1400():
    bars, core = _setup(breakout_hour=14)
    T = sr.satellite_trades(bars, core)
    assert len(T[T["sym"] == "ES"]) == 0   # entry bar would be 15:00 ET
