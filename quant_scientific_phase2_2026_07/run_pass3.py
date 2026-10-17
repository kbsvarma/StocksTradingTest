"""Pass III: barrier probabilities, posterior stopping and adaptive trails."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    HERE, PRODUCTION, START, SYMS, load_bars, production_signal,
    score_trade_sets, update_ledger,
)
from run_stage_a import normalized_returns, roll_clean_log_returns, simulate_direction
import rotation_grid as rg
import strategy


TRADE_COLUMNS = [
    "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
    "reason", "dir", "qty",
]


def kalman_mean(bars: pd.DataFrame, half_life: int = 120) -> pd.Series:
    y = normalized_returns(bars).to_numpy(dtype=float)
    phi = 2.0 ** (-1.0 / half_life)
    stationary_var = .10 ** 2
    process_var = stationary_var * (1.0 - phi * phi)
    mean, variance = 0.0, stationary_var
    output = np.full(len(y), np.nan)
    for i, observation in enumerate(y):
        predicted_mean = phi * mean
        predicted_var = phi * phi * variance + process_var
        if np.isfinite(observation):
            gain = predicted_var / (predicted_var + 1.0)
            mean = predicted_mean + gain * (observation - predicted_mean)
            variance = (1.0 - gain) * predicted_var
            output[i] = mean
        else:
            mean, variance = predicted_mean, predicted_var
    return pd.Series(output, index=bars.index)


def upper_barrier_probability(mu: np.ndarray, variance: np.ndarray,
                              upper: np.ndarray,
                              lower: np.ndarray) -> np.ndarray:
    """P(hit +upper before -lower) for arithmetic Brownian motion."""
    output = np.full(len(mu), np.nan)
    valid = ((variance > 0) & (upper > 0) & (lower > 0)
             & np.isfinite(mu + variance + upper + lower))
    z = np.zeros(len(mu))
    z[valid] = 2.0 * mu[valid] / variance[valid]
    nearly_zero = valid & (np.abs(z * (upper + lower)) < 1e-7)
    output[nearly_zero] = lower[nearly_zero] / (
        upper[nearly_zero] + lower[nearly_zero])
    positive = valid & ~nearly_zero & (z > 0)
    output[positive] = (-np.expm1(-z[positive] * lower[positive])
                        / -np.expm1(-z[positive]
                                    * (upper[positive] + lower[positive])))
    negative = valid & ~nearly_zero & (z < 0)
    # Stable algebra for negative drift, avoiding exp(large positive).
    output[negative] = (
        np.exp(np.maximum(z[negative] * upper[negative], -700.0))
        * (-np.expm1(z[negative] * lower[negative]))
        / (-np.expm1(z[negative]
                     * (upper[negative] + lower[negative]))))
    return np.clip(output, 0.0, 1.0)


def barrier_direction(bars: pd.DataFrame, symbol: str,
                      target_atr: float) -> pd.Series:
    frame = strategy.signal_frame(bars, PRODUCTION)
    returns = roll_clean_log_returns(bars)
    prior_vol = returns.ewm(halflife=48, adjust=False,
                            min_periods=240).std().shift(1)
    scale = prior_vol.to_numpy(dtype=float) * bars["close"].to_numpy(dtype=float)
    drift = kalman_mean(bars).to_numpy(dtype=float) * scale
    variance = scale * scale
    atr = frame["atr"].to_numpy(dtype=float)
    upper, lower = target_atr * atr, 2.0 * atr
    probability = upper_barrier_probability(drift, variance, upper, lower)
    expected_points = probability * upper - (1.0 - probability) * lower
    expected_dollars = expected_points * rg.MULT[symbol] - 3.50
    return (frame["signal"].astype(bool)
            & pd.Series(expected_dollars, index=bars.index).gt(0)).astype(np.int8)


def wonham_bull_probability(bars: pd.DataFrame) -> pd.Series:
    observations = normalized_returns(bars).to_numpy(dtype=float)
    q = 1.0 / 240.0
    posterior = np.array([0.5, 0.5])
    means = np.array([-0.10, 0.10])
    transition = np.array([[1.0 - q, q], [q, 1.0 - q]])
    output = np.full(len(observations), np.nan)
    for i, observation in enumerate(observations):
        prior = posterior @ transition
        if np.isfinite(observation):
            log_likelihood = -0.5 * (observation - means) ** 2
            likelihood = np.exp(log_likelihood - log_likelihood.max())
            posterior = prior * likelihood
            posterior /= posterior.sum()
            output[i] = posterior[1]
        else:
            posterior = prior
    return pd.Series(output, index=bars.index)


def _append(rows: list, entry_time, exit_time, symbol, entry, exit_px,
            reason: str) -> None:
    rows.append((entry_time, exit_time, symbol, entry, exit_px,
                 exit_px - entry, reason, 1, 1))


def simulate_posterior_exit(bars: pd.DataFrame, symbol: str,
                            probability: pd.Series,
                            enter_threshold: float,
                            exit_threshold: float) -> pd.DataFrame:
    frame = strategy.signal_frame(bars, PRODUCTION)
    signal = (frame["signal"].astype(bool)
              & probability.ge(enter_threshold)).to_numpy(dtype=bool)
    p = probability.to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    opens, highs, lows, closes = (
        bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    rolls = bars["roll"].to_numpy(dtype=bool)
    times = bars.index
    rows: list[tuple] = []
    position = pending_entry = pending_exit = False
    entry = stop = high_water = np.nan
    entry_time = None
    for i in range(len(bars)):
        if pending_entry and rolls[i]:
            pending_entry = False
        if pending_exit and position:
            _append(rows, entry_time, times[i], symbol, entry, opens[i],
                    "posterior_exit")
            position = pending_exit = False
        if pending_entry:
            position, pending_entry = True, False
            entry = high_water = opens[i]
            entry_time = times[i]
            stop = entry - PRODUCTION["k_trail"] * atr[i - 1]
            if not np.isfinite(stop):
                position = False
        if position and rolls[i]:
            exit_px = closes[i - 1] if i else opens[i]
            _append(rows, entry_time, times[i], symbol, entry, exit_px, "roll")
            position = pending_exit = False
        if position:
            if lows[i] <= stop:
                _append(rows, entry_time, times[i], symbol, entry,
                        min(stop, opens[i]), "stop")
                position = pending_exit = False
            else:
                high_water = max(high_water, highs[i])
                stop = max(stop, high_water - PRODUCTION["k_trail"] * atr[i])
                if np.isfinite(p[i]) and p[i] <= exit_threshold:
                    pending_exit = True
        if not position and not pending_entry and not pending_exit and signal[i]:
            pending_entry = True
    trades = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    return trades[trades["entry_time"] >= START].reset_index(drop=True)


def simulate_posterior_trail(bars: pd.DataFrame, symbol: str,
                             probability: pd.Series,
                             maximum_k: float) -> pd.DataFrame:
    frame = strategy.signal_frame(bars, PRODUCTION)
    signal = frame["signal"].to_numpy(dtype=bool)
    p = probability.to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    opens, highs, lows, closes = (
        bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    rolls = bars["roll"].to_numpy(dtype=bool)
    times = bars.index
    rows: list[tuple] = []
    position = pending = False
    pending_k = trade_k = 2.0
    entry = stop = high_water = np.nan
    entry_time = None
    for i in range(len(bars)):
        if pending and rolls[i]:
            pending = False
        if pending:
            position, pending = True, False
            trade_k = pending_k
            entry = high_water = opens[i]
            entry_time = times[i]
            stop = entry - trade_k * atr[i - 1]
            if not np.isfinite(stop):
                position = False
        if position and rolls[i]:
            exit_px = closes[i - 1] if i else opens[i]
            _append(rows, entry_time, times[i], symbol, entry, exit_px, "roll")
            position = False
        if position:
            if lows[i] <= stop:
                _append(rows, entry_time, times[i], symbol, entry,
                        min(stop, opens[i]), "stop")
                position = False
            else:
                high_water = max(high_water, highs[i])
                stop = max(stop, high_water - trade_k * atr[i])
        if not position and not pending and signal[i]:
            strength = np.clip(2.0 * p[i] - 1.0, 0.0, 1.0)
            if np.isfinite(strength):
                pending_k = 2.0 + (maximum_k - 2.0) * strength
                pending = True
    trades = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    return trades[trades["entry_time"] >= START].reset_index(drop=True)


def run() -> pd.DataFrame:
    bars = load_bars()
    probabilities = {symbol: wonham_bull_probability(bars[symbol])
                     for symbol in SYMS}
    trade_sets: dict[str, pd.DataFrame] = {}
    signals = {
        symbol: {
            "production_fixed1": production_signal(bars[symbol]),
            "barrier_target2": barrier_direction(bars[symbol], symbol, 2.0),
            "barrier_target4": barrier_direction(bars[symbol], symbol, 4.0),
        } for symbol in SYMS
    }
    for name in signals[SYMS[0]]:
        trade_sets[name] = pd.concat([
            simulate_direction(bars[symbol], symbol, signals[symbol][name])
            for symbol in SYMS
        ], ignore_index=True)
    for enter, leave, name in ((.60, .40, "wonham_p60_p40"),
                               (.70, .30, "wonham_p70_p30")):
        trade_sets[name] = pd.concat([
            simulate_posterior_exit(bars[symbol], symbol,
                                    probabilities[symbol], enter, leave)
            for symbol in SYMS
        ], ignore_index=True)
    for maximum, name in ((4.0, "posterior_trail_k4"),
                          (7.0, "posterior_trail_k7")):
        trade_sets[name] = pd.concat([
            simulate_posterior_trail(bars[symbol], symbol,
                                     probabilities[symbol], maximum)
            for symbol in SYMS
        ], ignore_index=True)
    results, daily = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass3_results.csv", index=False)
    daily.to_parquet(HERE / "pass3_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass3_trades.parquet")
    update_ledger("III", "pass3_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
