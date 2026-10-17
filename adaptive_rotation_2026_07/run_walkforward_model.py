"""Strictly causal walk-forward ridge ranking model."""
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
                                   baseline_policy, evaluate_policy)  # noqa: E402


def cross_z(frame: pd.DataFrame) -> pd.DataFrame:
    mean = frame.mean(axis=1)
    sd = frame.std(axis=1, ddof=0).replace(0, np.nan)
    return frame.sub(mean, axis=0).div(sd, axis=0).fillna(0.0)


def daily_design(daily: dict[str, pd.Series]):
    common = daily[SYMS[0]].index
    for sym in SYMS[1:]:
        common = common.intersection(daily[sym].index)
    price = pd.concat({s: daily[s].reindex(common) for s in SYMS}, axis=1)
    ret = {h: price.pct_change(h, fill_method=None) for h in (3, 5, 10, 21, 35)}
    zret = {h: cross_z(x) for h, x in ret.items()}
    one = price.pct_change(fill_method=None)
    vol21 = one.rolling(21).std()
    risk_adjusted = cross_z(ret[21].div(vol21 * np.sqrt(21)))
    acceleration = cross_z(ret[5] - ret[21])
    names = ["r3", "r5", "r10", "r21", "r35", "accel", "riskadj"]
    pieces = [zret[3], zret[5], zret[10], zret[21], zret[35],
              acceleration, risk_adjusted]
    # Shape: day x symbol x feature.
    design = np.stack([x.to_numpy(dtype=float) for x in pieces], axis=2)
    return price, design, names


def walkforward_scores(price: pd.DataFrame, design: np.ndarray,
                       horizon: int, train_window: int,
                       ridge: float) -> pd.DataFrame:
    future = price.shift(-horizon).div(price) - 1.0
    target = cross_z(future).to_numpy(dtype=float)
    scores = np.full((len(price), len(SYMS)), np.nan)
    minimum = min(252, train_window // 3)
    for decision in range(1, len(price)):
        feature_day = decision - 1
        last_labeled = feature_day - horizon
        first = max(0, last_labeled - train_window + 1)
        if last_labeled - first + 1 < minimum:
            continue
        x = design[first:last_labeled + 1].reshape(-1, design.shape[2])
        y = target[first:last_labeled + 1].reshape(-1)
        valid = np.isfinite(x).all(axis=1) & np.isfinite(y)
        x, y = x[valid], y[valid]
        current = design[feature_day]
        if len(x) < minimum * 2 or not np.isfinite(current).all():
            continue
        mean, sd = x.mean(axis=0), x.std(axis=0)
        sd[sd == 0] = 1.0
        xs = (x - mean) / sd
        beta = np.linalg.solve(xs.T @ xs + ridge * np.eye(xs.shape[1]),
                               xs.T @ y)
        scores[decision] = ((current - mean) / sd) @ beta
    return pd.DataFrame(scores, index=price.index, columns=SYMS)


def run(config=PRODUCTION, label="production"):
    rg.START, rg.SCFG = START, dict(config)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily = {s: bars[s]["close"].resample("1D").last().dropna() for s in SYMS}
    index = bars[SYMS[0]].index
    for sym in SYMS[1:]:
        index = index.intersection(bars[sym].index)
    price, design, _ = daily_design(daily)
    base = baseline_policy(daily, index)
    policies = {"weekly_L35": base}
    for horizon in (5, 10):
        for window in (756, 1260):
            for ridge in (1.0, 10.0):
                score = walkforward_scores(price, design, horizon, window,
                                           ridge)
                hourly_score = score.reindex(index.normalize()).set_axis(index)
                for mode in ("hard", "graduated"):
                    candidate = allocation(hourly_score, mode)
                    # Before the model has enough matured labels, use the
                    # benchmark rather than arbitrary tie-breaking.
                    unavailable = ~np.isfinite(hourly_score).all(axis=1)
                    candidate.loc[unavailable] = base.loc[unavailable]
                    name = f"ridge_H{horizon}_W{window}_L{ridge:g}_{mode}"
                    policies[name] = candidate

    original = strategy.signal_frame
    cached = {id(frame): original(frame, rg.SCFG) for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    try:
        result = pd.DataFrame([
            evaluate_policy(name, policy, bars)
            for name, policy in policies.items()
        ]).sort_values("net", ascending=False)
    finally:
        strategy.signal_frame = original
    result.to_parquet(HERE / f"walkforward_{label}.parquet", index=False)
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "production"
    run(PRODUCTION if label == "production" else D15, label)
