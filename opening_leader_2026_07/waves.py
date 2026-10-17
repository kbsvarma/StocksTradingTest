"""EXHAUSTION_PROGRAM waves W1-W6. Every cell -> one row in cells.csv.
IS = 2018-06..2023-12, OOS = 2024-01+. Gates applied to IS only here;
OOS is only *looked at* for IS survivors (one shot), enforced in report.
"""
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
P = pd.read_parquet(BASE / "data" / "signal_panel.parquet")
P = P[P["dv20"].notna()]
IS_END = "2024-01-01"

HS = np.where(P["mega"], 0.0003, 0.0005)
P = P.assign(hs=HS)
results = []


def net_long(entry, exit_, hs, notional):
    buy = entry * (1 + hs)
    sell = exit_ * (1 - hs)
    sh = notional / buy
    return sh * (sell - buy) - 2 * sh * 0.005


def net_short(entry, exit_, hs, notional):
    sell = entry * (1 - hs)
    buy = exit_ * (1 + hs)
    sh = notional / sell
    return sh * (sell - buy) - 2 * sh * 0.005


def record(wave, name, daily_pnl):
    """daily_pnl: Series indexed by day (one obs per day traded)."""
    d = daily_pnl.dropna()
    for split, lo, hi in (("IS", "", IS_END), ("OOS", IS_END, "9999")):
        x = d[(d.index >= lo) & (d.index < hi)]
        if len(x) < 30:
            row_ok = False
        m, s = x.mean(), x.std(ddof=1)
        t = m / (s / np.sqrt(len(x))) if len(x) > 1 and s > 0 else np.nan
        yrs = x.groupby(x.index.str[:4]).sum()
        results.append({
            "wave": wave, "cell": name, "split": split, "n": len(x),
            "mean": round(m, 2) if len(x) else np.nan,
            "t": round(t, 2) if t == t else np.nan,
            "wr": round((x > 0).mean(), 3) if len(x) else np.nan,
            "total": round(x.sum(), 0) if len(x) else np.nan,
            "pos_years": f"{int((yrs > 0).sum())}/{len(yrs)}",
        })


def single_name_cell(sel, entry_col, exit_col, side, wave, name, cond=None):
    """sel: per-day chosen row (DataFrame indexed by day)."""
    df = sel.dropna(subset=[entry_col, exit_col])
    if cond is not None:
        df = df[cond.reindex(df.index).fillna(False)]
    f = net_long if side == "L" else net_short
    pnl = f(df[entry_col], df[exit_col], df["hs"], 10000.0)
    record(wave, name, pd.Series(pnl.values, index=df.index))


def basket_pnl(df, entry_col, exit_col, side, notional=10000.0):
    """Equal-notional basket per day over df rows; returns day series."""
    df = df.dropna(subset=[entry_col, exit_col])
    if not len(df):
        return pd.Series(dtype=float)
    per = df.groupby("day").size().rename("k")
    df = df.merge(per, on="day")
    f = net_long if side == "L" else net_short
    pnl = f(df[entry_col], df[exit_col], df["hs"], notional / df["k"])
    return pnl.groupby(df["day"].values).sum()


# ---------- W1: cross-sectional decile map (rank by r5adj and by gap)
W1 = P.dropna(subset=["o936", "c1024"]).copy()
for rank_col in ("r5adj", "gap"):
    W1["dec"] = (W1.groupby("day")[rank_col]
                 .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                              labels=False)
                            if len(s) >= 30 else pd.Series(np.nan, s.index)))
    for dec in range(10):
        blk = W1[W1["dec"] == dec]
        for side in ("L", "S"):
            record("W1", f"{rank_col}_d{dec}_{side}",
                   basket_pnl(blk, "o936", "c1024", side))

# ---------- W2: reversal family (single-name picks)
cand = P.dropna(subset=["o936"])
leader = (cand[(cand["gap"] > 0) & (cand["r5"] > 0)]
          .sort_values("r5adj").groupby("day").last())
gapdn = cand.sort_values("gap").groupby("day").first()          # biggest gap-down
gapdn_fall = (cand[(cand["gap"] < 0) & (cand["r5"] < 0)]
              .sort_values("gap").groupby("day").first())
gapup_stall = (cand[(cand["gap"] > 0.01) & (cand["r5"] < 0)]
               .sort_values("gap").groupby("day").last())
for tag, sel, side in (("short_leader", leader, "S"),
                       ("long_biggest_gapdn", gapdn, "L"),
                       ("long_gapdn_falling", gapdn_fall, "L"),
                       ("short_gapup_stalled", gapup_stall, "S")):
    for exit_col in ("c944", "c959", "c1024"):
        single_name_cell(sel, "o936", exit_col, side, "W2",
                         f"{tag}_x{exit_col[1:]}")

