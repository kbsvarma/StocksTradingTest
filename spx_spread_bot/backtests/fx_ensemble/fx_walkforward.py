"""FX ensemble — walk-forward with cross-pair robustness (the real test).

The blueprint's claim: edge comes from the discovery+validation process.
So this is the honest test of that claim.

Anti-overfit design:
  - POOL all 21 pairs. Optimize ONE global parameter set (not per-pair).
    A signal that needs different params per pair is overfit; a real edge
    works with shared params across many pairs.
  - Walk-forward: optimize on IN-SAMPLE window, measure on the UNTOUCHED
    OOS window that follows. Roll forward. Aggregate OOS = honest edge.
  - Pick in-sample winner by expectancy (with min-trade floor), then report
    its OOS expectancy. The gap between IS and OOS = the overfit tax.

Reports IS vs OOS expectancy per window + pooled OOS across 22 years.
If pooled OOS expectancy > 0 and broad across pairs -> real (small) edge.
If OOS collapses vs IS -> it was overfit (honest finding either way).
"""
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product
import sys
sys.path.insert(0, str(Path(__file__).parent))
from fx_harness import build_signal, pip_size, spread_pips, DATA

PAIRS = sorted(p.stem for p in DATA.glob("*.parquet"))

# preload all pair frames once
FRAMES = {}
for p in PAIRS:
    d = pd.read_parquet(DATA / f"{p}.parquet")
    d = d[(d[["Open","High","Low","Close"]] > 0).all(axis=1)].copy()
    FRAMES[p] = d


def backtest_window(pair, df, params, tp_pips, sl_pips, max_hold, lo, hi):
    """Backtest one pair restricted to index range [lo, hi). Returns pnl_pips list."""
    ps = pip_size(pair); spr = spread_pips(pair) * ps
    sig = build_signal(df, params).values
    opens = df["Open"].values; highs = df["High"].values
    lows = df["Low"].values; closes = df["Close"].values
    n = len(df); out = []
    i = max(lo, 60)  # warmup
    end = min(hi, n - 1)
    while i < end:
        s = sig[i]
        if s == 0: i += 1; continue
        ei = i + 1
        entry = opens[ei] + (spr/2 if s == 1 else -spr/2)
        if s == 1: tp = entry + tp_pips*ps; sl = entry - sl_pips*ps
        else:      tp = entry - tp_pips*ps; sl = entry + sl_pips*ps
        ex = None
        for j in range(ei, min(ei+max_hold, n)):
            if s == 1:
                if lows[j] <= sl: ex = sl; break
                if highs[j] >= tp: ex = tp; break
            else:
                if highs[j] >= sl: ex = sl; break
                if lows[j] <= tp: ex = tp; break
        if ex is None: ex = closes[min(ei+max_hold-1, n-1)]
        out.append(((ex-entry)/ps)*s)
        i = j + 1
    return out


def pooled_expectancy(params, tp, sl, max_hold, lo_frac, hi_frac):
    """Expectancy pooled across all pairs over a fractional date window."""
    allp = []
    for p in PAIRS:
        df = FRAMES[p]; n = len(df)
        lo = int(n*lo_frac); hi = int(n*hi_frac)
        allp.extend(backtest_window(p, df, params, tp, sl, max_hold, lo, hi))
    if len(allp) < 30:
        return None, 0
    return float(np.mean(allp)), len(allp)


# modest grid (anti-overfit: keep dimensionality low)
GRID = []
for don_n in (10, 20, 40):
    for mom in ((10,40),(20,60)):
        for mr_z in (1.5, 2.0):
            for vote in (1, 2):
                for tp in (20, 30, 40):
                    for sl in (30, 45, 60):
                        GRID.append(dict(
                            params=dict(don_n=don_n, mom_fast=mom[0], mom_slow=mom[1],
                                        mr_n=20, mr_z=mr_z, atr_n=14,
                                        vol_lo=0.2, vol_hi=0.95, vote_min=vote),
                            tp=tp, sl=sl))
print(f"Grid size: {len(GRID)} combos | {len(PAIRS)} pairs", flush=True)

# walk-forward windows over the fractional timeline (pairs have ~same span)
# train 4 'units', test 2, roll 2 (units = 1/11 of history ~ 2yr each)
U = 1/11
windows = []
f = 0.0
while f + 6*U <= 1.0001:
    windows.append((f, f+4*U, f+4*U, f+6*U))  # (is_lo,is_hi, oos_lo,oos_hi)
    f += 2*U
print(f"Walk-forward windows: {len(windows)}", flush=True)

print("\n" + "="*78)
print("WALK-FORWARD: optimize pooled IS, measure pooled OOS (honest edge test)")
print("="*78)
print(f"{'Window':<8}{'IS exp':<10}{'OOS exp':<10}{'OOS N':<8}{'chosen tp/sl':<14}{'don/vote':<10}")
print("-"*70)

oos_all = []
for wi, (islo, ishi, ooslo, ooshi) in enumerate(windows):
    best = None
    for g in GRID:
        e, n = pooled_expectancy(g["params"], g["tp"], g["sl"], 10, islo, ishi)
        if e is None or n < 200: continue
        if best is None or e > best[0]:
            best = (e, g, n)
    if best is None:
        continue
    is_exp, g, _ = best
    oos_exp, oos_n = pooled_expectancy(g["params"], g["tp"], g["sl"], 10, ooslo, ooshi)
    if oos_exp is None: continue
    oos_all.append((oos_exp, oos_n))
    pr = g["params"]
    print(f"{wi:<8}{is_exp:<+9.2f}{oos_exp:<+9.2f}{oos_n:<8}"
          f"{str(g['tp'])+'/'+str(g['sl']):<14}{str(pr['don_n'])+'/'+str(pr['vote_min']):<10}",
          flush=True)

# aggregate OOS
if oos_all:
    tot_n = sum(n for _, n in oos_all)
    w_exp = sum(e*n for e, n in oos_all) / tot_n
    print("-"*70)
    print(f"\nPOOLED OUT-OF-SAMPLE expectancy: {w_exp:+.2f} pips/trade  over {tot_n} OOS trades")
    print(f"OOS windows positive: {sum(1 for e,_ in oos_all if e>0)}/{len(oos_all)}")
    print("\nHONEST READ:")
    if w_exp > 1:
        print("  OOS expectancy positive & meaningful -> possible real edge, dig deeper.")
    elif w_exp > 0:
        print("  OOS marginally positive -> fragile; likely eaten by swap/slippage.")
    else:
        print("  OOS <= 0 -> the IS optimization did NOT generalize (overfit). As expected for")
        print("  a small indicator grid; real edge (if any) needs better signals, not more fitting.")
