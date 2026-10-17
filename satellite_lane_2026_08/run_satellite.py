"""Two-lane satellite study — gate zero, 11 cells, gates, ledger inputs.

Reuses the verified variant FSM (engine_v.simulate_v with h1=h2=None is
trade-identical to rotation_grid.simulate_weighted); the satellite enters
purely through the week_qty callable: weekly weight when >0, else 1 when
the benched-qualification passes at that bar.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reentry_regime_2026_07"))
import engine_v as ev                                    # noqa: E402
rg = ev.rg
from rotation import monday_of_week                      # noqa: E402

WINDOWS = {"8h": 8, "1d": 24, "3d": 69, "5d": 115, "10d": 230}

print("loading roll-aware bars...", flush=True)
bars, daily = {}, {}
for sym in ("NQ", "ES", "YM"):
    B = rg.load_60m(sym)
    bars[sym] = B
    daily[sym] = B["close"].resample("1D").last().dropna()
rg.START = pd.Timestamp("2011-01-01", tz="UTC")
W = rg.weekly_weights(daily, 35, "aggressive300",
                      min(b.index.min() for b in bars.values()),
                      max(b.index.max() for b in bars.values()))
FEATS = {s: ev.precompute(bars[s], rg.SCFG) for s in bars}

# trailing W-bar returns for every sym, aligned (causally, ffill) onto each
# symbol's own bar index so the callable can compare cross-instrument
RET = {}          # RET[sym][wname] = DataFrame indexed by sym's bars, 3 cols
for sym in bars:
    idx = bars[sym].index
    RET[sym] = {}
    for wname, wbars in WINDOWS.items():
        cols = {}
        for other in bars:
            r = bars[other]["close"].pct_change(wbars, fill_method=None)
            cols[other] = (r if other == sym
                           else r.reindex(idx, method="ffill"))
        RET[sym][wname] = pd.DataFrame(cols, index=idx)

WROWS = {m: W.loc[m] for m in W.index}


def make_wq(sym, wname=None, rule=None, satellite=False):
    """weekly weight when allocated; else satellite 1-lot if qualified."""
    ret = RET[sym][wname] if wname else None

    def wq(ts):
        mon = monday_of_week(ts)
        row = WROWS.get(mon)
        if row is None:
            return 1
        wgt = int(row[sym])
        if wgt > 0:
            return wgt
        benched = [s for s in bars if int(row[s]) == 0]
        if sym not in benched or len(benched) == len(bars):
            return 0
        if not satellite:                      # pure baseline: lane 2 off
            return 0
        if rule is None:                       # control C0
            return 1
        r = ret.loc[:ts].iloc[-1]
        mine = r[sym]
        if not np.isfinite(mine) or mine <= 0:
            return 0
        if rule == "Q1":
            return 1
        others = [r[s] for s in benched if s != sym and np.isfinite(r[s])]
        return 1 if all(mine >= o for o in others) else 0
    return wq


def daily_mtm_dd(all_trades, lo, hi):
    """Daily-close MTM equity from booked trades + open marks."""
    days = pd.date_range(lo, hi, freq="D", tz="UTC")
    eq = pd.Series(0.0, index=days)
    for sym in bars:
        T = all_trades[all_trades["sym"] == sym]
        dc = daily[sym].reindex(days, method="ffill")
        for tr in T.itertuples():
            mult, q = rg.MULT[sym], tr.qty
            live = (days >= tr.entry_time.normalize()) & (days < tr.exit_time.normalize())
            eq[live] += (dc[live] - tr.entry_px) * mult * q
            eq[days >= tr.exit_time.normalize()] += (
                (tr.exit_px - tr.entry_px) * mult * q - rg.FR * q)
    dd = float((eq - eq.cummax()).min())
    return dd


def run(cell, wname=None, rule=None, satellite=True):
    out = []
    for sym in bars:
        T = ev.simulate_v(bars[sym], sym,
                          make_wq(sym, wname, rule, satellite),
                          FEATS[sym])
        T = T[T["qty"] > 0].copy()
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        out.append(T)
    A = pd.concat(out).sort_values("exit_time").reset_index(drop=True)
    P = A[(A["exit_time"] >= "2011-01-01") & (A["exit_time"] < "2026-01-01")]
    pnl = pd.Series(P["pnl"].values, index=pd.DatetimeIndex(P["exit_time"]))
    yearly = pnl.groupby(pnl.index.year).sum()
    dd = daily_mtm_dd(P, "2011-01-01", "2025-12-31")
    full = float(A[A["exit_time"] >= "2011-01-01"]["pnl"].sum())
    sat = P[P["qty"] == 1]          # 1-lot trades ≈ satellite (weekly never 1
                                    # in 3-0-0 weeks; 1-1-1 fallback weeks DO
                                    # produce qty=1 core trades — reported)
    return dict(cell=cell, total=round(float(pnl.sum())),
                maxDD=round(dd),
                ratio=round(float(pnl.sum()) / abs(dd), 2),
                worst_yr=round(float(yearly.min())),
                worst_lbl=int(yearly.idxmin()),
                trades=len(P), q1_trades=len(sat),
                h1=round(float(pnl[:"2017"].sum())),
                h2=round(float(pnl["2018":].sum())),
                full=round(full))


print("gate zero (satellite off must equal verified baseline)...", flush=True)
base = run("BASELINE", satellite=False)
print(base, flush=True)
assert abs(base["full"] - 57138) < 2, "GATE ZERO FAILED"

rows = [base]
cells = [("C0_none", None, None)]
cells += [(f"Q1_{w}", w, "Q1") for w in WINDOWS]
cells += [(f"Q2_{w}", w, "Q2") for w in WINDOWS]
for name, wname, rule in cells:
    r = run(name, wname, rule)
    r["d_total"] = r["total"] - base["total"]
    gate = (r["d_total"] > 0 and r["ratio"] >= base["ratio"]
            and r["worst_yr"] >= base["worst_yr"] - 500)
    r["gate"] = "PASS" if gate else ""
    rows.append(r)
    print(f"{name:10} total {r['total']:>7} (Δ{r['d_total']:>+7}) "
          f"ratio {r['ratio']:>6} worst {r['worst_yr']:>6}({r['worst_lbl']}) "
          f"trades {r['trades']:>5} sat/1lot {r['q1_trades']:>5} "
          f"halves {r['h1']}/{r['h2']} {r['gate']}", flush=True)
pd.DataFrame(rows).to_csv(HERE / "cells.csv", index=False)
print("DONE")
