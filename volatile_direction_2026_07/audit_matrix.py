#!/usr/bin/env python3
"""Independent invariants and mutation audit for a completed matrix run."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from run_matrix import (
    BOOK_NOTIONAL,
    COMMISSION_PER_SHARE,
    DIRECTIONS,
    HOLDS,
    SELECTORS,
    add_features,
    direction_columns,
    selected_rows,
)


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def assert_close(actual: pd.Series, expected: pd.Series, name: str) -> None:
    if not np.allclose(
        actual.to_numpy(float),
        expected.to_numpy(float),
        rtol=1e-10,
        atol=1e-10,
        equal_nan=True,
    ):
        delta = np.nanmax(np.abs(actual.to_numpy(float) - expected.to_numpy(float)))
        raise AssertionError(f"{name} mismatch; max absolute error={delta}")


def mutation_audit(
    panel: pd.DataFrame,
    universe: pd.DataFrame,
    cutoff: str,
) -> None:
    """Future price corruption must not alter any pre-cutoff signal."""
    original = direction_columns(add_features(panel))
    mutated_input = panel.copy()
    future = mutated_input["day"] >= cutoff
    symbol_code = pd.factorize(mutated_input.loc[future, "raw_symbol"])[0]
    multiplier = 0.35 + (symbol_code % 17) / 10.0
    for column in ("adj_open", "adj_high", "adj_low", "adj_close"):
        mutated_input.loc[future, column] *= multiplier
    mutated_input.loc[future, "clean_return"] = (
        np.sin(np.arange(future.sum())) * 0.75
    )
    mutated_input.loc[future, "volume"] = (
        1 + (np.arange(future.sum()) * 7919) % 10_000_000
    )
    mutated = direction_columns(add_features(mutated_input))

    original_selections, _ = selected_rows(original, universe)
    mutated_selections, _ = selected_rows(mutated, universe)
    compare_columns = ["day", "raw_symbol", *DIRECTIONS]
    for selector in SELECTORS:
        left = original_selections[selector]
        left = left[left["day"] < cutoff][compare_columns].reset_index(drop=True)
        right = mutated_selections[selector]
        right = right[right["day"] < cutoff][compare_columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(left, right, check_dtype=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument(
        "--mutation-cutoff",
        default=None,
        help="future-data mutation cutoff; default is midpoint session",
    )
    args = parser.parse_args()

    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    trades = pd.read_csv(BASE / f"trades_{args.label}.csv")
    summary = pd.read_csv(BASE / f"summary_{args.label}.csv")

    expected_cells = {
        (selector, direction, hold)
        for selector in SELECTORS
        for direction in DIRECTIONS
        for hold in HOLDS
    }
    actual_cells = set(
        summary[["selector", "direction_rule", "hold"]].itertuples(
            index=False, name=None
        )
    )
    assert actual_cells == expected_cells
    assert not summary.duplicated(["selector", "direction_rule", "hold"]).any()
    assert not trades.duplicated(
        ["selector", "direction_rule", "hold", "signal_day"]
    ).any()
    assert (trades["coverage"] <= 1.0).all() if "coverage" in trades else True

    signal = pd.to_datetime(trades["signal_day"])
    entry_day = pd.to_datetime(trades["entry_day"])
    exit_day = pd.to_datetime(trades["exit_day"])
    assert (signal < entry_day).all()
    assert (entry_day <= exit_day).all()

    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    calendar_position = pd.Series(np.arange(len(calendar)), index=calendar)
    signal_position = calendar_position.loc[signal].to_numpy()
    expected_entry = calendar[signal_position + 1]
    expected_exit = calendar[
        signal_position + trades["hold"].to_numpy(dtype=int)
    ]
    assert np.array_equal(entry_day.to_numpy(), expected_entry.to_numpy())
    assert np.array_equal(exit_day.to_numpy(), expected_exit.to_numpy())
    assert_close(
        trades["notional"],
        BOOK_NOTIONAL / trades["hold"],
        "equal-gross ticket",
    )

    signal_universe = universe.rename(columns={"symbol": "raw_symbol"}).copy()
    signal_universe["signal_day"] = (
        signal_universe["month"].astype(str) + "-01"
    )
    trade_cost = trades.copy()
    trade_cost["month"] = trade_cost["signal_day"].str[:7]
    trade_cost = trade_cost.merge(
        signal_universe[["month", "raw_symbol", "mega"]].rename(
            columns={"raw_symbol": "symbol"}
        ),
        on=["month", "symbol"],
        how="left",
        validate="many_to_one",
    )
    assert trade_cost["mega"].notna().all()
    half_spread = np.where(trade_cost["mega"], 0.0003, 0.0005)
    commission_return = (
        COMMISSION_PER_SHARE
        * (1.0 + trade_cost["exit_quantity_multiplier"])
        / trade_cost["raw_entry"]
    )
    calendar_days = np.maximum(
        1, (pd.to_datetime(trade_cost["exit_day"])
            - pd.to_datetime(trade_cost["entry_day"])).dt.days
    )
    short = trade_cost["side"].eq("SHORT")
    borrow = np.where(short, 0.0025 * calendar_days / 365.0, 0.0)
    borrow_stress = np.where(short, 0.25 * calendar_days / 365.0, 0.0)
    long_net = (
        trade_cost["exit"] * (1.0 - half_spread)
        - trade_cost["entry"] * (1.0 + half_spread)
    ) / trade_cost["entry"] - commission_return
    short_net = (
        trade_cost["entry"] * (1.0 - half_spread)
        - trade_cost["exit"] * (1.0 + half_spread)
    ) / trade_cost["entry"] - commission_return - borrow
    expected_net = pd.Series(np.where(short, short_net, long_net))
    long_stress = (
        trade_cost["exit"] * (1.0 - 2.0 * half_spread)
        - trade_cost["entry"] * (1.0 + 2.0 * half_spread)
    ) / trade_cost["entry"] - 2.0 * commission_return
    short_stress = (
        trade_cost["entry"] * (1.0 - 2.0 * half_spread)
        - trade_cost["exit"] * (1.0 + 2.0 * half_spread)
    ) / trade_cost["entry"] - 2.0 * commission_return - borrow_stress
    expected_stress = pd.Series(np.where(short, short_stress, long_stress))
    expected_gross = pd.Series(
        np.where(
            short,
            1.0 - trade_cost["exit"] / trade_cost["entry"],
            trade_cost["exit"] / trade_cost["entry"] - 1.0,
        )
    )
    assert_close(trade_cost["gross_return"], expected_gross, "gross return")
    assert_close(trade_cost["net_return"], expected_net, "net return")
    assert_close(trade_cost["stress_return"], expected_stress, "stress return")
    assert_close(
        trade_cost["net_pnl"],
        trade_cost["notional"] * expected_net,
        "net P&L",
    )
    assert (trade_cost["stress_pnl"] <= trade_cost["net_pnl"] + 1e-9).all()

    days = sorted(panel["day"].unique())
    cutoff = args.mutation_cutoff or days[len(days) // 2]
    mutation_audit(panel, universe, cutoff)

    print(
        "AUDIT PASS "
        f"cells={len(summary)} trades={len(trades):,} "
        f"mutation_cutoff={cutoff}"
    )


if __name__ == "__main__":
    main()
