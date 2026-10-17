"""Post-screen local neighborhood for the slow35 + live-fast equation."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, PRODUCTION, START, SYMS, allocation,
                                   cross_z, current_horizon_returns,
                                   evaluate_policy, frozen_monday_return)  # noqa: E402


def run(config, label):
    rg.START, rg.SCFG = START, dict(config)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily = {s: bars[s]["close"].resample("1D").last().dropna() for s in SYMS}
    index = bars[SYMS[0]].index
    for sym in SYMS[1:]:
        index = index.intersection(bars[sym].index)
    slow = cross_z(frozen_monday_return(daily, index, 35))
    fast = current_horizon_returns(bars, daily, index, (4, 5, 6, 7))
    policies = {}
    for horizon, returns in fast.items():
        zfast = cross_z(returns)
        for weight in (1.5, 2.0, 2.5):
            score = slow + weight * zfast
            for mode in ("hard", "conf", "graduated"):
                name = f"R{horizon}_W{weight:g}_{mode}"
                policies[name] = allocation(score, mode)
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
    result.to_parquet(HERE / f"neighbors_{label}.parquet", index=False)
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "production"
    run(PRODUCTION if label == "production" else D15, label)
