"""Causal online ensemble of fixed adaptive-rotation experts."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, PRODUCTION, START, SYMS, allocation,
                                   baseline_policy, evaluate_policy,
                                   feature_library)  # noqa: E402


def largest_remainder(values: np.ndarray) -> np.ndarray:
    values = np.maximum(values, 0.0)
    values = 3.0 * values / values.sum() if values.sum() else np.ones(3)
    base = np.floor(values).astype(int)
    for index in np.argsort(-(values - base))[:3 - base.sum()]:
        base[index] += 1
    return base


def online_policy(experts: dict[str, pd.DataFrame],
                  closes: pd.DataFrame, horizon_hours: int,
                  eta: float, half_life_days: int) -> pd.DataFrame:
    """Exponentially weight experts from labels that have actually matured.

    At timestamp t the reward uses each expert's allocation at t-h and the
    now-known return from t-h to t.  The resulting weights combine allocations
    computed from the current completed close for a possible next-bar entry.
    """
    names = tuple(experts)
    cube = np.stack([experts[name].reindex(closes.index).to_numpy(dtype=float)
                     for name in names])
    prices = closes.to_numpy(dtype=float)
    log_weight = np.zeros(len(names), dtype=float)
    decay = np.exp(-np.log(2.0) / (24.0 * half_life_days))
    output = np.ones((len(closes), len(SYMS)), dtype=int)
    for t in range(len(closes)):
        if t >= horizon_hours and np.isfinite(prices[[t - horizon_hours, t]]).all():
            realized = prices[t] / prices[t - horizon_hours] - 1.0
            excess = realized - realized.mean()
            reward = (cube[:, t - horizon_hours, :] * excess).sum(axis=1) / 3.0
            log_weight = decay * log_weight + eta * reward
            log_weight -= log_weight.max()
        weight = np.exp(log_weight)
        weight /= weight.sum()
        output[t] = largest_remainder(
            (cube[:, t, :] * weight[:, None]).sum(axis=0))
    assert (output.sum(axis=1) == 3).all()
    return pd.DataFrame(output, index=closes.index, columns=SYMS)


def run(config=PRODUCTION, label="production") -> pd.DataFrame:
    rg.START, rg.SCFG = START, dict(config)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily, index, scores = feature_library(bars)
    closes = pd.concat({s: bars[s]["close"].reindex(index) for s in SYMS},
                       axis=1)
    experts = {
        "weekly": baseline_policy(daily, index),
        "fast3": allocation(scores["week35_R3_W1"], "graduated"),
        "fast5": allocation(scores["week35_R5_W1"], "graduated"),
        "fast10": allocation(scores["week35_R10_W1"], "graduated"),
        "live35": allocation(scores["live_R35"], "graduated"),
        "vol21": allocation(scores["voladj_R21"], "graduated"),
        "quality21": allocation(scores["quality_21"], "graduated"),
        "kalman": allocation(scores["kalman_Q0.01"], "graduated"),
    }
    policies = {"weekly_L35": experts["weekly"]}
    for horizon in (24, 120):
        for eta in (10.0, 30.0, 100.0):
            for half_life in (63, 252):
                name = f"hedge_H{horizon}_E{eta:g}_HL{half_life}"
                policies[name] = online_policy(experts, closes, horizon, eta,
                                               half_life)

    original = strategy.signal_frame
    cached = {id(frame): original(frame, rg.SCFG) for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    try:
        rows = [evaluate_policy(name, policy, bars)
                for name, policy in policies.items()]
    finally:
        strategy.signal_frame = original
    result = pd.DataFrame(rows).sort_values("net", ascending=False)
    result.to_parquet(HERE / f"online_{label}.parquet", index=False)
    return result


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "production"
    result = run(PRODUCTION if label == "production" else D15, label)
    print(result.to_string(index=False))
