"""Study 2 — options-surface state at breakout signals ($0, owned SPXW cache).

For every RTH-hour ES/NQ/YM breakout signal 2016+: read the 0DTE SPXW chain
at the signal minute and compute
  iv_proxy  = ATM straddle mid / (0.8 * S * sqrt(T_to_close))   (B-S approx)
  skew      = (put mid @ ~S*0.99 - call mid @ ~S*1.01) / straddle mid
Then: trade expectancy by iv/skew quartiles. Bar: t>=3 + halves-consistent.
Coverage note: cache = 0DTE expiry days (MWF pre-2022, daily after); non-
covered days skipped and counted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent / "trend_bot"))
sys.path.insert(0, str(BASE.parent / "trend_bot/research"))
sys.path.insert(0, str(BASE.parent / "bps_rederive_2026_07"))
sys.path.insert(0, str(BASE.parent / "event_edge_research"))
import rotation_grid as rg
from chain import load_day, parity_spot, quote_at

ET = "America/New_York"


def signals():
    rows = []
    for sym in ("NQ", "ES", "YM"):
        B = rg.load_60m(sym)
        T = rg.simulate(B, sym)
        T = T[T["entry_time"] >= pd.Timestamp("2016-01-01", tz="UTC")].copy()
        T["sym"] = sym
        T["pnl"] = T["points"] * rg.MULT[sym] - rg.FR
        rows.append(T[["entry_time", "exit_time", "sym", "pnl"]])
    A = pd.concat(rows).reset_index(drop=True)
    A["min_et"] = pd.DatetimeIndex(A["entry_time"]).tz_convert(ET)
    hrs = A["min_et"].dt.hour
    # RTH-hour signals only (options quotes exist): 10:00..15:00 hour marks
    A = A[(hrs >= 10) & (hrs <= 15) & (A["min_et"].dt.minute == 0)].copy()
    A["tag"] = A["min_et"].dt.strftime("%Y%m%d")
    return A.reset_index(drop=True)


def straddle_and_skew(day, minute_et):
    s, n = parity_spot(day, minute_et)
    if not np.isfinite(s) or n < 3:
        return None
    p = quote_at(day["P"], minute_et)
    c = quote_at(day["C"], minute_et)
    p = p[(p.bid_px_00 > 0) & (p.ask_px_00 >= p.bid_px_00)]
    c = c[(c.bid_px_00 > 0) & (c.ask_px_00 >= c.bid_px_00)]
    if not len(p) or not len(c):
        return None

    def mid_at(df, k_target):
        k = df.iloc[(df["strike"] - k_target).abs().argsort()[:1]]
        if not len(k) or abs(k["strike"].iloc[0] - k_target) > 12.5:
            return np.nan
        return float((k["bid_px_00"].iloc[0] + k["ask_px_00"].iloc[0]) / 2)

    atm = round(s / 5) * 5
    pm, cm = mid_at(p, atm), mid_at(c, atm)
    if not (np.isfinite(pm) and np.isfinite(cm)):
        return None
    straddle = pm + cm
    close_et = minute_et.normalize() + pd.Timedelta(hours=16, minutes=15)
    T_yr = max((close_et - minute_et).total_seconds(), 600) / (365.25 * 24 * 3600)
    iv = straddle / (0.8 * s * np.sqrt(T_yr))
    otm_p = mid_at(p, round(s * 0.99 / 5) * 5)
    otm_c = mid_at(c, round(s * 1.01 / 5) * 5)
    skew = ((otm_p - otm_c) / straddle
            if np.isfinite(otm_p) and np.isfinite(otm_c) and straddle > 0
            else np.nan)
    return dict(spot=s, iv=iv, skew=skew)


def main():
    A = signals()
    print(f"RTH-hour signals: {len(A)} across {A['tag'].nunique()} days")
    feats = []
    missing_days = 0
    for tag, g in A.groupby("tag"):
        day = load_day(tag)
        if day is None:
            missing_days += 1
            continue
        for _, r in g.iterrows():
            f = straddle_and_skew(day, r["min_et"])
            if f is None:
                continue
            feats.append(dict(sym=r["sym"], entry_time=r["entry_time"],
                              exit_time=r["exit_time"], pnl=r["pnl"], **f))
    F = pd.DataFrame(feats)
    F.to_parquet(BASE / "data/surface_features.parquet")
    print(f"features: {len(F)}  (days without 0DTE chain: {missing_days})")

    from stats_tools import nw_tstat
    END = pd.Timestamp("2022-12-31", tz="UTC")
    for col in ("iv", "skew"):
        X = F.dropna(subset=[col]).copy()
        X["q"] = pd.qcut(X[col], 4, labels=["Q1(low)", "Q2", "Q3", "Q4(high)"])
        print(f"\n--- by {col} quartile (n={len(X)}) ---")
        for q, g in X.groupby("q", observed=True):
            t, _ = nw_tstat(g["pnl"].values, lags=3)
            h1 = g[g["exit_time"] <= END]["pnl"].mean()
            h2 = g[g["exit_time"] > END]["pnl"].mean()
            print(f"  {q:<9} n={len(g):>5}  mean ${g['pnl'].mean():>7.2f}  "
                  f"t={t:>5.2f}  halves ${h1:.0f}/${h2:.0f}")


if __name__ == "__main__":
    main()
