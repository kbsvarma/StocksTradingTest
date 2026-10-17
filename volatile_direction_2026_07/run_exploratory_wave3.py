#!/usr/bin/env python3
"""Post-holdout current-shock selector exploration."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from run_matrix import (
    HOLDS,
    add_features,
    bh_qvalues,
    direction_columns,
    signed,
    simulate,
    summarize,
)


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
SELECTORS3 = (
    "ABS_GAP",
    "GAP_Z",
    "ABS_DAY",
    "DAY_Z",
    "ABS_REL_RET1",
    "SURPRISE",
)
DIRECTIONS3 = (
    "GAP_FADE",
    "GAP_FOLLOW",
    "DAY_FADE",
    "DAY_FOLLOW",
    "REL_MR1",
    "REL_MOM1",
)


def build_eligible(
    panel: pd.DataFrame,
    universe: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    featured = direction_columns(add_features(panel))
    featured["month"] = featured["day"].str[:7]
    eligible = featured.merge(
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
        "gap",
        "session_return",
        "ma200",
    ]
    eligible = eligible.dropna(subset=required).copy()
    median_ret1 = eligible.groupby("day")["ret1"].transform("median")
    relative1 = eligible["ret1"] - median_ret1
    eligible["ABS_GAP"] = eligible["gap"].abs()
    eligible["GAP_Z"] = eligible["gap"].abs() / eligible["ATR20"].clip(
        lower=1e-6
    )
    eligible["ABS_DAY"] = eligible["session_return"].abs()
    eligible["DAY_Z"] = eligible["session_return"].abs() / eligible[
        "ATR20"
    ].clip(lower=1e-6)
    eligible["ABS_REL_RET1"] = relative1.abs()
    eligible["REL_SHOCK_Z"] = relative1.abs() / eligible["RV20"].clip(
        lower=1e-6
    )
    surprise_parts = [
        eligible.groupby("day")[column].rank(pct=True)
        for column in ("GAP_Z", "DAY_Z", "REL_SHOCK_Z", "VOL_SPIKE")
    ]
    eligible["SURPRISE"] = sum(surprise_parts)

    eligible["GAP_FADE"] = -signed(eligible["gap"], eligible["ret1"])
    eligible["GAP_FOLLOW"] = signed(eligible["gap"], eligible["ret1"])
    eligible["DAY_FADE"] = -signed(
        eligible["session_return"], eligible["ret1"]
    )
    eligible["DAY_FOLLOW"] = signed(
        eligible["session_return"], eligible["ret1"]
    )
    eligible["REL_MR1"] = -signed(relative1, eligible["ret1"])
    eligible["REL_MOM1"] = signed(relative1, eligible["ret1"])
    return eligible, featured


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    eligible, featured = build_eligible(panel, universe)
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    lookup = featured.set_index(["day", "raw_symbol"])
    ledgers: list[pd.DataFrame] = []
    summaries: list[dict] = []
    for selector in SELECTORS3:
        index = eligible.groupby("day")[selector].idxmax()
        selection = eligible.loc[index].sort_values("day").copy()
        for direction in DIRECTIONS3:
            for hold in HOLDS:
                trades = simulate(
                    selection,
                    lookup,
                    calendar,
                    selector,
                    direction,
                    hold,
                )
                ledgers.append(trades)
                summaries.append(summarize(trades, len(selection)))
    all_trades = pd.concat(ledgers, ignore_index=True)
    summary = pd.DataFrame(summaries)

    earlier = pd.read_csv(BASE / f"all_model_fdr_{args.label}.csv")[
        ["selector", "direction_rule", "hold", "holdout_p", "wave"]
    ]
    combined = pd.concat(
        [
            earlier,
            summary[
                ["selector", "direction_rule", "hold", "holdout_p"]
            ].assign(wave="posthoc_wave3"),
        ],
        ignore_index=True,
    )
    combined["combined_q_384"] = bh_qvalues(combined["holdout_p"])
    combined.to_csv(BASE / f"all_384_fdr_{args.label}.csv", index=False)
    wave3_q = combined[combined["wave"] == "posthoc_wave3"][
        ["selector", "direction_rule", "hold", "combined_q_384"]
    ]
    summary = summary.merge(
        wave3_q,
        on=["selector", "direction_rule", "hold"],
        validate="one_to_one",
    )
    summary["passes_confirmatory_gates"] = False
    summary = summary.sort_values("total_pnl", ascending=False)
    all_trades.to_csv(BASE / f"wave3_trades_{args.label}.csv", index=False)
    summary.to_csv(BASE / f"wave3_summary_{args.label}.csv", index=False)
    yearly = (
        all_trades.assign(year=all_trades["signal_day"].str[:4])
        .groupby(["selector", "direction_rule", "hold", "year"])["net_pnl"]
        .sum()
        .reset_index()
    )
    yearly.to_csv(BASE / f"wave3_yearly_{args.label}.csv", index=False)
    print(summary.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
