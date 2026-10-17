"""Gap-follow stability / multiple-comparison analysis (SPX daily).

Complements backtests/gap_follow_deep.py:
  - enumerates the full sweep grid that the deep-dive printed (the "cells")
  - per cell: t-stat of mean trade P&L, Bonferroni-adjusted significance
  - split-half stability: 2016-2020 vs 2021-2026 (per task spec) and the
    full-data halves 2013-2019 vs 2020-2026
  - baseline-relative check: does any filter beat the unfiltered strategy
    out of sample?

Only EOD exits are tested — the PT/SL variants in gap_follow_deep.py are
invalid on daily bars (stop applied to the CLOSE, i.e. look-ahead).

Writes cell_census.csv + prints a summary. Atomic write (tmp+rename).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from gap_follow_deep import load, trades_for  # noqa: E402

OUT = HERE / "cell_census.csv"


def cell_stats(t: pd.DataFrame, label: str) -> dict:
    def half(sub: pd.DataFrame) -> tuple[float, float]:
        if len(sub) == 0:
            return 0.0, 0.0
        return float(sub["pnl"].sum()), float(sub["pnl"].mean())

    p = t["pnl"].values
    n = len(p)
    if n < 2:
        return dict(label=label, n=n, total=float(p.sum()) if n else 0.0,
                    avg=np.nan, t_stat=np.nan, p_val=np.nan,
                    h1_total=np.nan, h1_avg=np.nan, h2_total=np.nan, h2_avg=np.nan,
                    e16_20_total=np.nan, e21_26_total=np.nan)
    tt = stats.ttest_1samp(p, 0.0)
    h1 = t[t.index < "2020-01-01"]
    h2 = t[t.index >= "2020-01-01"]
    e1 = t[(t.index >= "2016-01-01") & (t.index < "2021-01-01")]
    e2 = t[t.index >= "2021-01-01"]
    return dict(
        label=label,
        n=n,
        total=round(float(p.sum())),
        avg=round(float(p.mean())),
        t_stat=round(float(tt.statistic), 2),
        p_val=float(tt.pvalue),
        h1_total=round(half(h1)[0]),
        h1_avg=round(half(h1)[1]),
        h2_total=round(half(h2)[0]),
        h2_avg=round(half(h2)[1]),
        e16_20_total=round(half(e1)[0]),
        e16_20_avg=round(half(e1)[1]),
        e16_20_n=len(e1),
        e21_26_total=round(half(e2)[0]),
        e21_26_avg=round(half(e2)[1]),
        e21_26_n=len(e2),
    )


def main() -> None:
    df = load()
    rows: list[dict] = []

    # 1. threshold sweep (both directions, EOD)
    for thr in [0.002, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020, 0.025]:
        rows.append(cell_stats(trades_for(df, min_gap=thr), f"thr>{thr*100:.1f}%"))

    # 2. direction × threshold
    for thr in [0.005, 0.010]:
        for d in ["up", "down"]:
            rows.append(cell_stats(trades_for(df, min_gap=thr, direction=d),
                                   f"thr>{thr*100:.1f}% {d}-only"))

    # 3. DOW cells (thr 0.5%)
    base = trades_for(df, min_gap=0.005)
    dow_names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    for d in range(5):
        rows.append(cell_stats(base[base["dow"] == d], f"thr>0.5% {dow_names[d]}"))

    # 4. VIX cells
    for lo, hi in [(0, 15), (15, 20), (20, 25), (25, 999)]:
        rows.append(cell_stats(trades_for(df, 0.005, vix_min=lo, vix_max=hi),
                               f"thr>0.5% VIX {lo}-{hi}"))

    # 5. gap size buckets
    for lo, hi in [(0.005, 0.010), (0.010, 0.015), (0.015, 0.020),
                   (0.020, 0.030), (0.030, 1.0)]:
        sub = base[base["gap_pct"].abs().between(lo, hi)]
        rows.append(cell_stats(sub, f"bucket {lo*100:.1f}-{hi*100:.1f}%"))

    # 6. combo cells from section 8 of the deep dive
    rows.append(cell_stats(trades_for(df, 0.005, vix_min=15), "thr>0.5% VIX>15"))
    rows.append(cell_stats(trades_for(df, 0.005, vix_max=25), "thr>0.5% VIX<25"))
    rows.append(cell_stats(trades_for(df, 0.010, vix_max=25), "thr>1.0% VIX<25"))
    rows.append(cell_stats(trades_for(df, 0.010, vix_min=15, vix_max=25),
                           "thr>1.0% VIX 15-25"))
    rows.append(cell_stats(base[base["dow"] == 0], "Mon gaps (dup)"))
    t10 = trades_for(df, 0.010)
    rows.append(cell_stats(t10[t10["dow"] == 0], "Mon + thr>1.0%"))

    res = pd.DataFrame(rows)
    m = len(res)
    res["bonferroni_sig"] = res["p_val"] < 0.05 / m
    res["nominal_sig"] = res["p_val"] < 0.05
    res["stable_16_20_vs_21_26"] = (res["e16_20_avg"] > 0) & (res["e21_26_avg"] > 0)

    tmp = OUT.with_suffix(".csv.tmp")
    res.to_csv(tmp, index=False)
    tmp.rename(OUT)

    pd.set_option("display.width", 250)
    print(f"cells tested m={m}; Bonferroni alpha={0.05/m:.5f}")
    cols = ["label", "n", "total", "avg", "t_stat", "p_val", "nominal_sig",
            "bonferroni_sig", "e16_20_n", "e16_20_avg", "e21_26_n", "e21_26_avg",
            "stable_16_20_vs_21_26"]
    print(res[cols].to_string(index=False))
    print("\nBonferroni survivors:")
    print(res[res.bonferroni_sig][cols].to_string(index=False))
    # recent-regime check on the headline configs
    print("\nRecent regime (2023-01-01 onward) for headline configs:")
    for thr in [0.005, 0.010, 0.015]:
        t = trades_for(df, thr)
        recent = t[t.index >= "2023-01-01"]
        print(f"  thr>{thr*100:.1f}%: n={len(recent)} total=${recent['pnl'].sum():,.0f} "
              f"avg=${recent['pnl'].mean():,.0f} win={(recent['pnl']>0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
