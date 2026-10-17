"""Execution-boundary and causality audit of the K2/K15 state-switch lead."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import HERE, SYMS, load_bars, production_signal
from run_adaptive_rotation import baseline_policy, maximum_exposure
from run_pass7 import simulate_capped_trail
from run_pass8 import state_probability


def build_candidate(bars):
    directions = {symbol: production_signal(bars[symbol]) for symbol in SYMS}
    probabilities = {
        symbol: state_probability(bars[symbol], .10, 240) for symbol in SYMS}
    daily = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
             for symbol in SYMS}
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    policy = baseline_policy(daily, common)
    return simulate_capped_trail(
        directions, policy, bars, 3, probabilities, "posterior",
        maximum_k=15.0, shape="step")


def assert_entry_identity(trades, bars):
    failures = []
    for trade in trades.itertuples(index=False):
        index = bars[trade.sym].index
        i = index.get_loc(trade.entry_time)
        if i == 0 or int(production_signal(bars[trade.sym]).iloc[i - 1]) != 1:
            failures.append((trade.sym, str(trade.entry_time)))
    return failures


def assert_roll_identity(trades, bars):
    failures = []
    for trade in trades.itertuples(index=False):
        market = bars[trade.sym]
        start = market.index.get_loc(trade.entry_time)
        end = market.index.get_loc(trade.exit_time)
        roll_times = market.index[start:end + 1][
            market["roll"].iloc[start:end + 1].to_numpy(dtype=bool)]
        if len(roll_times):
            if trade.reason != "roll" or roll_times[0] != trade.exit_time:
                failures.append((trade.sym, str(trade.entry_time),
                                 str(trade.exit_time), trade.reason))
        elif trade.reason == "roll":
            failures.append((trade.sym, str(trade.entry_time),
                             str(trade.exit_time), trade.reason))
    return failures


def run() -> dict:
    bars = load_bars()
    archived = pd.read_parquet(HERE / "pass9_trades.parquet").xs(
        "base_step_k15", level="candidate").reset_index(drop=True)
    regenerated = build_candidate(bars).reset_index(drop=True)
    columns = ["entry_time", "exit_time", "sym", "entry_px", "exit_px",
               "points", "reason", "dir", "qty"]
    def canonical(frame):
        return frame[columns].sort_values(columns, kind="stable").reset_index(
            drop=True)

    pd.testing.assert_frame_equal(canonical(archived), canonical(regenerated),
                                  check_exact=True)

    cutoff = pd.Timestamp("2023-01-01", tz="UTC")
    changed = {symbol: frame.copy() for symbol, frame in bars.items()}
    for symbol in SYMS:
        mask = changed[symbol].index >= cutoff
        factors = np.linspace(1.17, 1.43, int(mask.sum()))
        changed[symbol].loc[mask, ["open", "high", "low", "close"]] *= (
            factors[:, None])
    mutated = build_candidate(changed)
    original_past = regenerated[regenerated["exit_time"] < cutoff].reset_index(
        drop=True)
    mutated_past = mutated[mutated["exit_time"] < cutoff].reset_index(drop=True)
    pd.testing.assert_frame_equal(canonical(original_past), canonical(mutated_past),
                                  check_exact=True)

    entry_failures = assert_entry_identity(regenerated, bars)
    roll_failures = assert_roll_identity(regenerated, bars)
    report = {
        "archived_replay_exact": True,
        "future_mutation_pre_cutoff_exact": True,
        "cutoff": str(cutoff),
        "pre_cutoff_trades_compared": len(original_past),
        "next_open_entry_failures": entry_failures,
        "roll_identity_failures": roll_failures,
        "max_concurrent_contracts": maximum_exposure(regenerated),
        "strict_cap_pass": maximum_exposure(regenerated) <= 3,
        "trade_count": len(regenerated),
    }
    assert not entry_failures
    assert not roll_failures
    assert report["strict_cap_pass"]
    (HERE / "candidate_causality_audit.json").write_text(
        json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    run()
