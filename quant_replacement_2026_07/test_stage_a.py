import numpy as np
import pandas as pd

from run_stage_a import (bayesian_drift_score, hidden_state_score,
                         first_passage, normalized_ema_score,
                         roll_clean_log_returns)
from run_stage_b import allocate_capacity, inverse_sqrt, largest_remainder


def sample_bars(periods=2_000):
    index = pd.date_range("2020-01-01", periods=periods, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(np.sin(np.arange(periods) / 30) * .001))
    return pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * .999,
        "close": close, "roll": np.zeros(periods, dtype=int),
    }, index=index)


def test_roll_return_is_removed():
    bars = sample_bars(10)
    bars.iloc[5, bars.columns.get_loc("close")] *= 2
    bars.iloc[5, bars.columns.get_loc("roll")] = 1
    returns = roll_clean_log_returns(bars)
    assert returns.iloc[5] == 0
    assert returns.iloc[4] != 0


def test_continuous_scores_cannot_read_future_prices():
    bars = sample_bars()
    scores = [normalized_ema_score, bayesian_drift_score,
              lambda frame: hidden_state_score(frame, .10, 120)]
    for make_score in scores:
        before = make_score(bars)
        changed = bars.copy()
        changed.loc[changed.index[1_500]:, ["open", "high", "low", "close"]] *= 3
        after = make_score(changed)
        pd.testing.assert_series_equal(before.loc[:bars.index[1_499]],
                                       after.loc[:bars.index[1_499]])


def test_first_passage_requires_zero_reset():
    score = pd.Series([0.0, .6, .8, .4, .7, -.1, .8])
    assert first_passage(score, .5).tolist() == [0, 1, 0, 0, 0, 0, 1]


def test_largest_remainder_is_bounded_integer_allocation():
    np.testing.assert_array_equal(largest_remainder([2.0, 1.0, -1.0]),
                                  [2, 1, 0])
    assert largest_remainder([0.2, 0.3, 0.5]).sum() == 3
    assert largest_remainder([-1.0, -2.0, -3.0]).sum() == 0


def test_inverse_sqrt_whitens_a_well_conditioned_matrix():
    correlation = np.array([[1.0, .5, .2], [.5, 1.0, .1], [.2, .1, 1.0]])
    transform = inverse_sqrt(correlation)
    np.testing.assert_allclose(transform @ correlation @ transform.T,
                               np.eye(3), atol=1e-10)


def test_capacity_priority_solves_integer_linear_objective():
    requested = np.array([2, 2, 1])
    np.testing.assert_array_equal(
        allocate_capacity(requested, 3, np.array([1.0, 3.0, 2.0])),
        np.array([0, 2, 1]),
    )


def test_capacity_missing_score_falls_back_to_proportional_rule():
    requested = np.array([2, 1, 1])
    expected = allocate_capacity(requested, 3)
    actual = allocate_capacity(requested, 3, np.array([1.0, np.nan, 2.0]))
    np.testing.assert_array_equal(actual, expected)
