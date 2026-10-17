"""FIRST-HOUR EXTENSION PROGRAM — the seven frozen waves, exactly as
declared in EXHAUSTION_PROGRAM.md. Every cell -> cells2.csv with IS and
holdout rows; BH q-values across all cells searched. Holdout numbers are
computed here but the ledger only *opens* them for IS survivors.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
P = pd.read_parquet(BASE / "data" / "signal_panel.parquet")
P = P[P["bars5"] == 5]                      # complete signal windows only
IS_END = "2024-01-01"
results = []


def _net(entry, exit_, hs, notional, side):
    if side == "L":
        buy, sell = entry * (1 + hs), exit_ * (1 - hs)
        sh = notional / buy
        return sh * (sell - buy) - 2 * sh * 0.005
    sell, buy = entry * (1 - hs), exit_ * (1 + hs)
    sh = notional / sell
    return sh * (sell - buy) - 2 * sh * 0.005


def hs_of(df, mult=1.0):
    return np.where(df["mega"].fillna(False), 0.0003, 0.0005) * mult


def record(wave, name, day_pnl):
    d = day_pnl.dropna()
    for split, lo, hi in (("IS", "", IS_END), ("HOLD", IS_END, "9999")):
        x = d[(d.index >= lo) & (d.index < hi)]
        m = x.mean() if len(x) else np.nan
        s = x.std(ddof=1) if len(x) > 1 else np.nan
        t = m / (s / np.sqrt(len(x))) if len(x) > 1 and s > 0 else np.nan
        yrs = x.groupby(x.index.str[:4]).sum() if len(x) else pd.Series(dtype=float)
        results.append({"wave": wave, "cell": name, "split": split,
                        "n": len(x), "mean": round(m, 2) if m == m else np.nan,
                        "t": round(t, 2) if t == t else np.nan,
                        "wr": round((x > 0).mean(), 3) if len(x) else np.nan,
                        "total": round(x.sum(), 0) if len(x) else np.nan,
                        "pos_years": f"{int((yrs > 0).sum())}/{len(yrs)}"})


def one_name(df, entry_col, exit_col, side, wave, name, spread_mult=1.0):
    d = df.dropna(subset=[entry_col, exit_col])
    pnl = _net(d[entry_col], d[exit_col], hs_of(d, spread_mult), 10000.0, side)
    record(wave, name, pd.Series(pnl.values, index=d["day"].values))


def basket(df, entry_col, exit_col, side, notional):
    d = df.dropna(subset=[entry_col, exit_col])
    if not len(d):
        return pd.Series(dtype=float)
    k = d.groupby("day")["sym"].transform("size")
    pnl = _net(d[entry_col], d[exit_col], hs_of(d), notional / k, side)
    return pnl.groupby(d["day"].values).sum()


SIG = {"5": ("r5", "o0936"), "10": ("r10", "o0940"),
       "15": ("r15", "o0945"), "30": ("r30", "o1000")}
EXIT = "c1024"

# W1: decile map by r5 and UTC-gap rank, both sides, 09:36 -> 10:24
W1 = P.dropna(subset=["o0936", EXIT]).copy()
for rank_col, tag in (("r5", "r5"), ("gap_utc", "gap")):
    dec = (W1.groupby("day")[rank_col]
           .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                        labels=False)
                      if s.notna().sum() >= 30 else pd.Series(np.nan, s.index)))
    for d_ in range(10):
        blk = W1[dec == d_]
        for side in ("L", "S"):
            record("W1", f"{tag}_d{d_}_{side}",
                   basket(blk, "o0936", EXIT, side, 10000.0))

# W2: one-name leader/loser x continuation/reversal at 4 horizons
for h, (rcol, ecol) in SIG.items():
    base = P.dropna(subset=[rcol, ecol, EXIT])
    lead = base.loc[base.groupby("day")[rcol].idxmax()]
    lose = base.loc[base.groupby("day")[rcol].idxmin()]
    one_name(lead, ecol, EXIT, "L", "W2", f"lead{h}_cont")
    one_name(lead, ecol, EXIT, "S", "W2", f"lead{h}_fade")
    one_name(lose, ecol, EXIT, "L", "W2", f"lose{h}_rev")
    one_name(lose, ecol, EXIT, "S", "W2", f"lose{h}_cont")

# W3: largest +/- UTC gap x confirmation/rejection at 5/10/15
for h in ("5", "10", "15"):
    rcol, ecol = SIG[h]
    base = P.dropna(subset=["gap_utc", rcol, ecol, EXIT])
    gup = base.loc[base.groupby("day")["gap_utc"].idxmax()]
    gdn = base.loc[base.groupby("day")["gap_utc"].idxmin()]
    for tag, sel in (("gup", gup), ("gdn", gdn)):
        for state, mask in (("conf", sel[rcol] > 0), ("rej", sel[rcol] < 0)):
            blk = sel[mask]
            for side in ("L", "S"):
                one_name(blk, ecol, EXIT, side, "W3",
                         f"{tag}{h}_{state}_{side}")

# W4: leaders/losers with causal opening-volume surprise filters
for h in ("5", "10", "15"):
    rcol, ecol = SIG[h]
    base = P.dropna(subset=[rcol, ecol, EXIT, f"rvol{h}_rank"])
    for vtag, vmask_fn in (("vmed", lambda b: b[f"rvol{h}_rank"] > 0.5),
                           ("vtop", lambda b: b[f"rvol{h}_rank"] > 0.75)):
        blk = base[vmask_fn(base)]
        lead = blk.loc[blk.groupby("day")[rcol].idxmax()]
        lose = blk.loc[blk.groupby("day")[rcol].idxmin()]
        one_name(lead, ecol, EXIT, "L", "W4", f"lead{h}_{vtag}_cont")
        one_name(lead, ecol, EXIT, "S", "W4", f"lead{h}_{vtag}_fade")
        one_name(lose, ecol, EXIT, "L", "W4", f"lose{h}_{vtag}_rev")
        one_name(lose, ecol, EXIT, "S", "W4", f"lose{h}_{vtag}_cont")

# W5: breadth/dispersion day-state on the four 5-minute direction cells
base5 = P.dropna(subset=["r5", "o0936", EXIT]).copy()
disp_med = (base5.groupby("day")["disp5"].first().sort_index()
            .expanding(60).median().shift(1))
lead5 = base5.loc[base5.groupby("day")["r5"].idxmax()].set_index("day")
lose5 = base5.loc[base5.groupby("day")["r5"].idxmin()].set_index("day")
for sname, sel, side in (("lead_cont", lead5, "L"), ("lead_fade", lead5, "S"),
                         ("lose_rev", lose5, "L"), ("lose_cont", lose5, "S")):
    for cname, cm in (("brd_hi", sel["breadth5"] > 0.5),
                      ("brd_lo", sel["breadth5"] <= 0.5),
                      ("disp_hi", sel["disp5"] > disp_med.reindex(sel.index)),
                      ("disp_lo", sel["disp5"] <= disp_med.reindex(sel.index))):
        blk = sel[cm.fillna(False)].reset_index()
        one_name(blk, "o0936", EXIT, side, "W5", f"{sname}_{cname}")

# W6: time structure — signal horizon x exit grid, leader & loser, both dirs
EXITS = ["c0949", "c0959", "c1009", "c1024"]
ELATER = {"5": EXITS, "10": EXITS, "15": ["c0959", "c1009", "c1024"]}
for h, exits in ELATER.items():
    rcol, ecol = SIG[h]
    base = P.dropna(subset=[rcol, ecol])
    lead = base.loc[base.groupby("day")[rcol].idxmax()]
    lose = base.loc[base.groupby("day")[rcol].idxmin()]
    for x in exits:
        one_name(lead, ecol, x, "L", "W6", f"lead{h}_L_{x}")
        one_name(lead, ecol, x, "S", "W6", f"lead{h}_S_{x}")
        one_name(lose, ecol, x, "L", "W6", f"lose{h}_L_{x}")
        one_name(lose, ecol, x, "S", "W6", f"lose{h}_S_{x}")

# W7: market-neutral baskets, $10k GROSS total.
# Entry MUST match the signal horizon (r15 is unknown at 09:36 — the first
# run entered everything at 09:36 and produced t=44 lookahead artifacts).
for h in ("5", "10", "15"):
    rcol, ecol = SIG[h]
    W7 = P.dropna(subset=[ecol, EXIT, rcol]).copy()
    r = W7.groupby("day")[rcol].rank(method="first")
    n = W7.groupby("day")[rcol].transform("size")
    dec = (W7.groupby("day")[rcol]
           .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                        labels=False)
                      if s.notna().sum() >= 30 else pd.Series(np.nan, s.index)))
    legs = {"decile": (W7[dec == 9], W7[dec == 0])}
    for K in (3, 5, 10):
        legs[f"K{K}"] = (W7[r > n - K], W7[r <= K])
    for lname, (top, bot) in legs.items():
        mom = (basket(top, ecol, EXIT, "L", 5000.0)
               .add(basket(bot, ecol, EXIT, "S", 5000.0), fill_value=0))
        rev = (basket(bot, ecol, EXIT, "L", 5000.0)
               .add(basket(top, ecol, EXIT, "S", 5000.0), fill_value=0))
        record("W7", f"mom{h}_{lname}", mom)
        record("W7", f"rev{h}_{lname}", rev)

R = pd.DataFrame(results)

# BH q-values across ALL searched cells (IS split, two-sided p from t)
from scipy import stats as st
IS = R[R["split"] == "IS"].copy()
IS["p"] = 2 * st.t.sf(IS["t"].abs(), IS["n"] - 1)
IS = IS.sort_values("p").reset_index(drop=True)
m = IS["p"].notna().sum()
IS["q_bh"] = (IS["p"] * m / (IS.index + 1)).cummin()[::-1].cummin()[::-1]
R = R.merge(IS[["wave", "cell", "q_bh"]], on=["wave", "cell"], how="left")
R.to_csv(BASE / "cells2.csv", index=False)

n_cells = IS["cell"].nunique()
gates = IS[(IS["t"] >= 2.5) & (IS["mean"] >= 8)
           & IS["pos_years"].isin(["4/6", "5/6", "6/6"])]
print(f"cells searched: {n_cells}")
print(f"\nIS gate passers (t>=2.5, mean>=+$8, >=4/6 years):")
print(gates.to_string(index=False) if len(gates) else "  NONE")
print(f"\nmin BH q across sweep: {IS['q_bh'].min():.3f}")
print("\ntop 12 IS by t:")
print(IS.nlargest(12, "t")[["wave", "cell", "n", "mean", "t", "wr",
                            "pos_years", "q_bh"]].to_string(index=False))
