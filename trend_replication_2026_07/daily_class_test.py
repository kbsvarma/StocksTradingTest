"""Extension 2 Q2: classic daily-bar Donchian+trail on the owned 14-asset basket.
Zero new data cost. Roll-honest (within-instrument returns via roll force-close),
gap-honest fills, $3.50 micro-equivalent RT friction (scaled specs as tsmom).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "event_edge_research"))
from stats_tools import nw_tstat

# micro-equivalent multipliers (as tsmom_tests SPECS where micros exist)
SPECS = {"ES": 5, "NQ": 2, "RTY": 5, "ZN": 1000, "ZF": 1000, "GC": 10, "SI": 1000,
         "HG": 25000, "CL": 100, "NG": 2500, "6E": 12500, "6J": 12500000 / 10,
         "6B": 62500, "6A": 100000}
FR = 3.50
END = {"h1": pd.Timestamp("2018-12-31", tz="UTC")}  # daily test halves: 2010-2018 / 2019-2026

def load():
    df = pd.read_parquet("../tsmom_research_2026_07/data/basket_1d_2010_2026.parquet")
    df = df.rename(columns=str.lower)
    df["sym"] = df["symbol"].str.replace(r"\.v\.0", "", regex=True)
    return df

def run(g, mult, N, K, long_only):
    g = g.sort_index()
    o = g["open"].values; h = g["high"].values; l = g["low"].values; c = g["close"].values
    roll = (g["instrument_id"] != g["instrument_id"].shift()).values
    tr_ = np.maximum.reduce([h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))])
    atr = pd.Series(tr_).rolling(20).mean().values
    hi = pd.Series(c).rolling(N).max().shift(1).values
    lo = pd.Series(c).rolling(N).min().shift(1).values
    idx = g.index
    trades = []; pos = 0; e = hw = st = sa = np.nan; pend = 0
    for i in range(len(g)):
        if pend != 0:
            pos = pend; e = o[i]; hw = o[i]; sa = atr[i - 1]
            st = e - pos * K * sa
            pend = 0
            if np.isnan(st): pos = 0
        if pos != 0 and roll[i]:
            x = c[i - 1] if i > 0 else o[i]
            trades.append((idx[i], pos * (x - e) * mult - FR)); pos = 0
        if pos != 0:
            hit = (l[i] <= st) if pos == 1 else (h[i] >= st)
            if hit:
                x = min(st, o[i]) if pos == 1 else max(st, o[i])
                trades.append((idx[i], pos * (x - e) * mult - FR)); pos = 0
            else:
                if pos == 1 and h[i] > hw: hw = h[i]
                if pos == -1 and l[i] < hw: hw = l[i]
                st = max(st, hw - K * atr[i]) if pos == 1 else min(st, hw + K * atr[i])
        if pos == 0 and pend == 0 and i > N and not np.isnan(atr[i]):
            if not np.isnan(hi[i]) and c[i] > hi[i]: pend = 1
            elif not long_only and not np.isnan(lo[i]) and c[i] < lo[i]: pend = -1
    return pd.Series(dict(trades)) if trades else pd.Series(dtype=float)

def main():
    df = load()
    rows = []
    for sym, g in df.groupby("sym"):
        mult = SPECS.get(sym)
        if mult is None: continue
        for N in (20, 55):
            for K in (2.0, 3.0):
                for lo_ in (True, False):
                    tr = run(g, mult, N, K, lo_)
                    if len(tr) < 20: continue
                    tr.index = pd.DatetimeIndex(tr.index)
                    t, _ = nw_tstat(tr.values, lags=3)
                    h1 = tr[tr.index <= END["h1"]]; h2 = tr[tr.index > END["h1"]]
                    rows.append(dict(sym=sym, N=N, K=K, dirn="L" if lo_ else "LS",
                                     n=len(tr), total=tr.sum(), t=t,
                                     h1=h1.sum(), h2=h2.sum()))
    R = pd.DataFrame(rows)
    R.to_parquet("daily_class_cells.parquet")
    print("cells passing (both halves >0, t>=2):")
    ok = R[(R.h1 > 0) & (R.h2 > 0) & (R.t >= 2)]
    cols = ["sym", "N", "K", "dirn", "n", "total", "t", "h1", "h2"]
    print(ok[cols].round(0).to_string(index=False) if len(ok) else "  NONE")
    print("\nbest cell per symbol (by total):")
    best = R.loc[R.groupby("sym")["total"].idxmax()]
    print(best[cols].round(0).sort_values("total", ascending=False).to_string(index=False))

if __name__ == "__main__":
    main()
