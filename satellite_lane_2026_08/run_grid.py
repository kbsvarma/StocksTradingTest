"""EXHAUSTIVE sizing surface per PREREG_ONELOT wave 3.

12 cells = L{1,2,3} x U{flat, lead1, top2, all3}. Reports core+satellite
and core-only, measured capital E = max_t[margin(t) - cumPnL(t)], and
pace per $1,000 of required capital (the decision metric when capital,
not signal, is the binding constraint).
"""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
src = open(HERE / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])

from rotation import monday_of_week                              # noqa: E402
from rotation_grid import trailing_returns                       # noqa: E402

OVN = {"NQ": 2300.0, "ES": 1600.0, "YM": 1100.0}
INTRA = {k: v * 0.60 for k, v in OVN.items()}
MONDAYS = list(W.index)
NAMED = {(3, "all3"): "BASE", (1, "all3"): "V2", (1, "flat"): "V3",
         (2, "flat"): "V4", (2, "all3"): "V5", (1, "lead1"): "V1"}


def build_weights(L, U):
    rows = {}
    for mon in MONDAYS:
        rets = trailing_returns(daily, mon, 35)
        valid = all(v == v for v in rets.values())
        vals = sorted(rets.values(), reverse=True)
        unsure = (not valid) or (vals[0] - vals[1]) * 100 < 1.0
        rank = sorted(rets, key=lambda s: rets[s], reverse=True)
        if not unsure:
            rows[mon] = {rank[0]: L, rank[1]: 0, rank[2]: 0}
        elif U == "flat":
            rows[mon] = {s: 0 for s in rets}
        elif U == "lead1":
            rows[mon] = {rank[0]: 1, rank[1]: 0, rank[2]: 0}
        elif U == "top2":
            rows[mon] = {rank[0]: 1, rank[1]: 1, rank[2]: 0}
        else:                                        # all3
            rows[mon] = {s: 1 for s in rets}
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


DAYS = pd.date_range("2011-01-01", "2025-12-31", freq="D", tz="UTC")


def capital(core_t, sat_t):
    margin = pd.Series(0.0, index=DAYS)
    for sym in OVN:
        legs = core_t[core_t["sym"] == sym]
        if len(legs):
            q = pd.Series(0.0, index=DAYS)
            for tr in legs.itertuples():
                live = ((DAYS >= pd.Timestamp(tr.entry_time).normalize()) &
                        (DAYS <= pd.Timestamp(tr.exit_time).normalize()))
                q[live] = q[live].clip(lower=float(tr.qty))
            margin += q * OVN[sym]
        if sat_t is not None:
            sl = sat_t[sat_t["sym"] == sym]
            if len(sl):
                sd = pd.DatetimeIndex(sl["entry_time"]).normalize().unique()
                margin[margin.index.isin(sd)] += INTRA[sym]
    parts = [pd.Series(core_t["pnl"].values,
                       index=pd.DatetimeIndex(core_t["exit_time"]).normalize())]
    if sat_t is not None:
        parts.append(pd.Series(
            sat_t["pnl"].values,
            index=pd.DatetimeIndex(sat_t["exit_time"]).normalize()))
    cum = (pd.concat(parts).groupby(level=0).sum()
           .reindex(DAYS, fill_value=0.0).cumsum())
    return float((margin - cum).max())


def stats(ct, st):
    parts = [pd.Series(ct["pnl"].values, index=pd.DatetimeIndex(ct["exit_time"]))]
    if st is not None:
        parts.append(pd.Series(st["pnl"].values,
                               index=pd.DatetimeIndex(st["exit_time"])))
    cp = pd.concat(parts).sort_index()
    dd = daily_mtm_dd(pd.concat([ct] + ([st.assign(qty=1)] if st is not None
                                        else [])))
    yr = cp.groupby(cp.index.year).sum()
    return float(cp.sum()), float(dd), float(yr.min())


rows = []
for L in (1, 2, 3):
    for U in ("flat", "lead1", "top2", "all3"):
        Wv = build_weights(L, U)
        globals()["W"] = Wv
        globals()["WROWS"] = {m: Wv.loc[m] for m in Wv.index}
        C = core_for(Wv)
        globals()["EXPO"] = expo_for(C)
        ct = C[(C["exit_time"] >= "2011-01-01") & (C["exit_time"] < "2026-01-01")]
        S = pd.concat([day_sat(s, cutoff=15) for s in M1])
        st = S[(S["exit_time"] >= "2011-01-01") & (S["exit_time"] < "2026-01-01")]
        tot, dd, worst = stats(ct, st)
        need = capital(ct, st)
        ctot, cdd, _ = stats(ct, None)
        cneed = capital(ct, None)
        y26 = float(pd.concat([C[C["exit_time"] >= "2026-01-01"]["pnl"],
                               S[S["exit_time"] >= "2026-01-01"]["pnl"]]).sum())
        r = dict(cell=NAMED.get((L, U), ""), L=L, U=U,
                 core=round(float(ct["pnl"].sum())),
                 sat=round(float(st["pnl"].sum())), total=round(tot),
                 maxDD=round(dd), ratio=round(tot / abs(dd), 2),
                 worst=round(worst), sat_n=len(st), need=round(need),
                 buf=round(need * 1.25), pace=round(tot / 15),
                 eff=round(tot / 15 / (need / 1000), 1),
                 core_only_pace=round(ctot / 15),
                 core_only_eff=round(ctot / 15 / (cneed / 1000), 1),
                 y26=round(y26))
        rows.append(r)
        print(f"L{L} {U:5} {r['cell']:4} tot {r['total']:>7,} DD {r['maxDD']:>6,} "
              f"ratio {r['ratio']:>5} worst {r['worst']:>6,} satN {r['sat_n']:>4} "
              f"need ${r['need']:>6,} pace ${r['pace']:>5,} "
              f"**eff ${r['eff']:>5}/yr per $1k** (core-only eff "
              f"${r['core_only_eff']}) 26 {r['y26']:>6,}", flush=True)
D = pd.DataFrame(rows).sort_values("eff", ascending=False)
D.to_csv(HERE / "grid_cells.csv", index=False)
print()
print("=== TOP 5 BY CAPITAL EFFICIENCY ===")
print(D.head(5)[["cell", "L", "U", "total", "ratio", "need", "pace",
                 "eff"]].to_string(index=False))
print()
print("=== AFFORDABLE AT $8,000 (buffered) ===")
aff = D[D["buf"] <= 8000]
print(aff[["cell", "L", "U", "ratio", "need", "buf", "pace", "eff"]]
      .to_string(index=False) if len(aff) else "none")
print("DONE")
