"""Canonical ET-session layer built from 1-min bars. This is the single source of
truth for all event-edge tests. Vendor daily bars are NOT used for trade prices
(they are UTC-calendar-day bars — see DATA_NOTES.md).

Definitions (America/New_York):
  RTH        = 09:30:00 .. 15:59:00 inclusive of minute-open timestamps
  rth_open   = open of the 09:30 bar
  rth_close  = close of the 15:59 bar (last RTH minute)
  Sessions are keyed by the ET calendar date of the RTH block.

Returns:
  intraday_ret[d] = rth_close[d] / rth_open[d]  - 1
  overnight_ret[d]= rth_open[d]  / rth_close[d-1] - 1   (close of prior session -> open of this one)
Both are computed ONLY when the two prices share the same instrument_id is NOT
required for open/close of the same session; for overnight we DO require that the
prior close and current open are on adjacent sessions and we FLAG roll-spanning
overnights (instrument_id change) so they can be excluded.
"""
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).parent / "data"
ET = "America/New_York"

def load_1m(tag):
    df = pd.concat([pd.read_parquet(f) for f in sorted(DATA.glob(f"{tag}_ohlcv-1m_*.parquet"))]).sort_index()
    df = df[~df.index.duplicated()]
    df = df.tz_convert(ET)
    return df

def build_sessions(tag):
    """Return a per-ET-date DataFrame with RTH open/close/high/low, instrument ids,
    and prior-session linkage for overnight returns."""
    m = load_1m(tag)
    m["etdate"] = m.index.normalize()
    tmin = m.index.hour * 60 + m.index.minute
    rth = m[(tmin >= 9 * 60 + 30) & (tmin <= 15 * 60 + 59)]

    g = rth.groupby(rth["etdate"])
    first = g.first()
    last = g.last()
    sess = pd.DataFrame({
        "rth_open":  first["open"],
        "rth_close": last["close"],
        "rth_high":  g["high"].max(),
        "rth_low":   g["low"].min(),
        "rth_vol":   g["volume"].sum(),
        "n_bars":    g.size(),
        "iid_open":  first["instrument_id"],
        "iid_close": last["instrument_id"],
        "open_ts":   g.apply(lambda x: x.index[0], include_groups=False),
        "close_ts":  g.apply(lambda x: x.index[-1], include_groups=False),
    })
    sess = sess.sort_index()
    # keep only real RTH sessions (a full-ish day has ~390 one-min bars)
    sess = sess[sess["n_bars"] >= 60]

    # returns
    sess["intraday_ret"] = sess["rth_close"] / sess["rth_open"] - 1.0
    sess["prev_close"] = sess["rth_close"].shift(1)
    sess["prev_iid_close"] = sess["iid_close"].shift(1)
    sess["overnight_ret"] = sess["rth_open"] / sess["prev_close"] - 1.0
    # roll flag: overnight spans a contract change
    sess["overnight_roll"] = sess["iid_open"] != sess["prev_iid_close"]
    # close-to-close (session) return, roll-flagged
    sess["cc_ret"] = sess["rth_close"] / sess["prev_close"] - 1.0
    return sess

def get_minute(tag):
    """ET-localized 1-min frame for intraday-window work (FOMC, macro releases)."""
    return load_1m(tag)

if __name__ == "__main__":
    for tag in ("ES", "NQ"):
        s = build_sessions(tag)
        print(f"\n{tag}: {len(s)} RTH sessions {s.index[0].date()} -> {s.index[-1].date()}")
        print(f"  median RTH bars/day: {s['n_bars'].median():.0f}  (short days <300: {(s['n_bars']<300).sum()})")
        print(f"  overnight roll-spanning sessions (excluded from ON tests): {s['overnight_roll'].sum()}")
        print(f"  mean intraday ret: {s['intraday_ret'].mean()*1e4:+.2f} bps   "
              f"mean overnight ret: {s.loc[~s['overnight_roll'],'overnight_ret'].mean()*1e4:+.2f} bps")
        # cross-check: session count vs vendor daily
        vd = pd.concat([pd.read_parquet(f) for f in sorted(DATA.glob(f"{tag}_ohlcv-1d_*.parquet"))])
        print(f"  vendor daily bars: {len(vd)}  |  our RTH sessions: {len(s)}")
