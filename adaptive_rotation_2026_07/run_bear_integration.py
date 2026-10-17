"""Recompute bear3 interaction for surviving adaptive D15/K7 allocations."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BEAR = ROOT / "bear_momentum_2026_07"
sys.path[:0] = [str(HERE), str(BEAR), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, START, SYMS, allocation,
                                   baseline_policy, feature_library)  # noqa: E402
from run_bear_profit_overlay import core_priority_short  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402
from sweep_correction_capture import gates_for  # noqa: E402
from validate_finalists import simulate_independent  # noqa: E402


def stats(pnl):
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    yearly = pnl.groupby(pnl.index.year).sum()
    return {
        "net": float(pnl.sum()), "maxDD": float(drawdown.min()),
        "worst": float(yearly.min()), "worst_year": int(yearly.idxmin()),
        "y2011_15": float(pnl[pnl.index.year <= 2015].sum()),
        "y2016_20": float(pnl[(pnl.index.year >= 2016)
                               & (pnl.index.year <= 2020)].sum()),
        "y2021_end": float(pnl[pnl.index.year >= 2021].sum()),
        "ex2026": float(pnl[pnl.index.year < 2026].sum()),
        "y2022": float(yearly.get(2022, 0.0)),
        "y2026": float(yearly.get(2026, 0.0)),
    }


def run():
    rg.START, rg.SCFG = START, dict(D15)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily, index, scores = feature_library(bars)
    policies = {
        "weekly": baseline_policy(daily, index),
        "fast5_w2_grad": allocation(scores["week35_R5_W2"], "graduated"),
        "fast5_w2_hard": allocation(scores["week35_R5_W2"], "hard"),
        "fast10_w1_grad": allocation(scores["week35_R10_W1"], "graduated"),
    }
    original = strategy.signal_frame
    cached = {id(frame): original(frame, rg.SCFG) for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    longs = {}
    try:
        for name, policy in policies.items():
            longs[name] = simulate_independent(policy, bars)
    finally:
        strategy.signal_frame = original

    # The published bear overlay deliberately uses the production short signal
    # and a D-1-lagged ret5 gate.  Recreate it once for each candidate's actual
    # NQ-long intervals; never add the old bear P&L arithmetically.
    rg.SCFG = dict(D15)
    nq = bars["NQ"]
    gate = gates_for(nq)["ret5"]
    rows = []
    for name, long_trades in longs.items():
        long_pnl = hourly_mtm(long_trades, bars, rg.FR)
        nq_longs = long_trades.query("sym == 'NQ'")
        short = core_priority_short(nq, gate, nq_longs, donch=20, k=2.0)
        short["qty"] = 3
        short_pnl = hourly_mtm(short, {"NQ": nq}, rg.FR)
        combo = pd.concat([long_pnl, short_pnl], axis=1).fillna(0).sum(axis=1)
        long_ct = int(long_trades["qty"].sum())
        short_ct = int(short["qty"].sum())

        def cost_fields(normal_net, contracts):
            gross = normal_net + rg.FR * contracts
            return {"contracts": contracts,
                    "p7": gross - 7.0 * contracts,
                    "p14": gross - 14.0 * contracts}

        long_stats, short_stats, combo_stats = (
            stats(long_pnl), stats(short_pnl), stats(combo))
        rows.append({"policy": name, "component": "long", **long_stats,
                     **cost_fields(long_stats["net"], long_ct)})
        rows.append({"policy": name, "component": "bear3", **short_stats,
                     **cost_fields(short_stats["net"], short_ct)})
        rows.append({"policy": name, "component": "combo", **combo_stats,
                     **cost_fields(combo_stats["net"], long_ct + short_ct)})
    result = pd.DataFrame(rows)
    result.to_csv(HERE / "bear_integration.csv", index=False)
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    run()
