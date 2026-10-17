"""Cross-lookback, cost, slippage, and instrument validation of profit leaders."""
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


TICKS = {"NQ": 0.25, "ES": 0.25, "YM": 1.0}


def convex(n):
    return plan_shapes(n)["convex_ladder"]


LEADERS = {
    "baseline_N3": (3, {}),
    "N4_1+2+1_normal": (4, {"stages": ((1, 0.0), (2, 0.5), (1, 1.0))}),
    "N4_1+2+1_losslock": (4, {"stages": ((1, 0.0), (2, 0.5), (1, 1.0)),
                                   "loss_session_rearm": True}),
    "N6_convex_losslock": (6, {"stages": convex(6),
                                 "loss_session_rearm": True}),
    "N8_convex_losslock": (8, {"stages": convex(8),
                                 "loss_session_rearm": True}),
    "N10_convex_losslock": (10, {"stages": convex(10),
                                   "loss_session_rearm": True}),
    "N12_convex_losslock": (12, {"stages": convex(12),
                                   "loss_session_rearm": True}),
    "N12_allin_normal": (12, {}),
    "N12_allin_losslock": (12, {"loss_session_rearm": True}),
    "N12_2+10_05_normal": (12, {"stages": ((2, 0.0), (10, 0.5))}),
}


def trades(bars, policy, kw, extra_ticks=0):
    pieces = []
    for sym, B in bars.items():
        opts = dict(kw)
        if opts.get("stages") is not None:
            opts["add_slippage_points"] = extra_ticks * TICKS[sym]
        pieces.append(simulate(B, sym, policy, **opts))
    return pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)


def metrics(T, bars):
    s = summarize(T, bars, rg.FR)
    p = hourly_mtm(T, bars, rg.FR)
    gross = s["total"] + rg.FR * s["contracts"]
    episode = T.assign(net=(T.exit_px - T.entry_px) * T.qty
                       * T.sym.map(rg.MULT) - rg.FR * T.qty) \
        .groupby(["sym", "episode"])["net"].sum()
    return {
        "net": s["total"], "dd": s["dd"], "worst": s["worst"],
        "ex26": p[p.index.year < 2026].sum(),
        "p7": gross - 7 * s["contracts"],
        "p14": gross - 14 * s["contracts"],
        "ct": s["contracts"], "worst_ep": episode.min(),
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
        policies = {}
        for lb in (21, 35, 55):
            base = weekly_policy(daily, lb, t0, t1)["baseline"]
            policies[lb] = {n: max_policy(base, n)
                            for n in {v[0] for v in LEADERS.values()}}

        cache = {}
        for lb in (21, 35, 55):
            print(f"\nL{lb} PROFIT LEADERS")
            print(f"{'name':<27}{'net':>10}{'maxDD':>10}{'worst':>10}"
                  f"{'ex26':>10}{'$7':>10}{'$14':>10}{'ct':>9}{'worstEp':>10}")
            for name, (n, kw) in LEADERS.items():
                T = trades(bars, policies[lb][n], kw)
                cache[(lb, name, 0)] = T
                m = metrics(T, bars)
                print(f"{name:<27}{m['net']:>10,.0f}{m['dd']:>10,.0f}"
                      f"{m['worst']:>10,.0f}{m['ex26']:>10,.0f}"
                      f"{m['p7']:>10,.0f}{m['p14']:>10,.0f}"
                      f"{m['ct']:>9,.0f}{m['worst_ep']:>10,.0f}")

        print("\nL35 EXTRA ADD-ORDER SLIPPAGE")
        staged = [name for name, (_, kw) in LEADERS.items() if kw.get("stages")]
        print(f"{'name':<27}{'0tick':>10}{'1tick':>10}{'2tick':>10}{'4tick':>10}")
        for name in staged:
            n, kw = LEADERS[name]
            values = []
            for ticks in (0, 1, 2, 4):
                T = (cache[(35, name, 0)] if ticks == 0 else
                     trades(bars, policies[35][n], kw, ticks))
                values.append(metrics(T, bars)["net"])
            print(f"{name:<27}" + "".join(f"{v:>10,.0f}" for v in values))

        print("\nL35 INSTRUMENT NET CONTRIBUTION ($3.50, zero extra ticks)")
        print(f"{'name':<27}{'NQ':>10}{'ES':>10}{'YM':>10}")
        for name in LEADERS:
            T = cache[(35, name, 0)]
            vals = []
            for sym in rg.MULT:
                vals.append(hourly_mtm(T.query("sym == @sym"),
                                       {sym: bars[sym]}, rg.FR).sum())
            print(f"{name:<27}" + "".join(f"{v:>10,.0f}" for v in vals))
    finally:
        strategy.signal_frame = original


if __name__ == "__main__":
    run()
