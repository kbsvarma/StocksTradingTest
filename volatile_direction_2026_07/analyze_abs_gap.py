#!/usr/bin/env python3
"""Robustness diagnostics for the post-holdout ABS_GAP finding."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from run_exploratory_wave3 import build_eligible
from run_matrix import nw_t_stat, simulate


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def aggregate_topk(trades: pd.DataFrame, top_k: int) -> tuple[dict, pd.DataFrame]:
    trades = trades.copy()
    for column in ("notional", "gross_pnl", "net_pnl", "stress_pnl"):
        trades[column] /= top_k
    daily = trades.groupby("signal_day").agg(
        gross_pnl=("gross_pnl", "sum"),
        net_pnl=("net_pnl", "sum"),
        stress_pnl=("stress_pnl", "sum"),
    )
    development = daily[daily.index < "2023-01-01"]["net_pnl"]
    holdout = daily[daily.index >= "2023-01-01"]["net_pnl"]
    cumulative = daily["net_pnl"].cumsum()
    drawdown = cumulative - cumulative.cummax().clip(lower=0.0)
    yearly = daily.assign(year=daily.index.str[:4]).groupby("year")[
        "net_pnl"
    ].sum()
    metrics = {
        "top_k": top_k,
        "days": len(daily),
        "total_pnl": daily["net_pnl"].sum(),
        "gross_total_pnl": daily["gross_pnl"].sum(),
        "stress_total_pnl": daily["stress_pnl"].sum(),
        "development_total": development.sum(),
        "holdout_total": holdout.sum(),
        "holdout_t": nw_t_stat(holdout, 1),
        "max_drawdown": drawdown.min(),
        "positive_years": int((yearly > 0).sum()),
        "worst_year": yearly.min(),
        "worst_year_label": yearly.idxmin(),
        "cost_break_even_multiple": (
            daily["gross_pnl"].sum()
            / (daily["gross_pnl"].sum() - daily["net_pnl"].sum())
            if daily["gross_pnl"].sum() > daily["net_pnl"].sum()
            else np.nan
        ),
    }
    yearly_output = yearly.rename("net_pnl").reset_index()
    yearly_output["top_k"] = top_k
    return metrics, yearly_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    eligible, featured = build_eligible(panel, universe)
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    lookup = featured.set_index(["day", "raw_symbol"])
    ordered = eligible.sort_values(
        ["day", "ABS_GAP", "raw_symbol"],
        ascending=[True, False, True],
    ).copy()
    ordered["slot"] = ordered.groupby("day").cumcount()

    summaries = []
    yearlies = []
    all_scaled = []
    for top_k in (1, 2, 3, 5, 10):
        slot_trades = []
        for slot in range(top_k):
            selection = ordered[ordered["slot"] == slot]
            trades = simulate(
                selection,
                lookup,
                calendar,
                "ABS_GAP",
                "GAP_FADE",
                1,
            )
            trades["slot"] = slot + 1
            slot_trades.append(trades)
        combined = pd.concat(slot_trades, ignore_index=True)
        metrics, yearly = aggregate_topk(combined, top_k)
        summaries.append(metrics)
        yearlies.append(yearly)
        scaled = combined.copy()
        for column in ("notional", "gross_pnl", "net_pnl", "stress_pnl"):
            scaled[column] /= top_k
        scaled["top_k"] = top_k
        all_scaled.append(scaled)

    summary = pd.DataFrame(summaries)
    summary.to_csv(BASE / "abs_gap_topk_summary.csv", index=False)
    pd.concat(yearlies, ignore_index=True).to_csv(
        BASE / "abs_gap_topk_yearly.csv", index=False
    )
    pd.concat(all_scaled, ignore_index=True).to_csv(
        BASE / "abs_gap_topk_trades.csv", index=False
    )

    top1 = pd.read_csv(BASE / f"wave3_trades_{args.label}.csv")
    top1 = top1[
        (top1["selector"] == "ABS_GAP")
        & (top1["direction_rule"] == "GAP_FADE")
        & (top1["hold"] == 1)
    ].copy()
    signal_values = ordered[ordered["slot"] == 0][
        ["day", "raw_symbol", "ABS_GAP", "gap"]
    ].rename(columns={"day": "signal_day", "raw_symbol": "symbol"})
    top1 = top1.merge(
        signal_values,
        on=["signal_day", "symbol"],
        validate="one_to_one",
    )
    top1["gap_quartile"] = pd.qcut(
        top1["ABS_GAP"],
        4,
        labels=["Q1 smallest", "Q2", "Q3", "Q4 largest"],
        duplicates="drop",
    )
    diagnostics = (
        top1.groupby(["gap_quartile", "side"], observed=True)
        .agg(
            trades=("net_pnl", "size"),
            total_pnl=("net_pnl", "sum"),
            mean_pnl=("net_pnl", "mean"),
            stress_pnl=("stress_pnl", "sum"),
            win_rate=("net_pnl", lambda value: (value > 0).mean()),
            mean_abs_gap=("ABS_GAP", "mean"),
        )
        .reset_index()
    )
    diagnostics.to_csv(BASE / "abs_gap_diagnostics.csv", index=False)
    print(summary.to_string(index=False))
    print("\nGap-size and side diagnostics:")
    print(diagnostics.to_string(index=False))


if __name__ == "__main__":
    main()
