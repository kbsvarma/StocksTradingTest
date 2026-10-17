#!/usr/bin/env python3
"""Descriptive post-snapshot study of the one archived IBKR borrow file."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from run_matrix import add_features, direction_columns, selected_rows


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DATA = BASE / "data"
BORROW_PATH = (
    ROOT / "shares_research" / "data" / "borrow" /
    "borrow_2026-06-28.parquet"
)
SNAPSHOT_DAY = "2026-06-28"


def fee_group(fee: float) -> str:
    if not np.isfinite(fee):
        return "missing"
    if fee <= 0.25:
        return "<=0.25%"
    if fee <= 1.0:
        return "0.25-1%"
    if fee <= 10.0:
        return "1-10%"
    return ">10%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label", default="2018_2026", nargs="?")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    borrow = pd.read_parquet(BORROW_PATH).copy()
    borrow["symbol"] = borrow["symbol"].astype(str)
    borrow = borrow.drop_duplicates("symbol", keep="last")
    borrow["fee_group"] = borrow["fee_rate"].map(fee_group)
    borrow["low_availability"] = borrow["available"].le(100_000)

    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    sessions = calendar[calendar > pd.Timestamp(SNAPSHOT_DAY)]
    if len(sessions) < 20:
        raise RuntimeError("fewer than 20 post-snapshot sessions are available")
    entry_day = sessions[0].strftime("%Y-%m-%d")
    cohort_month = entry_day[:7]
    cohort = universe[universe["month"] == cohort_month][
        ["symbol", "rank", "mega"]
    ].merge(borrow, on="symbol", how="left", validate="one_to_one")

    prices = panel.set_index(["day", "raw_symbol"])
    entry = prices.loc[entry_day][["adj_open"]].rename(
        columns={"adj_open": "entry"}
    )
    cohort = cohort.merge(
        entry,
        left_on="symbol",
        right_index=True,
        how="left",
        validate="one_to_one",
    )
    for hold in (1, 5, 20):
        exit_day = sessions[hold - 1].strftime("%Y-%m-%d")
        exit_price = prices.loc[exit_day][["adj_close"]].rename(
            columns={"adj_close": f"exit_{hold}"}
        )
        cohort = cohort.merge(
            exit_price,
            left_on="symbol",
            right_index=True,
            how="left",
            validate="one_to_one",
        )
        cohort[f"return_{hold}"] = (
            cohort[f"exit_{hold}"] / cohort["entry"] - 1.0
        )
    cohort.to_csv(BASE / "borrow_snapshot_cohort.csv", index=False)

    rows: list[dict] = []
    for column, groups in (
        ("fee_group", ("<=0.25%", "0.25-1%", "1-10%", ">10%", "missing")),
        ("low_availability", (True, False)),
    ):
        for group in groups:
            block = cohort[cohort[column] == group]
            for hold in (1, 5, 20):
                values = block[f"return_{hold}"].dropna()
                rows.append(
                    {
                        "classification": column,
                        "group": str(group),
                        "hold_sessions": hold,
                        "n": len(values),
                        "mean_long_return": values.mean(),
                        "median_long_return": values.median(),
                        "mean_short_return": -values.mean(),
                    }
                )
    group_results = pd.DataFrame(rows)
    group_results.to_csv(BASE / "borrow_snapshot_group_results.csv", index=False)

    featured = direction_columns(add_features(panel))
    selections, _ = selected_rows(featured, universe)
    selection_rows = []
    borrow_labels = borrow[
        ["symbol", "fee_rate", "available", "fee_group", "low_availability"]
    ]
    for selector, selected in selections.items():
        recent = selected[selected["day"] > SNAPSHOT_DAY].merge(
            borrow_labels,
            on="symbol",
            how="left",
            validate="many_to_one",
        )
        recent["selector"] = selector
        selection_rows.append(
            recent[
                [
                    "day",
                    "selector",
                    "symbol",
                    "fee_rate",
                    "available",
                    "fee_group",
                    "low_availability",
                ]
            ]
        )
    selected_borrow = pd.concat(selection_rows, ignore_index=True)
    selected_borrow.to_csv(
        BASE / "borrow_snapshot_selected_names.csv", index=False
    )
    selected_counts = (
        selected_borrow.groupby(["selector", "fee_group"], dropna=False)
        .size()
        .rename("selected_days")
        .reset_index()
    )
    selected_counts.to_csv(
        BASE / "borrow_snapshot_selected_counts.csv", index=False
    )

    print(
        f"snapshot={SNAPSHOT_DAY} entry={entry_day} "
        f"cohort={len(cohort)} borrow_matched={cohort.fee_rate.notna().sum()}"
    )
    print(group_results.to_string(index=False))
    print("\nDaily selected-name borrow groups:")
    print(selected_counts.to_string(index=False))


if __name__ == "__main__":
    main()