# ---------- W3: conditioning on leader long/short (the family's core picks)
sel = leader
vix_t = pd.qcut(sel["vix"], 3, labels=["v0", "v1", "v2"])
brd_t = pd.qcut(sel["breadth"], 3, labels=["b0", "b1", "b2"])
rv_t = pd.qcut(sel["relvol5"], 3, labels=["rv0", "rv1", "rv2"])
gapband = pd.cut(sel["gap"], [0.0, 0.01, 0.03, 0.06, np.inf],
                 labels=["g0", "g1", "g2", "g3"])
dow = pd.to_datetime(sel.index).dayofweek
conds = {}
for lab in ("v0", "v1", "v2"):
    conds[f"vix_{lab}"] = vix_t == lab
for lab in ("b0", "b1", "b2"):
    conds[f"brd_{lab}"] = brd_t == lab
for lab in ("rv0", "rv1", "rv2"):
    conds[f"rvol_{lab}"] = rv_t == lab
for lab in ("g0", "g1", "g2", "g3"):
    conds[f"gap_{lab}"] = gapband == lab
conds["es_on_up"] = sel["es_on"] > 0
conds["es_on_dn"] = sel["es_on"] < 0
for d in range(5):
    conds[f"dow{d}"] = pd.Series(dow == d, index=sel.index)
for cname, cmask in conds.items():
    for side in ("L", "S"):
        single_name_cell(sel, "o936", "c1024", side, "W3",
                         f"leader_{side}_{cname}", cond=cmask)

# ---------- W4: entry x exit grid on leader L/S + best map structure
GRID = [("o936", ["c944", "c949", "c959", "c1009", "c1024"]),
        ("o940", ["c944", "c949", "c959", "c1009", "c1024"]),
        ("o945", ["c949", "c959", "c1009", "c1024"]),
        ("o950", ["c959", "c1009", "c1024"]),
        ("o1000", ["c1009", "c1024"])]
for e, exits in GRID:
    for x in exits:
        for side in ("L", "S"):
            single_name_cell(leader, e, x, side, "W4",
                             f"leader_{side}_{e}_{x}")

# ---------- W5: top-K minus bottom-K market-neutral baskets
W5 = P.dropna(subset=["o936", "c1024"]).copy()
for rank_col in ("r5adj", "gap"):
    r = W5.groupby("day")[rank_col].rank(method="first")
    n = W5.groupby("day")[rank_col].transform("size")
    for K in (5, 10):
        top = W5[r > n - K]
        bot = W5[r <= K]
        for tag, lblk, sblk in ((f"mom_{rank_col}_K{K}", top, bot),
                                (f"rev_{rank_col}_K{K}", bot, top)):
            l = basket_pnl(lblk, "o936", "c1024", "L", 5000.0)
            s = basket_pnl(sblk, "o936", "c1024", "S", 5000.0)
            record("W5", tag, l.add(s, fill_value=0.0))

# ---------- W6: daily-panel classics (top-400 mapped universe, O2C)
D = pd.read_parquet(BASE / "data" / "panel_daily.parquet").sort_values("day")
D["prev_close"] = D.groupby("raw_symbol")["close"].shift(1)
D["prev_ret"] = D.groupby("raw_symbol")["close"].pct_change().shift(1)
D = D[(D["close"] >= 5) & D["prev_close"].notna()]
D["gap"] = D["open"] / D["prev_close"] - 1
D["hs"] = 0.0005
D = D[D["day"] >= "2018-06-01"]
for rank_col in ("gap", "prev_ret"):
    dd = D.dropna(subset=[rank_col, "open", "close"]).copy()
    dd["dec"] = (dd.groupby("day")[rank_col]
                 .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                              labels=False)
                            if len(s) >= 50 else pd.Series(np.nan, s.index)))
    for dec in (0, 1, 8, 9):                      # extremes + neighbors
        blk = dd[dd["dec"] == dec]
        for side in ("L", "S"):
            record("W6", f"daily_{rank_col}_d{dec}_{side}",
                   basket_pnl(blk, "open", "close", side))

R = pd.DataFrame(results)
R.to_csv(BASE / "cells.csv", index=False)
IS = R[R["split"] == "IS"]
print(f"total cells: {IS['cell'].nunique()}")
pass_gate = IS[(IS["t"] >= 2.5) & (IS["mean"].abs() >= 8)
               & (IS["mean"] > 0)]
print(f"\nIS gate passers (t>=2.5, mean>=+$8): {len(pass_gate)}")
print(pass_gate.to_string(index=False))
print("\ntop 15 IS cells by t:")
print(IS.nlargest(15, "t")[["wave", "cell", "n", "mean", "t", "wr",
                            "total", "pos_years"]].to_string(index=False))
print("\nbottom 5 IS cells by t (strongest inverse signal):")
print(IS.nsmallest(5, "t")[["wave", "cell", "n", "mean", "t", "total"]]
      .to_string(index=False))
