import numpy as np
import pandas as pd

from run_adaptive_rotation import allocation, cross_z, current_horizon_returns
from run_nq_only import causal_rolling_z
from run_online_experts import online_policy


def test_cross_z_is_cross_sectional_and_scale_invariant():
    index = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    frame = pd.DataFrame([[1.0, 2.0, 4.0], [10.0, 20.0, 40.0]],
                         index=index, columns=["NQ", "ES", "YM"])
    first = cross_z(frame)
    second = cross_z(frame * 7.0 + 3.0)
    np.testing.assert_allclose(first, second)
    np.testing.assert_allclose(first.sum(axis=1), 0.0, atol=1e-12)


def test_integer_allocations_always_sum_to_three():
    index = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    score = pd.DataFrame([[2.0, 1.0, -1.0], [.2, .1, 0.0],
                          [1.0, 1.0, 1.0]], index=index,
                         columns=["NQ", "ES", "YM"])
    for mode in ("hard", "conf", "graduated", "softmax"):
        result = allocation(score, mode)
        assert (result.sum(axis=1) == 3).all()
        assert (result >= 0).all().all()


def test_hourly_horizon_feature_does_not_read_future_closes():
    days = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    hours = days + pd.Timedelta(hours=12)
    bars, daily = {}, {}
    for offset, sym in enumerate(("NQ", "ES", "YM")):
        values = pd.Series(np.arange(10, dtype=float) + 100 + offset,
                           index=days)
        daily[sym] = values
        bars[sym] = pd.DataFrame({"close": values.to_numpy()}, index=hours)
    before = current_horizon_returns(bars, daily, hours, (3,))[3]
    altered = {s: frame.copy() for s, frame in bars.items()}
    altered["NQ"].loc[hours[-1], "close"] *= 10
    after = current_horizon_returns(altered, daily, hours, (3,))[3]
    pd.testing.assert_frame_equal(before.iloc[:-1], after.iloc[:-1])


def test_online_expert_future_change_cannot_change_past_policy():
    index = pd.date_range("2024-01-01", periods=200, freq="h", tz="UTC")
    closes = pd.DataFrame({
        "NQ": 100 + np.linspace(0, 5, len(index)),
        "ES": 100 + np.linspace(0, 3, len(index)),
        "YM": 100 + np.linspace(0, 1, len(index)),
    }, index=index)
    experts = {
        "nq": pd.DataFrame(np.tile([3, 0, 0], (len(index), 1)),
                           index=index, columns=closes.columns),
        "ym": pd.DataFrame(np.tile([0, 0, 3], (len(index), 1)),
                           index=index, columns=closes.columns),
    }
    first = online_policy(experts, closes, 24, 30.0, 63)
    changed = closes.copy()
    changed.loc[index[150]:, "YM"] *= 2
    second = online_policy(experts, changed, 24, 30.0, 63)
    pd.testing.assert_frame_equal(first.loc[:index[149]],
                                  second.loc[:index[149]])


def test_causal_time_series_z_cannot_read_future_values():
    index = pd.date_range("2020-01-01", periods=1_000, freq="h", tz="UTC")
    values = pd.Series(np.sin(np.arange(1_000) / 30.0), index=index)
    before = causal_rolling_z(values, window=200, min_periods=100)
    changed = values.copy()
    changed.loc[index[700]:] += 100.0
    after = causal_rolling_z(changed, window=200, min_periods=100)
    pd.testing.assert_series_equal(before.loc[:index[699]],
                                   after.loc[:index[699]])
