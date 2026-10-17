"""Pass VIII: frozen robustness surface around the posterior-trail lead."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    HERE, SYMS, load_bars, production_signal, score_trade_sets, update_ledger,
)
from run_adaptive_rotation import baseline_policy
from run_pass7 import simulate_capped_trail
from run_stage_a import normalized_returns


def state_probability(bars: pd.DataFrame, mean: float,
                      duration: int) -> pd.Series:
    observations = normalized_returns(bars).to_numpy(dtype=float)
    q = 1.0 / duration
    transition = np.array([[1.0 - q, q], [q, 1.0 - q]])
    means = np.array([-mean, mean])
    posterior = np.array([.5, .5])
    output = np.full(len(observations), np.nan)
    for i, observation in enumerate(observations):
        prior = posterior @ transition
        if np.isfinite(observation):
            log_likelihood = -.5 * (observation - means) ** 2
            likelihood = np.exp(log_likelihood - log_likelihood.max())
            posterior = prior * likelihood
            posterior /= posterior.sum()
            output[i] = posterior[1]
        else:
            posterior = prior
    return pd.Series(output, index=bars.index)


def run() -> pd.DataFrame:
    bars = load_bars()
    directions = {symbol: production_signal(bars[symbol]) for symbol in SYMS}
    daily_prices = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
                    for symbol in SYMS}
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    policy = baseline_policy(daily_prices, common)
    probabilities = {
        (mean, duration): {
            symbol: state_probability(bars[symbol], mean, duration)
            for symbol in SYMS
        }
        for mean in (.05, .10, .20)
        for duration in (120, 240, 480, 720)
    }
    default = probabilities[(.10, 240)]
    trade_sets = {}
    for maximum in range(3, 11):
        name = f"dynamic_kmax{maximum}"
        print(name, flush=True)
        trade_sets[name] = simulate_capped_trail(
            directions, policy, bars, 3, default, "posterior",
            maximum_k=float(maximum))
    for mean in (.05, .10, .20):
        for duration in (120, 240, 480, 720):
            name = f"state_m{int(mean * 100):02d}_d{duration}"
            print(name, flush=True)
            trade_sets[name] = simulate_capped_trail(
                directions, policy, bars, 3, probabilities[(mean, duration)],
                "posterior", maximum_k=7.0)
    for shape in ("linear", "square", "sqrt", "step"):
        name = f"shape_{shape}"
        print(name, flush=True)
        trade_sets[name] = simulate_capped_trail(
            directions, policy, bars, 3, default, "posterior",
            maximum_k=7.0, shape=shape)
    for trail in range(2, 11):
        name = f"static_k{trail}"
        print(name, flush=True)
        mode = "k2" if trail == 2 else "k7"
        trade_sets[name] = simulate_capped_trail(
            directions, policy, bars, 3, default, mode,
            maximum_k=float(trail))
    results, daily = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass8_results.csv", index=False)
    daily.to_parquet(HERE / "pass8_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass8_trades.parquet")
    update_ledger("VIII", "pass8_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
