"""Expand profit leaders until leverage—not signal choice—is visibly binding."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_episode_search import simulate, summarize  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402
from run_profit_max_grid import max_policy, plan_shapes, transient_atr_risk  # noqa: E402


SIZES = (12, 16, 20, 24, 30)


def families(n):
    return {
        "allin_normal": ({}, None),
        "allin_losslock": ({"loss_session_rearm": True}, None),
        "2+rest05_normal": ({"stages": ((2, 0.0), (n - 2, 0.5))},
                            ((2, 0.0), (n - 2, 0.5))),
        "convex_losslock": ({"stages": plan_shapes(n)["convex_ladder"],
                             "loss_session_rearm": True},
                            plan_shapes(n)["convex_ladder"]),
    }


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    frames = {id(B): strategy.signal_frame(B, rg.SCFG) for B in bars.values()}
    original = strategy.signal_frame
    strategy.signal_frame = lambda B, cfg: frames[id(B)]
    try:
        daily = {s: B["close"].resample("1D").last().dropna()
                 for s, B in bars.items()}
        t0, t1 = min(B.index.min() for B in bars.values()), \
                 max(B.index.max() for B in bars.values())
        for lb in (21, 35, 55):
            base = weekly_policy(daily, lb, t0, t1)["baseline"]
            print(f"\nL{lb} PROFIT BOUNDARY")
            print(f"{'cell':<31}{'net':>11}{'maxDD':>11}{'worst':>10}"
                  f"{'ex26':>11}{'$7':>11}{'$14':>11}{'ct':>10}"
                  f"{'worstEp':>11}{'ATRrisk':>10}")
            for n in SIZES:
                policy = max_policy(base, n)
                for family, (kw, stages) in families(n).items():
                    T = pd.concat([simulate(B, sym, policy, **kw)
                                   for sym, B in bars.items()]) \
                        .sort_values("exit_time").reset_index(drop=True)
                    s = summarize(T, bars, rg.FR)
                    p = hourly_mtm(T, bars, rg.FR)
                    gross = s["total"] + rg.FR * s["contracts"]
                    ep = T.assign(net=(T.exit_px - T.entry_px) * T.qty
                                  * T.sym.map(rg.MULT) - rg.FR * T.qty) \
                        .groupby(["sym", "episode"])["net"].sum().min()
                    name = f"N{n}_{family}"
                    print(f"{name:<31}{s['total']:>11,.0f}{s['dd']:>11,.0f}"
                          f"{s['worst']:>10,.0f}{p[p.index.year < 2026].sum():>11,.0f}"
                          f"{gross-7*s['contracts']:>11,.0f}"
                          f"{gross-14*s['contracts']:>11,.0f}"
                          f"{s['contracts']:>10,.0f}{ep:>11,.0f}"
                          f"{transient_atr_risk(stages, n):>10.1f}")
    finally:
        strategy.signal_frame = original


if __name__ == "__main__":
    run()
