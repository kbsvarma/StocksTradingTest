"""Factorial interactions among the viewed profit leaders."""
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
    return {
        "N3": (base, {}),
        "N12_convex": (max_policy(base, 12), {
            "stages": plan_shapes(12)["convex_ladder"],
            "loss_session_rearm": True}),
        "N30_convex": (max_policy(base, 30), {
            "stages": plan_shapes(30)["convex_ladder"],
            "loss_session_rearm": True}),
        "N12_allin": (max_policy(base, 12), {
            "loss_session_rearm": True}),
        "N30_allin": (max_policy(base, 30), {
            "loss_session_rearm": True}),
    }


def metric(T, bars):
    p = hourly_mtm(T, bars, rg.FR)
    eq = p.cumsum()
    dd = eq - eq.cummax().clip(lower=0.0)
    yearly = p.groupby(p.index.year).sum()
    contracts = int(T.qty.sum())
    net = float(p.sum())
    gross = net + rg.FR * contracts
    return {
        "net": net, "dd": float(dd.min()), "worst": float(yearly.min()),
        "ex26": float(p[p.index.year < 2026].sum()),
        "p7": gross - 7 * contracts, "p14": gross - 14 * contracts,
        "a": float(p[p.index.year <= 2015].sum()),
        "b": float(p[(p.index.year >= 2016) & (p.index.year <= 2020)].sum()),
        "c": float(p[p.index.year >= 2021].sum()), "ct": contracts,
    }


def print_top(R, title, count=30):
    print(f"\n{title}")
    print(f"{'config':<23}{'structure':<12}{'net':>11}{'maxDD':>11}"
          f"{'worst':>10}{'ex26':>11}{'$7':>11}{'$14':>11}"
          f"{'11-15':>11}{'16-20':>11}{'21-end':>11}{'ct':>9}")
    for r in R.head(count).itertuples():
        print(f"{r.config:<23}{r.structure:<12}{r.net:>11,.0f}"
              f"{r.dd:>11,.0f}{r.worst:>10,.0f}{r.ex26:>11,.0f}"
              f"{r.p7:>11,.0f}{r.p14:>11,.0f}{r.a:>11,.0f}"
              f"{r.b:>11,.0f}{r.c:>11,.0f}{r.ct:>9,.0f}")


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    policies = {lb: weekly_policy(daily, lb, t0, t1)["baseline"]
                for lb in (35, 42, 84)}
    original_cfg, original_signal = rg.SCFG, strategy.signal_frame
    rows = []
    try:
        for dn in (10, 15, 20, 40):
            for q in (0.925, 0.95):
                for session in ((2, 6), (2, 7)):
                    cfg = dict(PROD, donchian_n=dn, k_trail=4.0,
                               vol_gate_q=q, session_block_et=session)
                    rg.SCFG = cfg
                    frames = {id(B): original_signal(B, cfg)
                              for B in bars.values()}
                    strategy.signal_frame = lambda B, ignored: frames[id(B)]
                    for lb, base in policies.items():
                        label = f"D{dn}Q{q:g}S{session[0]}-{session[1]}L{lb}"
                        for structure, (policy, kw) in structures(base).items():
                            T = pd.concat([simulate(B, sym, policy, **kw)
                                           for sym, B in bars.items()]) \
                                .sort_values("exit_time").reset_index(drop=True)
                            rows.append({"config": label,
                                         "structure": structure,
                                         **metric(T, bars)})
    finally:
        rg.SCFG = original_cfg
        strategy.signal_frame = original_signal
    R = pd.DataFrame(rows)
    print_top(R.sort_values("net", ascending=False),
              "TOP INTERACTIONS BY NET PROFIT", 35)
    print_top(R.sort_values("ex26", ascending=False),
              "TOP INTERACTIONS EXCLUDING INCOMPLETE 2026", 25)
    print_top(R[R.structure == "N3"].sort_values("net", ascending=False),
              "TOP UNLEVERED N3 INTERACTIONS", 20)


if __name__ == "__main__":
    run()
