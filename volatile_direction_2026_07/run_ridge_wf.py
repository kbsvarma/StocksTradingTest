#!/usr/bin/env python3
"""Expanding-window pooled ridge direction challenger."""

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
    simulate,
    summarize,
)


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
RIDGE_LAMBDA = 100.0
FEATURES = (
    "ret1",
    "ret5",
    "ret20",
    "gap",
    "session_return",
    "ATR20",
    "RV20",
    "VOL_SPIKE",
    "price_ma50",
    "price_ma200",
    "price_to_high252",
    "range_position20",
)


def design_panel(
    panel: pd.DataFrame,
    universe: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    featured = direction_columns(add_features(panel))
    featured["month"] = featured["day"].str[:7]
    eligible = featured.merge(
        universe[["month", "symbol", "rank", "mega"]],
        left_on=["month", "raw_symbol"],
        right_on=["month", "symbol"],
        how="inner",
    )
    eligible["price_ma50"] = eligible["adj_close"] / eligible["ma50"] - 1.0
    eligible["price_ma200"] = eligible["adj_close"] / eligible["ma200"] - 1.0
    width = eligible["roll_high20"] - eligible["roll_low20"]
    eligible["range_position20"] = (
        (eligible["adj_close"] - eligible["roll_low20"]) / width - 0.5
    )
    eligible[list(FEATURES)] = eligible[list(FEATURES)].replace(
        [np.inf, -np.inf], np.nan
    )
    eligible = eligible.dropna(subset=list(FEATURES)).copy()
    for feature in FEATURES:
        eligible[f"x_{feature}"] = (
            eligible.groupby("day")[feature].rank(pct=True) * 2.0 - 1.0
        )

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
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    return eligible, featured, calendar


def add_targets(
    eligible: pd.DataFrame,
    featured: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    hold: int,
) -> pd.DataFrame:
    positions = pd.Series(
        np.arange(len(calendar)),
        index=calendar.strftime("%Y-%m-%d"),
    )
    output = eligible.copy()
    signal_positions = output["day"].map(positions)
    valid = signal_positions.notna() & (
        signal_positions.astype("Int64") + hold < len(calendar)
    )
    output = output.loc[valid].copy()
    signal_positions = signal_positions.loc[valid].astype(int)
    output["entry_day"] = calendar[signal_positions + 1].strftime("%Y-%m-%d")
    output["target_exit_day"] = calendar[
        signal_positions + hold
    ].strftime("%Y-%m-%d")
    prices = featured[
        ["day", "raw_symbol", "adj_open", "adj_close"]
    ].drop_duplicates(["day", "raw_symbol"], keep="last")
    output = output.merge(
        prices[["day", "raw_symbol", "adj_open"]].rename(
            columns={"day": "entry_day", "adj_open": "target_entry"}
        ),
        on=["entry_day", "raw_symbol"],
        how="left",
        validate="many_to_one",
    )
    output = output.merge(
        prices[["day", "raw_symbol", "adj_close"]].rename(
            columns={"day": "target_exit_day", "adj_close": "target_exit"}
        ),
        on=["target_exit_day", "raw_symbol"],
        how="left",
        validate="many_to_one",
    )
    output["target"] = output["target_exit"] / output["target_entry"] - 1.0
    output["target_clipped"] = output["target"].clip(-0.30, 0.30)
    return output.dropna(subset=["target_clipped"]).copy()


def ridge_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("non-finite value reached ridge fit")
    design = np.column_stack([np.ones(len(x)), x])
    penalty = np.eye(design.shape[1]) * RIDGE_LAMBDA
    penalty[0, 0] = 0.0
    gram = np.einsum("ni,nj->ij", design, design, optimize=True)
    cross = np.einsum("ni,n->i", design, y, optimize=True)
    coefficients = np.linalg.solve(
        gram + penalty, cross
    )
    if not np.isfinite(coefficients).all():
        raise ValueError("ridge coefficients are not finite")
    return coefficients


def walk_forward_predictions(examples: pd.DataFrame) -> pd.DataFrame:
    x_columns = [f"x_{feature}" for feature in FEATURES]
    predictions: list[pd.DataFrame] = []
    for year in range(2020, 2027):
        cutoff = f"{year}-01-01"
        train = examples[examples["target_exit_day"] < cutoff]
        test = examples[examples["day"].str[:4].astype(int) == year].copy()
        if train.empty or test.empty:
            continue
        coefficients = ridge_fit(
            train[x_columns].to_numpy(float),
            train["target_clipped"].to_numpy(float),
        )
        test_design = np.column_stack(
            [np.ones(len(test)), test[x_columns].to_numpy(float)]
        )
        test["prediction"] = np.einsum(
            "ni,i->n", test_design, coefficients, optimize=True
        )
        predictions.append(
            test[["day", "raw_symbol", "prediction"]]
        )
    return pd.concat(predictions, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    eligible, featured, calendar = design_panel(panel, universe)
    lookup = featured.set_index(["day", "raw_symbol"])
    ledgers: list[pd.DataFrame] = []
    summaries: list[dict] = []

    for hold in HOLDS:
        examples = add_targets(eligible, featured, calendar, hold)
        predictions = walk_forward_predictions(examples)
        scored = eligible.merge(
            predictions,
            on=["day", "raw_symbol"],
            how="inner",
            validate="one_to_one",
        )
        scored["RIDGE_WF"] = np.sign(scored["prediction"])
        zero = scored["RIDGE_WF"] == 0
        scored.loc[zero, "RIDGE_WF"] = np.sign(scored.loc[zero, "ret20"]).replace(
            0, 1.0
        )
        for selector in SELECTORS:
            index = scored.groupby("day")[selector].idxmax()
            selection = scored.loc[index].sort_values("day").copy()
            trades = simulate(
                selection,
                lookup,
                calendar,
                selector,
                "RIDGE_WF",
                hold,
            )
            ledgers.append(trades)
            summaries.append(summarize(trades, len(selection)))

    all_trades = pd.concat(ledgers, ignore_index=True)
    summary = pd.DataFrame(summaries)
    primary = pd.read_csv(BASE / f"summary_{args.label}.csv")
    wave2 = pd.read_csv(BASE / f"wave2_summary_{args.label}.csv")
    combined = pd.concat(
        [
            primary[["selector", "direction_rule", "hold", "holdout_p"]].assign(
                wave="primary"
            ),
            wave2[["selector", "direction_rule", "hold", "holdout_p"]].assign(
                wave="wave2"
            ),
            summary[["selector", "direction_rule", "hold", "holdout_p"]].assign(
                wave="ridge"
            ),
        ],
        ignore_index=True,
    )
    combined["combined_q_276"] = bh_qvalues(combined["holdout_p"])
    combined.to_csv(BASE / f"all_model_fdr_{args.label}.csv", index=False)
    ridge_q = combined[combined["wave"] == "ridge"][
        ["selector", "direction_rule", "hold", "combined_q_276"]
    ]
    summary = summary.merge(
        ridge_q,
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
        & (summary["combined_q_276"] <= 0.10)
        & summary["beats_both_fixed_sides"]
    )
    summary = summary.sort_values("total_pnl", ascending=False)
    all_trades.to_csv(BASE / f"ridge_trades_{args.label}.csv", index=False)
    summary.to_csv(BASE / f"ridge_summary_{args.label}.csv", index=False)
    yearly = (
        all_trades.assign(year=all_trades["signal_day"].str[:4])
        .groupby(["selector", "direction_rule", "hold", "year"])["net_pnl"]
        .sum()
        .reset_index()
    )
    yearly.to_csv(BASE / f"ridge_yearly_{args.label}.csv", index=False)
    print(summary.to_string(index=False))
    print(
        f"\nexecuted={len(summary)} cells "
        f"survivors={int(summary.passes_all_gates.sum())}"
    )


if __name__ == "__main__":
    main()
