"""Second-stage, train-selected NQ-only D/K sensitivity grid."""
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
from run_adaptive_rotation import (D15, START, SYMS, allocation,
                                   feature_library)  # noqa: E402
from run_nq_only import pnl_and_stats, trades_for_nq  # noqa: E402


DONCHIANS = (10, 15, 20, 30, 40, 55)
TRAILS = (2.0, 3.0, 5.0, 7.0, 10.0)


def run():
    rg.START = START
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    _, index, scores = feature_library(bars)
    relative_qty = allocation(
        scores["week35_R5_W2"], "graduated")["NQ"].astype(int)
    fixed_qty = pd.Series(3, index=index, dtype=int)
    policies = {"fixed3": fixed_qty, "relative_qty": relative_qty}

    rows = []
    original = strategy.signal_frame
    try:
        for donchian in DONCHIANS:
            for trail in TRAILS:
                config = dict(D15, donchian_n=donchian, k_trail=trail)
                rg.SCFG = config
                nq_signal = original(bars["NQ"], config)
                strategy.signal_frame = lambda frame, ignored: nq_signal
                for policy_name, quantity in policies.items():
                    trades = trades_for_nq(quantity, bars)
                    pnl, stats = pnl_and_stats(trades, bars)
                    rows.append({
                        "policy": policy_name,
                        "donchian": donchian,
                        "trail": trail,
                        "train_2011_20": float(
                            pnl[pnl.index.year <= 2020].sum()),
                        "test_2021_end": float(
                            pnl[pnl.index.year >= 2021].sum()),
                        **stats,
                    })
                print(f"D{donchian}/K{trail:g}", flush=True)
    finally:
        strategy.signal_frame = original

    frame = pd.DataFrame(rows)
    frame.to_csv(HERE / "nq_parameter_grid.csv", index=False)
    for policy_name in policies:
        cells = frame.query("policy == @policy_name")
        selected = cells.sort_values("train_2011_20", ascending=False).iloc[0]
        print(f"\n{policy_name} train-selected:\n{selected.to_string()}")
        print("\nfull-net leaders:")
        print(cells.sort_values("net", ascending=False).head(10).to_string(
            index=False, float_format=lambda value: f"{value:,.1f}"))
    return frame


if __name__ == "__main__":
    run()
