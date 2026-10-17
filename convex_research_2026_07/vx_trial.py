"""Stage 1 — VX daily trend cells per PREREG_CONVEX.md.

VXM micro economics: $100/pt, tick 0.05, honest friction $8.00 RT.
Roll force-close (the contango decay lives in the roll — never pocketed).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
MULT = 100.0
FR = 8.00
CORE = pd.Series({2019: 987, 2020: 11906, 2021: 12617, 2022: 280,
                  2023: 3580, 2024: 10811, 2025: 11857, 2026: 2767})


def load_vx():
    df = pd.read_parquet(BASE / "data/vx_1d_v0_v1.parquet")
    front = df[df["symbol"] == "VX.v.0"].copy()
    front["roll"] = (front["instrument_id"] != front["instrument_id"].shift()).astype(int)
    return front


def run_cell(g, N, K, long_only=True):
    o, h, l, c = (g[x].values for x in ("open", "high", "low", "close"))
    roll = g["roll"].values
    pc = np.roll(c, 1)
    tr_ = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = pd.Series(tr_).rolling(20).mean().values
    hi = pd.Series(c).rolling(N).max().shift(1).values
    ts = g.index
    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    for i in range(len(g)):
        if pend:
            pos, pend = 1, 0
            e, hw = o[i], o[i]
            st = e - K * atr[i - 1]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos and roll[i]:
            x = c[i - 1] if i > 0 else o[i]
            trades.append((e_ts, ts[i], (x - e) * MULT - FR, "roll"))
            pos = 0
        if pos:
            if l[i] <= st:
                x = min(st, o[i])
                trades.append((e_ts, ts[i], (x - e) * MULT - FR, "stop"))
                pos = 0
            else:
                hw = max(hw, h[i])
                st = max(st, hw - K * atr[i])
        if pos == 0 and pend == 0 and i > N and not np.isnan(atr[i]):
            if not np.isnan(hi[i]) and c[i] > hi[i]:
                pend = 1
    return pd.DataFrame(trades, columns=["entry", "exit", "pnl", "reason"])


def main():
    g = load_vx()
    print(f"VX.v.0 daily: {len(g)} rows  {g.index.min().date()} -> "
          f"{g.index.max().date()}  rolls: {int(g['roll'].sum())}")
    for N in (20, 55):
        for K in (2.0, 3.0):
            T = run_cell(g, N, K)
            p = pd.Series(T["pnl"].values, index=pd.DatetimeIndex(T["exit"]))
            y = p.groupby(p.index.year).sum()
            corr = y.reindex(CORE.index).fillna(0).corr(CORE)
            med_dorm = y[y < 0].median() if (y < 0).any() else 0
            tail = y.max()
            print(f"\nN{N} K{K}: n={len(T)}  total ${p.sum():,.0f}  "
                  f"corr(core)={corr:+.2f}  tail ${tail:,.0f} vs median-dormant "
                  f"${med_dorm:,.0f}")
            print("  yearly:", {int(k): int(v) for k, v in y.items()})


if __name__ == "__main__":
    main()
