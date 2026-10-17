"""Pass IX: frozen boundary extension of the posterior-trail frontier."""
from __future__ import annotations

import pandas as pd

from common import (
    HERE, SYMS, load_bars, production_signal, score_trade_sets, update_ledger,
)
from run_adaptive_rotation import baseline_policy
from run_pass7 import simulate_capped_trail
from run_pass8 import state_probability


def run() -> pd.DataFrame:
    bars = load_bars()
    directions = {symbol: production_signal(bars[symbol]) for symbol in SYMS}
    daily_prices = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
                    for symbol in SYMS}
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    policy = baseline_policy(daily_prices, common)
    state_specs = {(mean, duration) for mean in (.10, .20, .30, .40)
                   for duration in (480, 720, 1440)}
    state_specs.update(((.10, 240), (.20, 720)))
    probabilities = {
        spec: {symbol: state_probability(bars[symbol], *spec)
               for symbol in SYMS}
        for spec in state_specs
    }
    base, strong = probabilities[(.10, 240)], probabilities[(.20, 720)]
    trade_sets = {}

    def add(name, probability, mode="posterior", maximum=7.0,
            shape="linear"):
        print(name, flush=True)
        trade_sets[name] = simulate_capped_trail(
            directions, policy, bars, 3, probability, mode,
            maximum_k=float(maximum), shape=shape)

    for maximum in (12, 15, 20, 30):
        add(f"base_dynamic_k{maximum}", base, maximum=maximum)
    for maximum in (8, 10, 12, 15, 20, 30):
        add(f"strong_dynamic_k{maximum}", strong, maximum=maximum)
    for mean in (.10, .20, .30, .40):
        for duration in (480, 720, 1440):
            add(f"state_m{int(mean*100):02d}_d{duration}_k10",
                probabilities[(mean, duration)], maximum=10)
    for label, probability in (("base", base), ("strong", strong)):
        for maximum in (7, 10, 15, 20):
            add(f"{label}_step_k{maximum}", probability,
                maximum=maximum, shape="step")
    for trail in (12, 15, 20, 30):
        add(f"static_k{trail}", base, mode="k7", maximum=trail)

    results, daily = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass9_results.csv", index=False)
    daily.to_parquet(HERE / "pass9_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass9_trades.parquet")
    update_ledger("IX", "pass9_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
