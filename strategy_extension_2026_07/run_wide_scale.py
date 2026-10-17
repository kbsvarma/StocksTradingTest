"""Combine the frozen K7 signal plateau with fixed sizing structures."""
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
from run_episode_search import simulate  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402
from run_profit_max_grid import max_policy, plan_shapes  # noqa: E402


PROD = dict(rg.SCFG)


def structures(base):
    out = {"N3": (base, {})}
    for n in (12, 30):
        p = max_policy(base, n)
        out[f"N{n}_allin_loss"] = (p, {"loss_session_rearm": True})
        out[f"N{n}_convex_loss"] = (p, {
            "stages": plan_shapes(n)["convex_ladder"],
            "loss_session_rearm": True})
        out[f"N{n}_2+rest_normal"] = (p, {
            "stages": ((2, 0.0), (n-2, 0.5))})
    return out


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    base = weekly_policy(daily, 35, t0, t1)["baseline"]
    original_cfg, original_signal = rg.SCFG, strategy.signal_frame
    try:
        print(f"{'config':<8}{'structure':<20}{'net':>12}{'maxDD':>12}"
              f"{'worst':>11}{'ex26':>12}{'$7':>12}{'$14':>12}"
              f"{'11-15':>12}{'16-20':>12}{'21-end':>12}{'ct':>10}"
              f"{'worstEp':>12}")
        for dn in (8, 10, 15):
            cfg = dict(PROD, donchian_n=dn, k_trail=7.0,
                       vol_gate_q=.925, session_block_et=(2, 7))
            rg.SCFG = cfg
            frames = {id(B): original_signal(B, cfg) for B in bars.values()}
            strategy.signal_frame = lambda B, ignored: frames[id(B)]
            for name, (policy, kw) in structures(base).items():
                T = pd.concat([simulate(B, sym, policy, **kw)
                               for sym, B in bars.items()]) \
                    .sort_values("exit_time").reset_index(drop=True)
                p = hourly_mtm(T, bars, rg.FR)
                eq = p.cumsum(); dd = eq-eq.cummax().clip(lower=0)
                y = p.groupby(p.index.year).sum(); ct = int(T.qty.sum())
                gross = p.sum()+rg.FR*ct
                ep = T.assign(net=(T.exit_px-T.entry_px)*T.qty
                              * T.sym.map(rg.MULT)-rg.FR*T.qty) \
                    .groupby(["sym", "episode"])["net"].sum().min()
                label = f"D{dn}K7"
                print(f"{label:<8}{name:<20}{p.sum():>12,.0f}{dd.min():>12,.0f}"
                      f"{y.min():>11,.0f}{p[p.index.year<2026].sum():>12,.0f}"
                      f"{gross-7*ct:>12,.0f}{gross-14*ct:>12,.0f}"
                      f"{p[p.index.year<=2015].sum():>12,.0f}"
                      f"{p[(p.index.year>=2016)&(p.index.year<=2020)].sum():>12,.0f}"
                      f"{p[p.index.year>=2021].sum():>12,.0f}{ct:>10,.0f}"
                      f"{ep:>12,.0f}")
    finally:
        rg.SCFG, strategy.signal_frame = original_cfg, original_signal


if __name__ == "__main__":
    run()
