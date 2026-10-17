"""Post-selection D40/K10 strict-cap-three basket symmetry diagnostic."""
from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, START, SYMS, allocation,
                                   feature_library)  # noqa: E402
from run_nq_only import pnl_and_stats  # noqa: E402
from validate_finalists import simulate_with_global_cap  # noqa: E402


def run():
    config = dict(D15, donchian_n=40, k_trail=10.0)
    rg.START, rg.SCFG = START, config
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    _, _, scores = feature_library(bars)
    policy = allocation(scores["week35_R5_W2"], "graduated")
    trades = simulate_with_global_cap(policy, bars, 3)
    _, stats = pnl_and_stats(trades, bars)
    print(stats)
    return stats


if __name__ == "__main__":
    run()
