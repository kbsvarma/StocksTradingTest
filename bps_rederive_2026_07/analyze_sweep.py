"""IS sweep analysis: per-cell stats -> gate filter -> smoothness check -> ranked
survivor candidates. NO OOS data is touched here (shards are IS years only).

Cell = (entry, width, otm, dir_variant, sl, vix_gate). dir/vix applied as filters
on stored day flags. Trades = one row per traded day per cell.
Gates (PREREGISTRATION_BPS.md): net HAC t>=2.0, drop-worst-year t>=1.5,
>=100 IS trades, profitable in >=2 eras it spans.
Smoothness: a candidate must have >=half of its +/-1-step neighbors profitable.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

IS_YEARS = list(range(2013, 2021))
SWEEP = Path("data/sweep")

DIRS = ["none", "dir", "dir25"]
ENTRIES = ["09:35","10:00","10:30","11:00","11:30","12:00","12:30","13:00","13:30","14:00"]
WIDTHS = [25.0, 50.0, 75.0, 100.0]
OTMS = [0.005, 0.010, 0.015]
SLS = [0.0, 1.5, 2.0, 3.0]

def nw_tstat(x, lags=5):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 10: return np.nan
    mu = x.mean(); e = x - mu
    var = (e @ e) / n
    for k in range(1, lags + 1):
        w = 1 - k / (lags + 1)
        var += 2 * w * ((e[k:] @ e[:-k]) / n)
    se = np.sqrt(var / n)
    return mu / se if se > 0 else np.nan

def era(d):
    if d < pd.Timestamp("2016-02-23"): return "friERA"
    if d < pd.Timestamp("2022-04-18"): return "mwfERA"
    return "dailyERA"

def load_is():
    parts = [pd.read_parquet(SWEEP / f"sweep_{y}.parquet") for y in IS_YEARS
             if (SWEEP / f"sweep_{y}.parquet").exists()]
    df = pd.concat(parts, ignore_index=True)
    df["year"] = df.date.dt.year
    df["era"] = df.date.apply(era)
    return df

def apply_gates(df, dir_variant, vix_gate):
    out = df
    if vix_gate:
        out = out[(out.vix_open >= 12.0) & (out.vix_open <= 25.0)]
    if dir_variant == "dir":
        out = out[out.dir_ok == True]
    elif dir_variant == "dir25":
        out = out[out.dir25_ok == True]
    return out

def cell_stats(sub):
    x = sub.pnl_usd
    if len(x) < 10:
        return None
    t = nw_tstat(x.values)
    yr_means = sub.groupby("year")["pnl_usd"].mean()
    worst = yr_means.idxmin() if len(yr_means) > 1 else None
    t_dwy = nw_tstat(sub[sub.year != worst]["pnl_usd"].values) if worst is not None else np.nan
    eras = sub.groupby("era")["pnl_usd"].sum()
    return {
        "n": len(x), "mean": x.mean(), "t": t, "t_dwy": t_dwy,
        "total": x.sum(), "%pos": (x > 0).mean() * 100, "worst": x.min(),
        "eras_pos": int((eras > 0).sum()), "eras_present": len(eras),
        "stop_rate": (sub.status == "stop").mean() * 100,
    }

def main():
    df = load_is()
    print(f"IS rows: {len(df)}  days: {df.date.nunique()}  years: {sorted(df.year.unique())}")
    results = []
    for dirv in DIRS:
        for vg in (True, False):
            g = apply_gates(df, dirv, vg)
            for (entry, width, otm, sl), sub in g.groupby(["entry", "width", "otm", "sl"]):
                st = cell_stats(sub)
                if st is None: continue
                st.update({"entry": entry, "width": width, "otm": otm, "sl": sl,
                           "dir": dirv, "vix": vg})
                results.append(st)
    R = pd.DataFrame(results)
    R.to_parquet("data/is_cell_stats.parquet")
    print(f"cells with stats: {len(R)}")

    # gates
    cand = R[(R.t >= 2.0) & (R.t_dwy >= 1.5) & (R.n >= 100) & (R.eras_pos >= 2)].copy()
    print(f"cells passing IS gates: {len(cand)}")

    # smoothness: for each candidate, check +/-1-step neighbors in (entry, width, otm, sl)
    key = ["entry","width","otm","sl","dir","vix"]
    Rix = R.set_index(key)
    def neighbors(row):
        out = []
        ei = ENTRIES.index(row.entry) if row.entry in ENTRIES else None
        for axis, vals, cur in (("entry", ENTRIES, row.entry), ("width", WIDTHS, row.width),
                                ("otm", OTMS, row.otm), ("sl", SLS, row.sl)):
            if cur not in vals: continue
            i = vals.index(cur)
            for j in (i-1, i+1):
                if 0 <= j < len(vals):
                    k = dict(entry=row.entry, width=row.width, otm=row.otm, sl=row.sl)
                    k[axis] = vals[j]
                    out.append((k["entry"], k["width"], k["otm"], k["sl"], row.dir, row.vix))
        return out
    smooth_flags = []
    for _, row in cand.iterrows():
        nb = neighbors(row)
        vals = []
        for k in nb:
            if k in Rix.index:
                vals.append(Rix.loc[k, "total"] if np.isscalar(Rix.loc[k, "total"]) else Rix.loc[k, "total"].iloc[0])
        pos = sum(1 for v in vals if v > 0)
        smooth_flags.append(pos >= max(1, len(vals) // 2))
    cand["smooth"] = smooth_flags
    surv = cand[cand.smooth].sort_values("t", ascending=False)
    print(f"cells passing smoothness: {len(surv)}")
    cols = ["entry","width","otm","sl","dir","vix","n","mean","t","t_dwy","total","%pos","worst","eras_pos","stop_rate"]
    print("\nTOP 20 by IS t-stat:")
    print(surv[cols].head(20).to_string(index=False))
    surv[cols].to_csv("is_survivor_candidates.csv", index=False)

    # incumbent (SCAN cell) reported regardless
    print("\nINCUMBENT (SCAN1030, w50, otm1.0, dir-coupled, vix on):")
    inc = df[(df.entry == "SCAN1030") & (df.sl == 2.0)]
    inc = inc[(inc.vix_open >= 12) & (inc.vix_open <= 25)]
    st = cell_stats(inc)
    if st:
        print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in st.items()})
    for sl in (0.0, 1.5, 3.0):
        inc2 = df[(df.entry == "SCAN1030") & (df.sl == sl)]
        inc2 = inc2[(inc2.vix_open >= 12) & (inc2.vix_open <= 25)]
        st2 = cell_stats(inc2)
        if st2:
            print(f"  SL={sl}: t={st2['t']:.2f} t_dwy={st2['t_dwy']:.2f} n={st2['n']} total=${st2['total']:.0f} stop%={st2['stop_rate']:.1f}")

if __name__ == "__main__":
    main()
