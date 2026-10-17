"""Expected-dollar priority for scarce D15 adaptive cap-three capacity."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (  # noqa: E402
    D15, START, SYMS, allocation, feature_library,
)
from run_stage_a import pnl_stats  # noqa: E402
from run_stage_b import attach_initial_risk, risk_stats, simulate_capped  # noqa: E402
from run_stage_c import (  # noqa: E402
    causal_scores, feature_frames, teacher_trades,
)


def run():
    rg.START, rg.SCFG = START, dict(D15)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    _, _, library = feature_library(bars)
    policy = allocation(library["week35_R5_W2"], "graduated")
    features = feature_frames(bars, config=D15)
    teachers = teacher_trades(bars, features, config=D15)
    directions = {symbol: strategy.signal_frame(
        bars[symbol], D15)["signal"].astype("int8") for symbol in SYMS}

    priorities = {"proportional": None}
    for penalty in (10.0, 100.0):
        risk_scores = causal_scores(
            bars, features, teachers, penalty=penalty, config=D15)
        dollar_scores = {}
        for symbol in SYMS:
            atr = strategy.signal_frame(bars[symbol], D15)["atr"]
            dollar_scores[symbol] = (
                risk_scores[symbol] * D15["k_trail"] * atr * rg.MULT[symbol])
        priorities[f"dollar_priority_ridge{int(penalty)}"] = dollar_scores

    rows, paths = [], {}
    for name, priority in priorities.items():
        trades = simulate_capped(
            directions, policy, bars, 3, config=D15, priority=priority)
        pnl, stats = pnl_stats(trades, bars)
        stats |= risk_stats(attach_initial_risk(trades, bars, config=D15))
        rows.append({"candidate": name, **stats})
        paths[name] = pnl.groupby(pnl.index.normalize()).sum()
        print(name, stats, flush=True)
    result = pd.DataFrame(rows)
    result.to_csv(HERE / "stage_f_capacity_results.csv", index=False)
    pd.concat(paths, axis=1).fillna(0.0).to_parquet(
        HERE / "stage_f_capacity_daily_pnl.parquet")
    print("\n", result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result


if __name__ == "__main__":
    run()
