"""Iteration-5 confirmation-earned fourth-contract experiment."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
from run_episode_search import simulate, summarize  # noqa: E402
from run_policy_search import START, weekly_policy  # noqa: E402


CELLS = {
    "current_baseline_3": {},
    "candidate_max3": {"session_rearm": True,
                        "stages": ((1, 0.0), (2, 0.5))},
    "earned4_1+3_0.5_PRIMARY": {"session_rearm": True,
                                 "stages": ((1, 0.0), (3, 0.5))},
    "earned4_2+2_0.5": {"session_rearm": True,
                         "stages": ((2, 0.0), (2, 0.5))},
    "earned4_1+3_1.0": {"session_rearm": True,
                         "stages": ((1, 0.0), (3, 1.0))},
}


def with_aggressive_max4(policy: pd.DataFrame) -> pd.DataFrame:
    out = policy.copy()
    lcols = [c for c in out if c.startswith("L_")]
    for idx, row in out.iterrows():
        values = row[lcols]
        if values.max() == 3 and (values == 3).sum() == 1:
            winner = values.idxmax()
            out.loc[idx, winner] = 4
    return out


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    for lookback in (21, 35, 55):
        base_policy = weekly_policy(daily, lookback, t0, t1)["baseline"]
        max4_policy = with_aggressive_max4(base_policy)
        print(f"\n=== L{lookback} confirmation-earned fourth ===")
        print(f"{'cell':<29}{'total':>10}{'maxDD':>10}{'$7tot':>10}"
              f"{'$7DD':>10}{'worst':>10}{'11-15':>10}{'16-20':>10}"
              f"{'21-end':>10}{'ex22':>10}{'2022':>9}")
        for name, kw in CELLS.items():
            policy = (base_policy if name in
                      {"current_baseline_3", "candidate_max3"}
                      else max4_policy)
            T = pd.concat([simulate(B, sym, policy, **kw)
                           for sym, B in bars.items()]) \
                .sort_values("exit_time").reset_index(drop=True)
            s, c = summarize(T, bars, rg.FR), summarize(T, bars, 7.0)
            print(f"{name:<29}{s['total']:>10,.0f}{s['dd']:>10,.0f}"
                  f"{c['total']:>10,.0f}{c['dd']:>10,.0f}{s['worst']:>10,.0f}"
                  f"{s['a']:>10,.0f}{s['b']:>10,.0f}{s['c']:>10,.0f}"
                  f"{s['ex22']:>10,.0f}{s['y22']:>9,.0f}")


if __name__ == "__main__":
    run()
