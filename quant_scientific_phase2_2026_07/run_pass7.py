"""Pass VII: strict-cap-three attribution of ridge and posterior trails."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    HERE, PRODUCTION, START, SYMS, load_bars, production_signal,
    score_trade_sets, update_ledger,
)
from run_adaptive_rotation import baseline_policy
from run_pass3 import wonham_bull_probability
from run_stage_b import allocate_capacity
from run_stage_c import causal_predictions, feature_frames, teacher_trades
import rotation_grid as rg
import strategy


def simulate_capped_trail(directions, policy, bars, cap: int,
                          probabilities, mode: str, maximum_k: float = 7.0,
                          shape: str = "linear",
                          per_trade_risk_cap: float | None = None) -> pd.DataFrame:
    frames = {symbol: strategy.signal_frame(bars[symbol], PRODUCTION)
              for symbol in SYMS}
    states = {
        symbol: {"pos": 0, "pending": 0, "pending_k": 2.0,
                 "trade_k": 2.0, "entry": np.nan, "high": np.nan,
                 "stop": np.nan, "entry_time": None}
        for symbol in SYMS
    }
    times = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        times = times.union(bars[symbol].index)
    locations = {symbol: {stamp: i for i, stamp in enumerate(bars[symbol].index)}
                 for symbol in SYMS}
    rows = []
    for stamp in times:
        for symbol in SYMS:
            state = states[symbol]
            if (state["pending"] and stamp in locations[symbol]
                    and int(bars[symbol]["roll"].iloc[locations[symbol][stamp]])):
                state["pending"] = 0

        pending = [(symbol, states[symbol]["pending"]) for symbol in SYMS
                   if states[symbol]["pending"] and stamp in locations[symbol]]
        existing = sum(states[symbol]["pos"] for symbol in SYMS)
        grants = allocate_capacity(
            np.array([quantity for _, quantity in pending], dtype=int),
            max(cap - existing, 0)) if pending else np.array([], dtype=int)
        fills = {symbol: 0 for symbol in SYMS}
        fills.update({symbol: int(quantity)
                      for (symbol, _), quantity in zip(pending, grants)})
        for symbol, _ in pending:
            state = states[symbol]
            i = locations[symbol][stamp]
            state["pending"] = 0
            quantity = fills[symbol]
            if quantity:
                state["pos"] = quantity
                state["trade_k"] = state["pending_k"]
                state["entry"] = float(bars[symbol]["open"].iloc[i])
                state["high"] = state["entry"]
                state["stop"] = (
                    state["entry"] - state["trade_k"]
                    * float(frames[symbol]["atr"].iloc[i - 1]))
                state["entry_time"] = stamp
                if not np.isfinite(state["stop"]):
                    state["pos"] = 0

        for symbol in SYMS:
            if stamp not in locations[symbol]:
                continue
            i = locations[symbol][stamp]
            bar = bars[symbol].iloc[i]
            state = states[symbol]
            if state["pos"] and int(bar["roll"]):
                exit_px = (float(bars[symbol]["close"].iloc[i - 1]) if i
                           else float(bar["open"]))
                rows.append((state["entry_time"], stamp, symbol,
                             state["entry"], exit_px,
                             exit_px - state["entry"], "roll", 1,
                             state["pos"]))
                state["pos"] = 0
            if state["pos"]:
                if float(bar["low"]) <= state["stop"]:
                    exit_px = min(state["stop"], float(bar["open"]))
                    rows.append((state["entry_time"], stamp, symbol,
                                 state["entry"], exit_px,
                                 exit_px - state["entry"], "stop", 1,
                                 state["pos"]))
                    state["pos"] = 0
                else:
                    state["high"] = max(state["high"], float(bar["high"]))
                    state["stop"] = max(
                        state["stop"], state["high"] - state["trade_k"]
                        * float(frames[symbol]["atr"].iloc[i]))
            if not state["pos"] and not state["pending"]:
                if (stamp in directions[symbol].index
                        and int(directions[symbol].loc[stamp]) == 1):
                    requested = int(policy[symbol].asof(stamp))
                    if requested <= 0:
                        continue
                    if mode == "k2":
                        entry_k = 2.0
                    elif mode == "k7":
                        entry_k = maximum_k
                    elif mode == "posterior":
                        p = float(probabilities[symbol].asof(stamp))
                        if not np.isfinite(p):
                            continue
                        strength = np.clip(2.0 * p - 1.0, 0.0, 1.0)
                        if shape == "square":
                            strength = strength ** 2
                        elif shape == "sqrt":
                            strength = np.sqrt(strength)
                        elif shape == "step":
                            strength = float(strength > 0.0)
                        elif shape != "linear":
                            raise ValueError(shape)
                        entry_k = 2.0 + (maximum_k - 2.0) * strength
                    else:
                        raise ValueError(mode)
                    if per_trade_risk_cap is not None:
                        atr = float(frames[symbol]["atr"].loc[stamp])
                        per_contract_risk = entry_k * atr * rg.MULT[symbol]
                        affordable = (int(per_trade_risk_cap // per_contract_risk)
                                      if per_contract_risk > 0 else 0)
                        requested = min(requested, affordable)
                        if requested <= 0:
                            continue
                    state["pending"] = requested
                    state["pending_k"] = entry_k
    trades = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "dir", "qty",
    ])
    return trades[trades["entry_time"] >= START].sort_values(
        "exit_time").reset_index(drop=True)


def run() -> pd.DataFrame:
    bars = load_bars()
    daily_prices = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
                    for symbol in SYMS}
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    policy = baseline_policy(daily_prices, common)
    features = feature_frames(bars)
    teachers = teacher_trades(bars, features)
    ridge = causal_predictions(
        bars, features, teachers, penalties=(10.0,))["ridge10_mean"]
    production = {symbol: production_signal(bars[symbol]) for symbol in SYMS}
    probabilities = {symbol: wonham_bull_probability(bars[symbol])
                     for symbol in SYMS}
    trade_sets = {}
    for prefix, directions in (("production", production), ("ridge", ridge)):
        for mode, suffix in (("k2", "k2"), ("k7", "k7"),
                             ("posterior", "posterior_k7")):
            name = f"{prefix}_{suffix}_cap3"
            print(name, flush=True)
            trade_sets[name] = simulate_capped_trail(
                directions, policy, bars, 3, probabilities, mode)
    results, daily = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass7_results.csv", index=False)
    daily.to_parquet(HERE / "pass7_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass7_trades.parquet")
    update_ledger("VII", "pass7_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
