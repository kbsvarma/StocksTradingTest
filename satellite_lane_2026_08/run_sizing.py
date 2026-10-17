"""Full sizing table: P&L stats + measured capital, one pass per variant.

Cells: BASE(3/111) V2(1/111) V3(1/flat) V4(2/flat, user ask) V5(2/111).
Capital = max_t[margin(t) - cumPnL(t)], per-symbol MAX qty per day.
"""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
src = open(HERE / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])

from rotation import monday_of_week, weights_for                # noqa: E402
from rotation_grid import trailing_returns                      # noqa: E402

OVN = {"NQ": 2300.0, "ES": 1600.0, "YM": 1100.0}
INTRA = {k: v * 0.60 for k, v in OVN.items()}
MONDAYS = list(W.index)
SPEC = {"BASE": (3, "111"), "V2": (1, "111"), "V3": (1, "flat"),
        "V4": (2, "flat"), "V5": (2, "111")}


def build_weights(kind):
    lead, unsure_mode = SPEC[kind]
    rows = {}
    for mon in MONDAYS:
        rets = trailing_returns(daily, mon, 35)
        valid = all(v == v for v in rets.values())
        vals = sorted(rets.values(), reverse=True)
        unsure = (not valid) or (vals[0] - vals[1]) * 100 < 1.0
        rank = sorted(rets, key=lambda s: rets[s], reverse=True)
        if unsure:
            rows[mon] = ({s: 1 for s in rets} if unsure_mode == "111"
                         else {s: 0 for s in rets})
        else:
            rows[mon] = {rank[0]: lead, rank[1]: 0, rank[2]: 0}
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


def capital(core_t, sat_t):
    days = pd.date_range("2011-01-01", "2025-12-31", freq="D", tz="UTC")
    margin = pd.Series(0.0, index=days)
    for sym in OVN:
        legs = core_t[core_t["sym"] == sym]
        if len(legs):
            q = pd.Series(0.0, index=days)
            for tr in legs.itertuples():
                live = ((days >= pd.Timestamp(tr.entry_time).normalize()) &
                        (days <= pd.Timestamp(tr.exit_time).normalize()))
                q[live] = q[live].clip(lower=float(tr.qty))
            margin += q * OVN[sym]
        sl = sat_t[sat_t["sym"] == sym]
        if len(sl):
            sd = pd.DatetimeIndex(sl["entry_time"]).normalize().unique()
            margin[margin.index.isin(sd)] += INTRA[sym]
    pnl = pd.concat([
        pd.Series(core_t["pnl"].values,
                  index=pd.DatetimeIndex(core_t["exit_time"]).normalize()),
        pd.Series(sat_t["pnl"].values,
                  index=pd.DatetimeIndex(sat_t["exit_time"]).normalize())])
    cum = pnl.groupby(level=0).sum().reindex(days, fill_value=0.0).cumsum()
    need = margin - cum
    return float(need.max()), need.idxmax(), float(margin.max())


rows = []
for kind in SPEC:
    Wv = build_weights(kind)
    globals()["W"] = Wv
    globals()["WROWS"] = {m: Wv.loc[m] for m in Wv.index}
    C = core_for(Wv)
    globals()["EXPO"] = expo_for(C)
    ct = C[(C["exit_time"] >= "2011-01-01") & (C["exit_time"] < "2026-01-01")]
    S = pd.concat([day_sat(s, cutoff=15) for s in M1])
    st = S[(S["exit_time"] >= "2011-01-01") & (S["exit_time"] < "2026-01-01")]
    cp = pd.concat([
        pd.Series(ct["pnl"].values, index=pd.DatetimeIndex(ct["exit_time"])),
        pd.Series(st["pnl"].values, index=pd.DatetimeIndex(st["exit_time"]))
    ]).sort_index()
    dd = daily_mtm_dd(pd.concat([ct, st.assign(qty=1)]))
    need, when, pkm = capital(ct, st)
    yr = cp.groupby(cp.index.year).sum()
    r = dict(cell=kind, core=round(float(ct["pnl"].sum())),
             sat=round(float(st["pnl"].sum())), total=round(float(cp.sum())),
             maxDD=round(dd), ratio=round(float(cp.sum()) / abs(dd), 2),
             worst=round(float(yr.min())), sat_n=len(st),
             need=round(need), buf=round(need * 1.25), peak_margin=round(pkm),
             pace=round(float(cp.sum()) / 15),
             y26=round(float(pd.concat([
                 C[C["exit_time"] >= "2026-01-01"]["pnl"],
                 S[S["exit_time"] >= "2026-01-01"]["pnl"]]).sum())))
    rows.append(r)
    print(f"{kind:5} core {r['core']:>7,} sat {r['sat']:>6,} tot {r['total']:>7,} "
          f"DD {r['maxDD']:>6,} ratio {r['ratio']:>5} worst {r['worst']:>6,} "
          f"satN {r['sat_n']:>4} | need ${r['need']:>6,} (+25% ${r['buf']:>6,}) "
          f"peakMgn ${r['peak_margin']:>6,} pace ${r['pace']:>5,}/yr "
          f"26 {r['y26']:>6,}", flush=True)
pd.DataFrame(rows).to_csv(HERE / "sizing_cells.csv", index=False)
print("DONE")
