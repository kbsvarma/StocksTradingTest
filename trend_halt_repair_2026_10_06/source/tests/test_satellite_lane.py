"""Live-lane regressions for the breadth satellite v2 (frozen 2026-08-04).

15-minute signal bars, entries from channel formation until 15:00 ET, stop
anchored to HOURLY ATR, flat 16:00. Encodes the session lessons (evening
guard) and the v2 mechanics (subpoll-callable, per-bar dedup).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))

from test_failure_injection import tb, FakeEx  # noqa: E402,F401

DAY = "2026-07-21"                             # a Tuesday
FRONT = SimpleNamespace(conId=901, localSymbol="MESU6")


def _hourly_history(seed=3):
    """Noisy hourly parent history: ATR + the 95th-pctile vol gate need a
    VARIABLE-range past (constant TR rejects any move)."""
    idx = pd.date_range(end=f"{DAY} 08:00", periods=80 * 23, freq="h",
                        tz="America/New_York")
    rng = np.random.default_rng(seed)
    px = 100.0 + rng.normal(0, 0.4, len(idx)).cumsum() * 0.05
    w = rng.uniform(0.5, 3.0, len(idx))
    return pd.DataFrame({"open": px, "high": px + w, "low": px - w,
                         "close": px, "volume": 1.0},
                        index=idx.tz_convert("UTC"))


def _bars15(last_hm, breakout_hm=None):
    """DAY session 15m bars 09:00 .. last_hm inclusive (+ evening 18:00+)."""
    times, px = [], []
    t = pd.Timestamp(f"{DAY} 09:00", tz="America/New_York")
    end = pd.Timestamp(f"{DAY} {last_hm}", tz="America/New_York")
    while t <= end:
        times.append(t)
        px.append(100.0)
        t += pd.Timedelta(minutes=15)
    for hm in ("18:00", "18:15"):
        times.append(pd.Timestamp(f"{DAY} {hm}", tz="America/New_York"))
        px.append(100.0)
    B = pd.DataFrame({"open": px, "high": [p + 0.5 for p in px],
                      "low": [p - 0.5 for p in px], "close": px},
                     index=pd.DatetimeIndex(times).tz_convert("UTC"))
    if breakout_hm:
        bts = pd.Timestamp(f"{DAY} {breakout_hm}",
                           tz="America/New_York").tz_convert("UTC")
        B.loc[B.index >= bts, ["open", "high", "close"]] += 12.0
    return B


def _setup(tb, benched=True, core_on=True, breakout_hm="12:45",
           last_hm="12:45"):
    tb.all_instruments = {**tb.cfg["core_instruments"],
                         **tb.cfg["sleeve_instruments"],
                         **tb.cfg["satellite_instruments"]}
    tb.st.state["rotation"] = {
        "weights": ({"YM": 3, "ES": 0, "NQ": 0} if benched
                    else {"YM": 1, "ES": 1, "NQ": 1})}
    if core_on:
        tb.st.pos("YM").update(position=1, qty=3)
    tb.store._cache["ES"] = _hourly_history()
    bars15 = _bars15(last_hm, breakout_hm)
    tb.data = SimpleNamespace(
        front_contract=lambda *a, **k: FRONT,
        hourly_bars=lambda *a, **k: bars15)
    tb._entered = []
    tb._enter = lambda sym, icfg, front, w, c, a, tag="": \
        tb._entered.append((sym, w, round(c, 2)))


def _now(hm):
    return pd.Timestamp(f"{DAY} {hm}",
                        tz="America/New_York").tz_convert("UTC")


def test_v2_enters_on_15m_breakout_midday(tb):
    _setup(tb)                                   # breakout on the 12:45 bar
    tb.process_satellite(_now("13:07"))
    assert [e[0] for e in tb._entered] == ["ES_S"]
    assert tb.st.state["satellite_day"]["ES_S"] == DAY


def test_v2_enters_early_morning(tb):
    _setup(tb, breakout_hm="09:45", last_hm="09:45")
    tb.process_satellite(_now("10:03"))
    assert [e[0] for e in tb._entered] == ["ES_S"]


def test_v2_cutoff_1500(tb):
    _setup(tb, breakout_hm="14:45", last_hm="14:45")
    tb.process_satellite(_now("15:04"))          # entry bar would be >=15:00
    assert tb._entered == []


def test_v2_requires_core_position(tb):
    _setup(tb, core_on=False)
    tb.process_satellite(_now("13:07"))
    assert tb._entered == []


def test_v2_requires_benched_week(tb):
    _setup(tb, benched=False)
    tb.process_satellite(_now("13:07"))
    assert tb._entered == []


def test_v2_same_bar_not_reevaluated_but_new_bar_is(tb):
    _setup(tb, breakout_hm=None, last_hm="12:45")   # no breakout yet
    tb.process_satellite(_now("13:02"))
    tb.process_satellite(_now("13:07"))             # same completed bar
    assert tb._entered == []
    # a NEW bar arrives WITH a breakout -> must be evaluated
    _b = _bars15("13:00", "13:00")
    tb.data.hourly_bars = lambda *a, **k: _b
    tb.process_satellite(_now("13:17"))
    assert [e[0] for e in tb._entered] == ["ES_S"]


def test_v2_never_enters_evening(tb):
    _setup(tb, breakout_hm="18:00", last_hm="12:45")
    tb.process_satellite(_now("18:33"))
    assert tb._entered == []


def test_v2_eod_flatten_books_and_clears(tb):
    _setup(tb)
    p = tb.st.pos("ES_S")
    p.update(position=1, qty=1, entry_px=100.0,
             entry_time="2026-07-21T17:00:00+00:00", stop_px=98.0,
             con_id=901, local_symbol="MESU6", entry_weight=1, hw=101.0,
             sig_atr=1.0, stop_order_id=77, entry_exec_ids=[],
             entry_fees_allocated=False)
    tb.ex.positions_map = {901: 0.0}
    tb.ex.contract_by_conid = lambda cid: FRONT
    tb.process_satellite(_now("16:00"))
    assert tb.st.pos("ES_S")["position"] == 0
    rows = (tb.st.dir / "trades.csv").read_text().splitlines()
    assert any("ES_S" in r and "eod" in r and "satellite" in r for r in rows)


def test_v2_stale_history_not_entered(tb):
    """Audit 2026-08-03 §3: if the latest EXPECTED completed bar has not
    arrived, an older bar must not be traded as a live breakout."""
    _setup(tb)                                   # last bar 12:45 w/ breakout
    tb.process_satellite(_now("13:22"))          # expected 13:00 is missing
    assert tb._entered == []
    # sigmark must NOT be consumed by the stale skip
    assert tb.st.state.get("satellite_sig_bar", {}).get("ES_S") is None


def test_v2_roll_session_blocks_entry(tb):
    """Audit 2026-08-03 §4: research skips contract-roll sessions; the live
    gate must block the session on which the front conId changes."""
    _setup(tb)
    tb.st.state["satellite_front"] = {"ES_S": {"conid": 900}}  # old front
    tb.process_satellite(_now("13:07"))
    assert tb._entered == []
    assert tb.st.state["satellite_front"]["ES_S"]["roll_session"] == DAY
    # same front next time -> no new roll_session, entry allowed again
    tb.st.state["satellite_front"]["ES_S"] = {"conid": FRONT.conId}
    tb.st.state.get("satellite_sig_bar", {}).pop("ES_S", None)
    tb.process_satellite(_now("13:07"))
    assert [e[0] for e in tb._entered] == ["ES_S"]


def test_v2_early_close_flattens_before_noon(tb):
    """Audit 2026-08-03 §5: on CME early-close days a held satellite must
    flatten during the 11:xx wakes, not wait for the 16:00 clock."""
    _setup(tb)
    tb.st.pos("ES_S").update(position=1, qty=1)
    flat = []
    tb._satellite_flatten = lambda sym, icfg: flat.append(sym)
    ec = pd.Timestamp("2026-09-07 11:30",
                      tz="America/New_York").tz_convert("UTC")
    tb.process_satellite(ec)                     # Labor Day: early close
    assert flat == ["ES_S"]
    flat.clear()
    tb.process_satellite(_now("11:30"))          # normal day: no flatten
    assert flat == []
