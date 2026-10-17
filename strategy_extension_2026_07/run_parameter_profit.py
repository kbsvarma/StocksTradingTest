"""Profit-first signal and rotation parameter screen."""
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
from run_profit_max_grid import max_policy, plan_shapes  # noqa: E402


PROD = dict(rg.SCFG)


def signal_cells():
    for n in (10, 15, 20, 30, 40, 55):
        for k in (1.5, 2.0, 2.5, 3.0, 4.0):
            yield f"D{n}_K{k:g}", dict(PROD, donchian_n=n, k_trail=k)
    for q in (0.90, 0.925, 0.95, 0.975, 0.99):
        yield f"Q{q:g}", dict(PROD, vol_gate_q=q)
    for label, hours in (
        ("session_none", (0, 0)),
        ("session_00_05", (0, 5)),
        ("session_02_06", (2, 6)),
        ("session_03_06", (3, 6)),
        ("session_02_07", (2, 7)),
    ):
        yield label, dict(PROD, session_block_et=hours)


def evaluate(name, Bset, bars, structures):
    rows = []
    for structure, (policy, kw) in structures.items():
        T = pd.concat([simulate(B, sym, policy, **kw)
                       for sym, B in bars.items()]) \
            .sort_values("exit_time").reset_index(drop=True)
        s = summarize(T, bars, rg.FR)
        p = hourly_mtm(T, bars, rg.FR)
        gross = s["total"] + rg.FR * s["contracts"]
        rows.append({
            "cell": name, "structure": structure, "net": s["total"],
            "dd": s["dd"], "worst": s["worst"],
            "ex26": p[p.index.year < 2026].sum(),
            "p7": gross - 7 * s["contracts"],
            "p14": gross - 14 * s["contracts"],
            "ct": s["contracts"],
            "net_per_ct": s["total"] / s["contracts"],
        })
    return rows


def print_top(R, title, n=25):
    print(f"\n{title}")
    print(f"{'cell':<19}{'structure':<13}{'net':>11}{'maxDD':>11}"
          f"{'worst':>10}{'ex26':>11}{'$7':>11}{'$14':>11}"
          f"{'ct':>9}{'$/ct':>9}")
    for r in R.head(n).itertuples():
        print(f"{r.cell:<19}{r.structure:<13}{r.net:>11,.0f}"
              f"{r.dd:>11,.0f}{r.worst:>10,.0f}{r.ex26:>11,.0f}"
              f"{r.p7:>11,.0f}{r.p14:>11,.0f}{r.ct:>9,.0f}"
              f"{r.net_per_ct:>9.2f}")


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    original_cfg = rg.SCFG
    original_signal = strategy.signal_frame
    rows = []
    try:
        policy35 = weekly_policy(daily, 35, t0, t1)["baseline"]
        structures = {
            "N3": (policy35, {}),
            "N12_convex": (max_policy(policy35, 12), {
                "stages": plan_shapes(12)["convex_ladder"],
                "loss_session_rearm": True,
            }),
        }
        for name, cfg in signal_cells():
            rg.SCFG = cfg
            frames = {id(B): original_signal(B, cfg) for B in bars.values()}
            strategy.signal_frame = lambda B, ignored: frames[id(B)]
            rows.extend(evaluate(name, frames, bars, structures))

        # Rotation cells use the unmodified production signal.
        rg.SCFG = dict(PROD)
        frames = {id(B): original_signal(B, rg.SCFG) for B in bars.values()}
        strategy.signal_frame = lambda B, ignored: frames[id(B)]
        for lb in (10, 14, 21, 28, 35, 42, 55, 63, 84, 126):
            p = weekly_policy(daily, lb, t0, t1)["baseline"]
            structures = {
                "N3": (p, {}),
                "N12_convex": (max_policy(p, 12), {
                    "stages": plan_shapes(12)["convex_ladder"],
                    "loss_session_rearm": True,
                }),
            }
            rows.extend(evaluate(f"rotation_L{lb}", frames, bars, structures))
    finally:
        rg.SCFG = original_cfg
        strategy.signal_frame = original_signal

    R = pd.DataFrame(rows)
    print_top(R.sort_values("net", ascending=False),
              "TOP PARAMETER CELLS BY NET PROFIT", 30)
    print_top(R.sort_values("ex26", ascending=False),
              "TOP PARAMETER CELLS EXCLUDING INCOMPLETE 2026", 20)
    print_top(R.sort_values("net_per_ct", ascending=False),
              "TOP PARAMETER CELLS BY NET PROFIT PER CONTRACT", 20)


if __name__ == "__main__":
    run()
