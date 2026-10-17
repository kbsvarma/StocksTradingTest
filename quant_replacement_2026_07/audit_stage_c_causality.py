"""Direct future-perturbation and opportunity-set audit for Stage C."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import PRODUCTION, SYMS  # noqa: E402
from run_stage_c import (  # noqa: E402
    causal_predictions, feature_frames, teacher_trades,
)


def run():
    rg.SCFG = dict(PRODUCTION)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    features = feature_frames(bars)
    cutoff = pd.Timestamp("2023-01-01", tz="UTC")

    changed_bars = {symbol: frame.copy() for symbol, frame in bars.items()}
    for symbol, frame in changed_bars.items():
        mask = frame.index >= cutoff
        wave = 1.0 + 0.25 * np.sin(np.arange(mask.sum()) / 17.0)
        for column in ("open", "high", "low", "close"):
            frame.loc[mask, column] = frame.loc[mask, column].to_numpy() * wave
    changed_features = feature_frames(changed_bars)

    for symbol in SYMS:
        pd.testing.assert_frame_equal(
            features[symbol].loc[features[symbol].index < cutoff],
            changed_features[symbol].loc[
                changed_features[symbol].index < cutoff],
            check_exact=True,
        )
        original_signal = strategy.signal_frame(
            bars[symbol], PRODUCTION)["signal"]
        changed_signal = strategy.signal_frame(
            changed_bars[symbol], PRODUCTION)["signal"]
        pd.testing.assert_series_equal(
            original_signal.loc[original_signal.index < cutoff],
            changed_signal.loc[changed_signal.index < cutoff],
            check_exact=True,
        )

    teachers = teacher_trades(bars, features)
    mutated_teachers = teachers.copy()
    future = mutated_teachers["exit_time"] >= cutoff
    mutated_teachers.loc[future, "y"] = (
        mutated_teachers.loc[future, "y"] * -10_000.0 + 123.0)
    original = causal_predictions(
        bars, features, teachers, penalties=(10.0,))["ridge10_mean"]
    mutated = causal_predictions(
        bars, features, mutated_teachers,
        penalties=(10.0,))["ridge10_mean"]

    opportunity_counts = {}
    for symbol in SYMS:
        pd.testing.assert_series_equal(
            original[symbol].loc[original[symbol].index < cutoff],
            mutated[symbol].loc[mutated[symbol].index < cutoff],
            check_exact=True,
        )
        base = strategy.signal_frame(bars[symbol], PRODUCTION)[
            "signal"].astype(np.int8)
        if bool((original[symbol] > base).any()):
            raise AssertionError(f"{symbol}: meta-filter created a new entry")
        opportunity_counts[symbol] = {
            "baseline": int(base.sum()),
            "accepted": int(original[symbol].sum()),
        }

    result = {
        "status": "PASS",
        "cutoff": cutoff.isoformat(),
        "future_market_perturbation": "prior features and signals identical",
        "future_label_perturbation": "prior decisions identical",
        "opportunity_subset": opportunity_counts,
    }
    (HERE / "stage_d_causality_audit.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run()
