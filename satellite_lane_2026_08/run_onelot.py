"""1-contract core sizing variants per PREREG_ONELOT.md.

Capital measurement, not an edge hunt. Rebuilds the weekly weight matrix
per variant, re-runs the core lane AND the deployed-geometry satellite,
and reports drawdown / worst year / satellite trade count so the funding
number is measured rather than divided by three.
"""
import sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
src = open(HERE / "run_subhourly_exhaust.py").read()
# everything up to the sweep: native/M1/rg/strategy/HFRAME/day_sat/dd helpers
exec(src[:src.index('run("ANCHOR")')])

from rotation import monday_of_week, weights_for                # noqa: E402
from rotation_grid import trailing_returns                      # noqa: E402

MONDAYS = list(W.index)


def build_weights(kind):
    """kind: BASE (3-0-0 + 1-1-1 unsure) | V1 one_always | V2 one_unsure111
    | V3 one_flat."""
    rows = {}
    for mon in MONDAYS:
        rets = trailing_returns(daily, mon, 35)
        vals = sorted(rets.values(), reverse=True)
        valid = all(v == v for v in rets.values())
        spread_pp = (vals[0] - vals[1]) * 100 if valid else float("nan")
        unsure = (not valid) or spread_pp < 1.0
        rank = sorted(rets, key=lambda s: rets[s], reverse=True)
        if kind == "BASE":
            rows[mon] = weights_for(rets, "aggressive", 1.0)["weights"]
        elif kind == "V1":                       # 1-0-0 always
            rows[mon] = ({s: 1 for s in rets} if not valid
                         else {rank[0]: 1, rank[1]: 0, rank[2]: 0})
        elif kind == "V2":                       # 1-0-0, 1-1-1 when unsure
            rows[mon] = ({s: 1 for s in rets} if unsure
                         else {rank[0]: 1, rank[1]: 0, rank[2]: 0})
        elif kind == "V3":                       # 1-0-0, FLAT when unsure
            rows[mon] = ({s: 0 for s in rets} if unsure
                         else {rank[0]: 1, rank[1]: 0, rank[2]: 0})
    return pd.DataFrame(rows).T.sort_index()


def core_for(Wv):
    out = []
    for sym in M1:
        wq = lambda ts, s=sym: (int(Wv.loc[monday_of_week(ts), s])
                                if monday_of_week(ts) in Wv.index else 1)
        T = rg.simulate_weighted(native(sym, "60min"), sym, wq)
        T = T[T["qty"] > 0].copy()
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        out.append(T)
    return pd.concat(out)


def expo_for(C):
    return (pd.concat([
        pd.Series(C["qty"].to_numpy(), index=pd.DatetimeIndex(C["entry_time"])),
        pd.Series(-C["qty"].to_numpy(), index=pd.DatetimeIndex(C["exit_time"]))
    ]).groupby(level=0).sum().sort_index().cumsum())


def peak_contracts(C):
    """Max concurrent CORE contracts (drives margin)."""
    return int(expo_for(C).max())


results = []
for kind in ("BASE", "V1", "V2", "V3"):
    Wv = build_weights(kind)
    # rebuild the globals day_sat()/benched() read at call time
    globals()["W"] = Wv
    globals()["WROWS"] = {m: Wv.loc[m] for m in Wv.index}
    C = core_for(Wv)
    globals()["EXPO"] = expo_for(C)
    ct = C[(C["exit_time"] >= "2011-01-01") & (C["exit_time"] < "2026-01-01")]
    cpnl = pd.Series(ct["pnl"].values,
                     index=pd.DatetimeIndex(ct["exit_time"])).sort_index()
    # satellite at the DEPLOYED frozen geometry
    S = pd.concat([day_sat(s, cutoff=15) for s in M1])
    S25 = S[S["exit_time"] < "2026-01-01"]
    sp = pd.Series(S25["pnl"].values,
                   index=pd.DatetimeIndex(S25["exit_time"])).sort_index()
    both = pd.concat([ct, S25.assign(qty=1)])
    dd = daily_mtm_dd(both)
    tot_c, tot_s = float(cpnl.sum()), float(sp.sum())
    comb = pd.concat([cpnl, sp]).sort_index()
    yr = comb.groupby(comb.index.year).sum()
    unsure_wks = int(sum(1 for m in MONDAYS
                         if len(set(int(v) for v in Wv.loc[m])) == 1))
    r = dict(cell=kind, core=round(tot_c), sat=round(tot_s),
             total=round(float(comb.sum())), maxDD=round(dd),
             ratio=round(float(comb.sum()) / abs(dd), 2),
             worst=round(float(yr.min())), worst_yr=int(yr.idxmin()),
             sat_n=len(S25), peak_core_ct=peak_contracts(ct),
             h1=round(float(comb[:"2017"].sum())),
             h2=round(float(comb["2018":].sum())),
             y26=round(float(pd.concat([
                 C[C["exit_time"] >= "2026-01-01"]["pnl"],
                 S[S["exit_time"] >= "2026-01-01"]["pnl"]]).sum())),
             flat_wks=unsure_wks)
    results.append(r)
    print(f"{kind:5} core {r['core']:>7,} sat {r['sat']:>6,} "
          f"total {r['total']:>7,} DD {r['maxDD']:>7,} ratio {r['ratio']:>5} "
          f"worst {r['worst']:>7,}({r['worst_yr']}) satN {r['sat_n']:>4} "
          f"peakCt {r['peak_core_ct']} 26 {r['y26']:>6,}", flush=True)

D = pd.DataFrame(results)
D.to_csv(HERE / "onelot_cells.csv", index=False)
print()
print("=== CAPITAL ESTIMATE (margin uses peak concurrent contracts) ===")
MGN = {1: 2300, 2: 4600, 3: 6900}          # ~MNQ overnight per contract
for r in results:
    margin = MGN.get(r["peak_core_ct"], r["peak_core_ct"] * 2300) + 1300
    print(f"{r['cell']:5} peak {r['peak_core_ct']} core ct -> margin ~${margin:,}"
          f" + observed DD ${abs(r['maxDD']):,} = floor ${margin+abs(r['maxDD']):,}"
          f"  | pace ${r['total']/15:,.0f}/yr")
print("DONE")
