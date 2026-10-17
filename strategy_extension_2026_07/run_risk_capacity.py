"""Iteration-8 causal, initial-risk-capped pyramid."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
from run_earned_fourth import with_aggressive_max4  # noqa: E402
from run_episode_search import simulate, summarize  # noqa: E402
from run_policy_search import START, weekly_policy  # noqa: E402


CANDIDATE = {
    "session_rearm": True,
    "stages": ((1, 0.0), (2, 0.5), (1, 1.0)),
    "stage_execution": "high_next_open",
    "risk_budget_atr_contracts": 6.0,
}


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    for lookback in (21, 35, 55):
        base = weekly_policy(daily, lookback, t0, t1)["baseline"]
        max4 = with_aggressive_max4(base)
        print(f"\n=== L{lookback} causal risk-capacity pyramid ===")
        print(f"{'cell':<24}{'total':>10}{'maxDD':>10}{'$7tot':>10}"
              f"{'$7DD':>10}{'worst':>10}{'11-15':>10}{'16-20':>10}"
              f"{'21-end':>10}{'ex22':>10}{'2022':>9}{'ct':>8}")
        for name, policy, kw in (
            ("current_baseline_3", base, {}),
            ("risk_capacity_4", max4, CANDIDATE),
        ):
            T = pd.concat([simulate(B, sym, policy, **kw)
                           for sym, B in bars.items()]) \
                .sort_values("exit_time").reset_index(drop=True)
            s, c = summarize(T, bars, rg.FR), summarize(T, bars, 7.0)
            print(f"{name:<24}{s['total']:>10,.0f}{s['dd']:>10,.0f}"
                  f"{c['total']:>10,.0f}{c['dd']:>10,.0f}{s['worst']:>10,.0f}"
                  f"{s['a']:>10,.0f}{s['b']:>10,.0f}{s['c']:>10,.0f}"
                  f"{s['ex22']:>10,.0f}{s['y22']:>9,.0f}"
                  f"{s['contracts']:>8}")
        print("data-end liquidation sensitivity")
        for name, policy, kw in (
            ("current_baseline_3", base, {}),
            ("risk_capacity_4", max4, CANDIDATE),
        ):
            opts = dict(kw, liquidate_end=True)
            T = pd.concat([simulate(B, sym, policy, **opts)
                           for sym, B in bars.items()])
            s = summarize(T, bars, rg.FR)
            print(f"  {name:<22} {s['total']:>10,.0f} / {s['dd']:>10,.0f}")


if __name__ == "__main__":
    run()
