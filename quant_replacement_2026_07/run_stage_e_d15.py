"""Frozen fusion: D15/K7 adaptive rotation plus causal expectancy filter."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (  # noqa: E402
    D15, START, SYMS, allocation, baseline_policy, feature_library,
)
from run_stage_a import pnl_stats  # noqa: E402
from run_stage_b import attach_initial_risk, risk_stats, simulate_capped  # noqa: E402
from run_stage_c import (  # noqa: E402
    causal_predictions, feature_frames, teacher_trades,
)


def evaluate(directions, policy, bars):
    trades = simulate_capped(directions, policy, bars, 3, config=D15)
    pnl, stats = pnl_stats(trades, bars)
    risky = attach_initial_risk(trades, bars, config=D15)
    return trades, pnl, stats | risk_stats(risky)


def run():
    rg.START, rg.SCFG = START, dict(D15)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    daily, index, scores = feature_library(bars)
    policies = {
        "weekly": baseline_policy(daily, index),
        "adaptive": allocation(scores["week35_R5_W2"], "graduated"),
    }
    features = feature_frames(bars, config=D15)
    teachers = teacher_trades(bars, features, config=D15)
    teachers.to_parquet(HERE / "stage_e_d15_teacher.parquet", index=False)
    predictions = causal_predictions(
        bars, features, teachers, penalties=(10.0, 100.0), config=D15)
    base = {symbol: strategy.signal_frame(
        bars[symbol], D15)["signal"].astype("int8") for symbol in SYMS}

    rows, paths = [], {}
    direction_sets = {
        "unfiltered": base,
        "ridge10": predictions["ridge10_mean"],
        "ridge100": predictions["ridge100_mean"],
    }
    for policy_name, policy in policies.items():
        for filter_name, directions in direction_sets.items():
            label = f"{policy_name}_{filter_name}"
            _, pnl, stats = evaluate(directions, policy, bars)
            rows.append({"candidate": label, **stats})
            paths[label] = pnl.groupby(pnl.index.normalize()).sum()
            print(label, stats, flush=True)

    result = pd.DataFrame(rows)
    result.to_csv(HERE / "stage_e_d15_results.csv", index=False)
    pd.concat(paths, axis=1).fillna(0.0).to_parquet(
        HERE / "stage_e_d15_daily_pnl.parquet")
    print("\n", result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result


if __name__ == "__main__":
    run()
