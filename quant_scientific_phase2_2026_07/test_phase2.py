from __future__ import annotations

import numpy as np
import pandas as pd

from common import HERE, load_bars
from run_pass3 import upper_barrier_probability
from run_pass4 import hedge_weights
from run_pass8 import state_probability
from run_adaptive_rotation import maximum_exposure


def test_barrier_probability_has_correct_zero_and_drift_limits():
    result = upper_barrier_probability(
        np.array([0.0, .10, -.10]), np.ones(3),
        np.full(3, 2.0), np.full(3, 2.0))
    assert result[0] == .5
    assert result[1] > .5
    assert result[2] < .5
    assert np.isclose(result[1] + result[2], 1.0)


def test_state_filter_is_invariant_to_future_price_mutation():
    bars = load_bars()["NQ"]
    cutoff = pd.Timestamp("2023-01-01", tz="UTC")
    original = state_probability(bars, .10, 240)
    changed = bars.copy()
    future = changed.index >= cutoff
    changed.loc[future, ["open", "high", "low", "close"]] *= 1.37
    mutated = state_probability(changed, .10, 240)
    pd.testing.assert_series_equal(original[original.index < cutoff],
                                   mutated[mutated.index < cutoff])


def test_hedge_weights_use_only_prior_daily_rewards():
    index = pd.date_range("2020-01-01", periods=100, tz="UTC")
    daily = pd.DataFrame({"a": np.arange(100.0),
                          "b": np.arange(100.0)[::-1]}, index=index)
    changed = daily.copy()
    changed.iloc[80:] *= -1000
    original_weight = hedge_weights(daily, .20)
    changed_weight = hedge_weights(changed, .20)
    pd.testing.assert_frame_equal(original_weight.iloc[:81],
                                  changed_weight.iloc[:81])


def test_archived_frontier_candidate_obeys_strict_cap_three():
    trades = pd.read_parquet(HERE / "pass9_trades.parquet").xs(
        "base_dynamic_k20", level="candidate")
    assert maximum_exposure(trades) <= 3


def test_pass7_baseline_matches_verified_phase1_engine():
    current = pd.read_csv(HERE / "pass7_results.csv").set_index("candidate")
    phase1 = pd.read_csv(
        HERE.parent / "quant_replacement_2026_07/stage_c_results.csv")
    reference = phase1[(phase1["candidate"] == "donchian")
                       & (phase1["mode"] == "cap3")].iloc[0]
    assert np.isclose(current.loc["production_k2_cap3", "net"],
                      reference["net"])
    assert np.isclose(current.loc["production_k2_cap3", "maxDD"],
                      reference["maxDD"])
