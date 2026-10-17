"""Chain loading + parity-derived SPX spot. Foundation of the fresh BPS engine.

Data contract per trading day:
  puts:  cache day-file + winter-hour repair (if any) concatenated
  calls: same
  Quotes: bid_px_00/ask_px_00 per minute per contract (CBBO 1-min).

Spot via put-call parity for 0DTE (r*t ~ 0 intraday):
  S(t) = K + C_mid(K,t) - P_mid(K,t), medianized across the 5 strikes nearest ATM
  with two-sided quotes. Cross-checked daily vs official Cboe close.
"""
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent / "spx_spread_bot/backtests/narrow_width_databento_2026_05_21"
REPAIR = Path(__file__).parent / "data/repairs"
NEWDAYS = Path(__file__).parent / "data/new_days"
# Upper call strikes (spot .. spot+4%), pulled 2026-08-07 for the iron-condor
# study. The original 2013-2026 cache was bought for bull PUT spreads and
# spans only ~spot-8% .. spot+0%, so a short call wing was absent.
CALLWING = Path(__file__).parent / "data/call_wing"
ET = "America/New_York"

def _load_side(tag, side):
    """side in {'p','c'}; tag = YYYYMMDD. Returns minute-indexed quote frame or None."""
    parts = []
    f = BASE / f"spxw_{side}_cbbo_1m" / f"spxw_{side}_cbbo_{tag}.parquet"
    if f.exists():
        parts.append(pd.read_parquet(f))
    nf = NEWDAYS / f"spxw_{side}_cbbo_{tag}.parquet"
    if nf.exists():
        parts.append(pd.read_parquet(nf))
    rf = REPAIR / f"winter_hour_{side.upper()}" / f"{tag}.parquet"
    if rf.exists() and rf.stat().st_size > 0:
        parts.append(pd.read_parquet(rf))
    mf = REPAIR / "missing_side" / f"{side.upper()}_{tag}.parquet"
    if mf.exists() and mf.stat().st_size > 0:
        parts.append(pd.read_parquet(mf))
    if side == "c":
        wf = CALLWING / f"spxw_c_cbbo_{tag}.parquet"
        if wf.exists() and wf.stat().st_size > 0:
            parts.append(pd.read_parquet(wf))
    if not parts:
        return None
    df = pd.concat(parts).sort_index()
    # normalize: keep quotes only
    df = df[["symbol", "bid_px_00", "ask_px_00", "bid_sz_00", "ask_sz_00"]].copy()
    df["strike"] = df["symbol"].str[-8:].astype(int) / 1000.0
    df["minute"] = df.index.tz_convert(ET)
    # dedup on (minute, contract) — repair files can overlap the original at the seam
    df = df.drop_duplicates(subset=["minute", "symbol"], keep="first")
    return df

def load_day(tag):
    """Returns dict with 'P' and 'C' minute-frames (or None if either missing)."""
    p = _load_side(tag, "p")
    c = _load_side(tag, "c")
    if p is None or c is None:
        return None
    return {"P": p, "C": c}

def quote_at(df, minute_et, strike=None):
    """All quotes at a given ET minute; optionally one strike row."""
    sel = df[df["minute"] == minute_et]
    if strike is not None:
        sel = sel[sel["strike"] == strike]
    return sel

def parity_spot(day, minute_et):
    """S = K + C_mid - P_mid, medianized over 5 strikes closest to ATM with clean
    two-sided quotes on both sides. Returns (spot, n_strikes_used) or (nan, 0)."""
    p = quote_at(day["P"], minute_et)
    c = quote_at(day["C"], minute_et)
    if len(p) == 0 or len(c) == 0:
        return np.nan, 0
    p = p[(p.bid_px_00 > 0) & (p.ask_px_00 > 0) & (p.ask_px_00 >= p.bid_px_00)]
    c = c[(c.bid_px_00 > 0) & (c.ask_px_00 > 0) & (c.ask_px_00 >= c.bid_px_00)]
    m = p.merge(c, on="strike", suffixes=("_p", "_c"))
    if len(m) == 0:
        return np.nan, 0
    m["p_mid"] = (m.bid_px_00_p + m.ask_px_00_p) / 2
    m["c_mid"] = (m.bid_px_00_c + m.ask_px_00_c) / 2
    m["s_est"] = m["strike"] + m["c_mid"] - m["p_mid"]
    # ATM = strikes where |c_mid - p_mid| smallest
    m["atm_dist"] = (m["c_mid"] - m["p_mid"]).abs()
    use = m.nsmallest(5, "atm_dist")
    return float(use["s_est"].median()), len(use)

def spot_series(day, minutes):
    """Parity spot at each ET minute in `minutes` (list of Timestamps)."""
    out = {}
    for t in minutes:
        s, n = parity_spot(day, t)
        out[t] = s
    return pd.Series(out)

if __name__ == "__main__":
    # smoke test on a summer day + winter day, cross-check vs official close
    spx = pd.read_csv("data/spx_daily_cboe.csv", parse_dates=["DATE"]).set_index("DATE")["SPX"]
    for tag in ("20240607", "20240116", "20180928"):
        day = load_day(tag)
        if day is None:
            print(tag, "no data"); continue
        d = pd.Timestamp(tag)
        t_open = d.tz_localize(ET) + pd.Timedelta(hours=9, minutes=35)
        t_late = d.tz_localize(ET) + pd.Timedelta(hours=15, minutes=55)
        s_open, n1 = parity_spot(day, t_open)
        s_late, n2 = parity_spot(day, t_late)
        official = spx.get(d, np.nan)
        print(f"{tag}: parity@09:35={s_open:.2f}(n={n1})  parity@15:55={s_late:.2f}(n={n2})  official_close={official:.2f}  diff={s_late-official:+.2f}")
