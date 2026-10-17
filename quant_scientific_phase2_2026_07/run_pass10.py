"""Pass X: deterministic account-risk translation of the two frontier paths."""
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
    probabilities = {
        symbol: state_probability(bars[symbol], .10, 240) for symbol in SYMS}
    daily = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
             for symbol in SYMS}
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    policy = baseline_policy(daily, common)
    trade_sets = {}
    for shape, maximum, label in (("step", 15.0, "step_k15"),
                                  ("linear", 20.0, "dynamic_k20")):
        for risk_cap in (None, 3000.0, 6000.0, 9000.0, 12000.0):
            suffix = "uncapped" if risk_cap is None else f"cap{int(risk_cap)}"
            name = f"{label}_{suffix}"
            print(name, flush=True)
            trade_sets[name] = simulate_capped_trail(
                directions, policy, bars, 3, probabilities, "posterior",
                maximum_k=maximum, shape=shape,
                per_trade_risk_cap=risk_cap)
    results, daily_pnl = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass10_results.csv", index=False)
    daily_pnl.to_parquet(HERE / "pass10_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass10_trades.parquet")
    update_ledger("X", "pass10_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
