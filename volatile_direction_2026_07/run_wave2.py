#!/usr/bin/env python3
"""Run the frozen literature-guided daily direction matrix."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from run_matrix import (
    HOLDS,
    SELECTORS,
    add_features,
    bh_qvalues,
    direction_columns,
    signed,
    simulate,
    summarize,
)


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DATA = BASE / "data"
VIX_PATH = ROOT / "bps_rederive_2026_07" / "data" / "vix_daily_cboe.parquet"
WAVE2_DIRECTIONS = (
    "REL_MR1",
    "REL_MOM1",
    "REL_MR5",
    "REL_MOM5",
    "GAP_FADE",
    "GAP_FOLLOW",
    "DAY_FADE",
    "DAY_FOLLOW",
    "PTH_HYBRID",
    "VIX_REV_TREND",
)


def selections_with_directions(
    panel: pd.DataFrame,
    universe: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    panel = direction_columns(add_features(panel))
    panel["month"] = panel["day"].str[:7]
    eligible = panel.merge(
        universe[["month", "symbol", "rank", "mega"]],
        left_on=["month", "raw_symbol"],
        right_on=["month", "symbol"],
        how="inner",
    )
    required = [
        "ATR20",
        "RV20",
        "VOL_SPIKE",
        "ret1",
        "ret5",
        "ret20",
        "gap",
        "session_return",
        "ma50",
        "ma200",
        "roll_high20",
        "roll_low20",
        "price_to_high252",
    ]
    eligible = eligible.dropna(subset=required).copy()
    median_ret1 = eligible.groupby("day")["ret1"].transform("median")
    median_ret5 = eligible.groupby("day")["ret5"].transform("median")
    relative1 = eligible["ret1"] - median_ret1
    relative5 = eligible["ret5"] - median_ret5
    eligible["REL_MR1"] = -signed(relative1, eligible["ret1"])
    eligible["REL_MOM1"] = signed(relative1, eligible["ret1"])
    eligible["REL_MR5"] = -signed(relative5, eligible["ret5"])
    eligible["REL_MOM5"] = signed(relative5, eligible["ret5"])
    eligible["GAP_FADE"] = -signed(eligible["gap"], eligible["ret1"])
    eligible["GAP_FOLLOW"] = signed(eligible["gap"], eligible["ret1"])
    eligible["DAY_FADE"] = -signed(
        eligible["session_return"], eligible["ret1"]
    )
    eligible["DAY_FOLLOW"] = signed(
        eligible["session_return"], eligible["ret1"]
    )
    median_pth = eligible.groupby("day")["price_to_high252"].transform("median")
    eligible["PTH_HYBRID"] = eligible["MR5"]
    high_pth = eligible["price_to_high252"] >= median_pth
    eligible.loc[high_pth, "PTH_HYBRID"] = eligible.loc[high_pth, "MOM5"]

    vix = pd.read_parquet(VIX_PATH)
    vix_close = vix["close"].copy()
    vix_close.index = pd.to_datetime(vix_close.index).strftime("%Y-%m-%d")
    vix_median = vix_close.rolling(252, min_periods=126).median()
    eligible["vix"] = eligible["day"].map(vix_close)
    eligible["vix_median252"] = eligible["day"].map(vix_median)
    eligible["VIX_REV_TREND"] = np.nan
    high_vix = eligible["vix"] >= eligible["vix_median252"]
    low_vix = eligible["vix"] < eligible["vix_median252"]
    eligible.loc[high_vix, "VIX_REV_TREND"] = eligible.loc[high_vix, "MR1"]
    eligible.loc[low_vix, "VIX_REV_TREND"] = eligible.loc[
        low_vix, "PRICE_MA50"
    ]

    ranks = [
        eligible.groupby("day")[column].rank(pct=True)
        for column in ("ATR20", "RV20", "VOL_SPIKE")
    ]
    ranks.append(
        eligible.groupby("day")["ret5"].transform(
            lambda value: value.abs().rank(pct=True)
        )
    )
    eligible["SHOCK"] = sum(ranks)
    selections: dict[str, pd.DataFrame] = {}
    for selector in SELECTORS:
        index = eligible.groupby("day")[selector].idxmax()
        selections[selector] = eligible.loc[index].sort_values("day").copy()
    return selections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    selections = selections_with_directions(panel, universe)
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    feature_panel = direction_columns(add_features(panel))
    lookup = feature_panel.set_index(["day", "raw_symbol"])

    ledgers: list[pd.DataFrame] = []
    summaries: list[dict] = []
    for selector, selection in selections.items():
        for direction in WAVE2_DIRECTIONS:
            usable = selection.dropna(subset=[direction])
            for hold in HOLDS:
                trades = simulate(
                    usable, lookup, calendar, selector, direction, hold
                )
                if trades.empty:
                    continue
                ledgers.append(trades)
                summaries.append(summarize(trades, len(selection)))
    all_trades = pd.concat(ledgers, ignore_index=True)
    summary = pd.DataFrame(summaries)
    summary["wave2_q"] = bh_qvalues(summary["holdout_p"])

    primary = pd.read_csv(BASE / f"summary_{args.label}.csv")
    combined_p = pd.concat(
        [
            primary[["selector", "direction_rule", "hold", "holdout_p"]].assign(
                wave="primary"
            ),
            summary[["selector", "direction_rule", "hold", "holdout_p"]].assign(
                wave="wave2"
            ),
        ],
        ignore_index=True,
    )
    combined_p["combined_q"] = bh_qvalues(combined_p["holdout_p"])
    combined_p.to_csv(BASE / f"combined_fdr_{args.label}.csv", index=False)
    wave2_q = combined_p[combined_p["wave"] == "wave2"][
        ["selector", "direction_rule", "hold", "combined_q"]
    ]
    summary = summary.merge(
        wave2_q,
        on=["selector", "direction_rule", "hold"],
        validate="one_to_one",
    )

    benchmark = primary[
        primary["direction_rule"].isin(["ALWAYS_LONG", "ALWAYS_SHORT"])
    ].set_index(["selector", "direction_rule", "hold"])["total_pnl"]
    summary["beats_both_fixed_sides"] = [
        row.total_pnl
        > benchmark.get((row.selector, "ALWAYS_LONG", row.hold), np.inf)
        and row.total_pnl
        > benchmark.get((row.selector, "ALWAYS_SHORT", row.hold), np.inf)
        for row in summary.itertuples()
    ]
    summary["passes_all_gates"] = (
        (summary["coverage"] >= 0.95)
        & (summary["development_total"] > 0)
        & (summary["development_mean"] > 0)
        & (summary["holdout_total"] > 0)
        & (summary["holdout_mean"] > 0)
        & (summary["holdout_t"] >= 2.0)
        & (summary["stress_total_pnl"] > 0)
        & (summary["positive_years"] >= 6)
        & (summary["key_years_negative"] <= 1)
        & (summary["max_ticker_positive_contribution"] <= 0.20)
        & (summary["combined_q"] <= 0.10)
        & summary["beats_both_fixed_sides"]
    )
    summary = summary.sort_values("total_pnl", ascending=False)
    all_trades.to_csv(BASE / f"wave2_trades_{args.label}.csv", index=False)
    summary.to_csv(BASE / f"wave2_summary_{args.label}.csv", index=False)
    yearly = (
        all_trades.assign(year=all_trades["signal_day"].str[:4])
        .groupby(["selector", "direction_rule", "hold", "year"])["net_pnl"]
        .sum()
        .reset_index()
    )
    yearly.to_csv(BASE / f"wave2_yearly_{args.label}.csv", index=False)
    print(summary.head(25).to_string(index=False))
    print(
        f"\nexecuted={len(summary)} cells "
        f"survivors={int(summary.passes_all_gates.sum())}"
    )


if __name__ == "__main__":
    main()
