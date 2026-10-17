"""Flat-day persistence base rates per PREREG_FLATDAY.md.

Premise test only: does "flat so far at T" predict "never touches +/-X%
between T and 16:00"? Reported as LIFT against a TIME-MATCHED control
(unconditional rate over the same remaining window), because part of any
naive lift is just fewer hours left, not regime persistence.
"""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reentry_regime_2026_07"))
import engine_v as ev                                          # noqa: E402
rg = ev.rg

print("loading ES 1-min...", flush=True)
files = sorted(glob.glob(str(rg.SRC["ES"])))
df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
df = df[~df.index.duplicated(keep="last")]
df = df[["open", "high", "low", "close"]]
et = df.index.tz_convert("America/New_York")
df = df.assign(_d=et.date, _h=et.hour, _m=et.minute)
rth = df[((df["_h"] > 9) | ((df["_h"] == 9) & (df["_m"] >= 30))) &
         (df["_h"] < 16)]
print(f"RTH 1-min rows: {len(rth):,}", flush=True)

VIX = pd.read_parquet(HERE.parent / "bps_rederive_2026_07/data/vix_daily_cboe.parquet")
VIX.index = pd.to_datetime(VIX.index).date
vix_close = VIX["close"]
vix_chg = VIX["close"].pct_change()

days = []
for d, g in rth.groupby("_d"):
    if len(g) < 200:                       # half-days / holidays
        continue
    days.append((d, g))
print(f"sessions: {len(days):,}", flush=True)

TS = [(11, 0), (12, 0), (13, 0)]
XS = [0.005, 0.010, 0.015]
rows = []
for hh, mm in TS:
    recs = []
    for d, g in days:
        pre = g[(g["_h"] < hh) | ((g["_h"] == hh) & (g["_m"] < mm))]
        post = g[(g["_h"] > hh) | ((g["_h"] == hh) & (g["_m"] >= mm))]
        if len(pre) < 30 or len(post) < 30:
            continue
        op = float(pre["open"].iloc[0])
        spot = float(pre["close"].iloc[-1])
        rng = (float(pre["high"].max()) - float(pre["low"].min())) / op
        move = abs(spot / op - 1.0)
        hi = float(post["high"].max()); lo = float(post["low"].min())
        up = hi / spot - 1.0; dn = 1.0 - lo / spot
        recs.append(dict(d=d, op=op, spot=spot, rng=rng, move=move,
                         up=up, dn=dn, exc=max(up, dn)))
    R = pd.DataFrame(recs).sort_values("d").reset_index(drop=True)
    # REL classifier: bottom tercile of trailing-60 own distribution
    R["rng_q"] = R["rng"].rolling(60).apply(
        lambda w: (w[-1] <= np.quantile(w[:-1], 1/3)) * 1.0, raw=True)
    R["mv_q"] = R["move"].rolling(60).apply(
        lambda w: (w[-1] <= np.quantile(w[:-1], 1/3)) * 1.0, raw=True)
    R["ABS"] = (R["move"] < 0.0025) & (R["rng"] < 0.0050)
    R["REL"] = (R["rng_q"] == 1) & (R["mv_q"] == 1)
    R["vix"] = R["d"].map(vix_close)
    R["vchg"] = R["d"].map(vix_chg)
    R["VIXOK"] = R["vix"].between(12, 25) & (R["vchg"].abs() < 0.05)
    for X in XS:
        R["safe"] = R["exc"] < X
        base = float(R["safe"].mean())          # time-matched control:
        # SAME remaining window for everyone, so clock effects cancel
        for name in ("ABS", "REL"):
            sel = R[R[name].fillna(False)]
            if len(sel) < 30:
                continue
            cond = float(sel["safe"].mean())
            # VIX-steady subset
            selv = sel[sel["VIXOK"].fillna(False)]
            condv = float(selv["safe"].mean()) if len(selv) >= 30 else np.nan
            rows.append(dict(T=f"{hh:02d}:{mm:02d}", X=f"{X*100:.1f}%",
                             clf=name, n=len(sel), n_all=len(R),
                             base=round(base*100, 1), cond=round(cond*100, 1),
                             lift=round((cond-base)*100, 1),
                             n_vix=len(selv),
                             cond_vix=round(condv*100, 1) if condv == condv else None,
                             lift_vix=(round((condv-base)*100, 1)
                                       if condv == condv else None),
                             med_exc_break=round(
                                 float(sel[~sel["safe"]]["exc"].median())*100, 2)
                             if (~sel["safe"]).any() else None))
    if (hh, mm) == (12, 0):
        R.to_csv(HERE / "flatday_T1200.csv", index=False)

D = pd.DataFrame(rows)
D.to_csv(HERE / "flatday_cells.csv", index=False)
pd.set_option("display.width", 200)
print()
print("=== FLAT-DAY PERSISTENCE (base vs conditional, time-matched) ===")
print(D.to_string(index=False))
print()
print("=== LIFT SUMMARY at the +/-1.0% band (IC-relevant) ===")
print(D[D["X"] == "1.0%"][["T", "clf", "n", "base", "cond", "lift",
                           "cond_vix", "lift_vix",
                           "med_exc_break"]].to_string(index=False))
print("DONE")
