"""Filter lab per the locked acceptance cage:
accept only if (a) maxDD improves on >=2/3 instruments, (b) basket total >=85%
of baseline, (c) DD improvement present in both calendar halves, (d) all logged.
Baseline = 60m N20 K2.0 long-only + volgate(95th). Max 2 filters in final stack.
Participation filters (F6-F8) mask trades on the SAME signal timeline (documented
approximation: skipped trades zeroed, sequence unchanged).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
from trend_test import load_fut, bars, atr
from breadth_test import load_parq_dir

FR = 3.50
END22 = pd.Timestamp("2022-12-31", tz="UTC")

def engine(B, A, mult, N=20, K=2.0, volgate=0.95,
           regime_ma=False, vol_tighten=False, breakeven=False,
           session_skip=False, confirm2=False):
    hi = B.c.rolling(N).max().shift(1)
    o = B.o.values; h = B.h.values; l = B.l.values; c = B.c.values
    roll = B.roll.values; hiv = hi.values; av = A.values
    ap_s = (A / B.c)
    th = ap_s.rolling(24 * 365, min_periods=24 * 60).quantile(volgate).values
    ap = ap_s.values
    # daily 200d MA regime (ex-ante: computed on prior days' closes)
    if regime_ma:
        dcl = B.c.resample("1D").last().dropna()
        ma200 = dcl.rolling(200).mean().shift(1)
        ma_map = ma200.reindex(B.index.normalize()).values
        cl_map = dcl.shift(1).reindex(B.index.normalize()).values
        regime_ok = (cl_map > ma_map)
    hours = B.index.tz_convert("America/New_York").hour
    idx = B.index
    trades = []
    pos = 0; entry = best = stop = 0.0; pend = 0; atr0 = 0.0
    for i in range(len(B)):
        if pend != 0:
            pos = pend; entry = o[i]; best = o[i]; atr0 = av[i - 1]
            stop = entry - K * atr0 if not np.isnan(atr0) else np.nan
            pend = 0
            if np.isnan(stop): pos = 0
        if pos != 0 and roll[i] == 1:
            px = c[i - 1] if i > 0 else o[i]
            trades.append((idx[i], (px - entry) * mult - FR, entry)); pos = 0
        if pos != 0:
            if l[i] <= stop:
                px = min(stop, o[i])
                trades.append((idx[i], (px - entry) * mult - FR, entry)); pos = 0
            else:
                Keff = K
                if vol_tighten and not np.isnan(th[i]) and ap[i] > th[i]:
                    Keff = K * 0.7
                if h[i] > best:
                    best = h[i]
                if pos != 0:
                    stop = max(stop, best - Keff * av[i])
                    if breakeven and best >= entry + atr0:
                        stop = max(stop, entry)
        if pos == 0 and pend == 0 and i > N:
            sig = (not np.isnan(hiv[i])) and c[i] > hiv[i]
            if sig and confirm2:
                sig = i > 0 and (not np.isnan(hiv[i - 1])) and c[i - 1] > hiv[i - 1]
            if sig and (not np.isnan(th[i])) and ap[i] > th[i]:
                sig = False
            if sig and session_skip and 2 <= hours[i] < 6:
                sig = False
            if sig and regime_ma and not (regime_ok[i] == True):
                sig = False
            if sig:
                pend = 1
    if not trades:
        return pd.DataFrame(columns=["pnl"])
    T = pd.DataFrame(trades, columns=["t", "pnl", "entry"]).set_index("t")
    return T

def mask_throttle(T, n_losses=3):
    keep = []; streak = 0; live = True
    for _, r in T.iterrows():
        if live:
            keep.append(True)
            streak = streak + 1 if r.pnl < 0 else 0
            if streak >= n_losses: live = False
        else:
            keep.append(False)             # shadow trade
            if r.pnl > 0: live = True; streak = 0
    return T[pd.Series(keep, index=T.index)]

def mask_eqcurve(T, n=50):
    eq = T.pnl.cumsum(); ma = eq.rolling(n).mean()
    ok = (eq.shift(1) > ma.shift(1)) | ma.shift(1).isna()
    return T[ok]

def stats(T):
    if len(T) == 0: return dict(total=0, dd=0, dd1=0, dd2=0, tot1=0, tot2=0)
    p = T.pnl
    eq = p.cumsum(); dd = float((eq - eq.cummax()).min())
    h1 = p[p.index <= END22]; h2 = p[p.index > END22]
    def mdd(x):
        if len(x) == 0: return 0.0
        e = x.cumsum(); return float((e - e.cummax()).min())
    return dict(total=float(p.sum()), dd=dd, dd1=mdd(h1), dd2=mdd(h2),
                tot1=float(h1.sum()), tot2=float(h2.sum()))

def main():
    data = []
    for inst, loader, mult in [("NQ", lambda: load_fut("NQ", 2.0)[0], 2.0),
                               ("ES", lambda: load_fut("ES", 5.0)[0], 5.0),
                               ("YM", lambda: load_parq_dir("data/YM_1m_*.parquet"), 0.50)]:
        df = loader(); B = bars(df, 60); A = atr(B, 20)
        data.append((inst, B, A, mult))

    FILTERS = [
        ("baseline", {}),
        ("F1_regimeMA", dict(regime_ma=True)),
        ("F2_volTightenTrail", dict(vol_tighten=True)),
        ("F3_breakeven+1ATR", dict(breakeven=True)),
        ("F4_skip2-6am", dict(session_skip=True)),
        ("F5_confirm2bar", dict(confirm2=True)),
    ]
    results = {}
    base_T = {}
    for name, kw in FILTERS:
        row = {}
        for inst, B, A, mult in data:
            T = engine(B, A, mult, **kw)
            T = T[T.index >= pd.Timestamp("2016-01-01", tz="UTC")]
            if name == "baseline": base_T[inst] = T
            row[inst] = stats(T)
        results[name] = row
    # participation masks on baseline trades
    for name, fn in (("F7_chopThrottle", mask_throttle), ("F8_eqCurve", mask_eqcurve)):
        row = {}
        for inst, B, A, mult in data:
            row[inst] = stats(fn(base_T[inst]))
        results[name] = row
    # F6 concurrency cap: chronological mask across instruments (needs entry times = index here is exit; approximation uses exit-time ordering — documented)
    # report
    def prow(name, row):
        tot = sum(row[i]["total"] for i in ("NQ", "ES", "YM"))
        dds = [row[i]["dd"] for i in ("NQ", "ES", "YM")]
        base = results["baseline"]
        dd_better = sum(1 for i in ("NQ", "ES", "YM") if row[i]["dd"] > base[i]["dd"] + 1)
        tot_base = sum(base[i]["total"] for i in ("NQ", "ES", "YM"))
        h1ok = sum(1 for i in ("NQ","ES","YM") if row[i]["dd1"] >= base[i]["dd1"])
        h2ok = sum(1 for i in ("NQ","ES","YM") if row[i]["dd2"] >= base[i]["dd2"])
        print(f"{name:<20} total ${tot:>8,.0f} ({tot/tot_base*100 if tot_base else 0:>4.0f}%)  "
              f"DD: NQ {dds[0]:>7,.0f} ES {dds[1]:>7,.0f} YM {dds[2]:>7,.0f}  "
              f"ddBetter {dd_better}/3  halves {h1ok}/{h2ok}")
    print(f"{'filter':<20} {'basket total':>20}  per-instrument maxDD")
    for name in results:
        prow(name, results[name])

if __name__ == "__main__":
    main()
