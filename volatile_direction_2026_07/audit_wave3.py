#!/usr/bin/env python3
"""Causality and structure audit for post-holdout Wave 3."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from run_exploratory_wave3 import DIRECTIONS3, SELECTORS3, build_eligible
from run_matrix import HOLDS


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def selected(eligible: pd.DataFrame, selector: str) -> pd.DataFrame:
    index = eligible.groupby("day")[selector].idxmax()
    return eligible.loc[index].sort_values("day")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--cutoff", default="2022-06-10")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    original, _ = build_eligible(panel, universe)
    corrupted = panel.copy()
    future = corrupted["day"] >= args.cutoff
    sequence = np.arange(future.sum())
    multiplier = 0.2 + (sequence % 23) / 7.0
    for column in ("adj_open", "adj_high", "adj_low", "adj_close"):
        corrupted.loc[future, column] *= multiplier
    corrupted.loc[future, "clean_return"] = np.cos(sequence) * 0.80
    corrupted.loc[future, "volume"] = 1 + (sequence * 3571) % 20_000_000
    mutated, _ = build_eligible(corrupted, universe)

    columns = ["day", "raw_symbol", *DIRECTIONS3]
    for selector in SELECTORS3:
        left = selected(original, selector)
        left = left[left["day"] < args.cutoff][columns].reset_index(drop=True)
        right = selected(mutated, selector)
        right = right[right["day"] < args.cutoff][columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False)

    trades = pd.read_csv(BASE / f"wave3_trades_{args.label}.csv")
    cells = set(
        trades[["selector", "direction_rule", "hold"]].itertuples(
            index=False, name=None
        )
    )
    expected = {
        (selector, direction, hold)
        for selector in SELECTORS3
        for direction in DIRECTIONS3
        for hold in HOLDS
    }
    assert cells == expected
    assert not trades.duplicated(
        ["selector", "direction_rule", "hold", "signal_day"]
    ).any()
    assert (
        pd.to_datetime(trades["signal_day"])
        < pd.to_datetime(trades["entry_day"])
    ).all()
    assert (
        pd.to_datetime(trades["entry_day"])
        <= pd.to_datetime(trades["exit_day"])
    ).all()
    assert (trades["stress_pnl"] <= trades["net_pnl"] + 1e-9).all()

    abs_gap = selected(original, "ABS_GAP")[["day", "raw_symbol"]].rename(
        columns={"day": "signal_day", "raw_symbol": "symbol"}
    )
    candidate = trades[
        (trades["selector"] == "ABS_GAP")
        & (trades["direction_rule"] == "GAP_FADE")
        & (trades["hold"] == 1)
    ][["signal_day", "symbol"]]
    check = candidate.merge(
        abs_gap,
        on=["signal_day", "symbol"],
        how="left",
        indicator=True,
        validate="one_to_one",
    )
    assert check["_merge"].eq("both").all()
    print(
        "WAVE3 AUDIT PASS "
        f"cells={len(cells)} trades={len(trades):,} "
        f"mutation_cutoff={args.cutoff}"
    )


if __name__ == "__main__":
    main()
