"""Profit-first bounded sizing/pyramiding screen."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_episode_search import simulate, summarize  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


SIZES = (3, 4, 5, 6, 7, 8, 9, 10, 12)


def max_policy(base: pd.DataFrame, maximum: int) -> pd.DataFrame:
    out = base.copy()
    lcols = [c for c in out if c.startswith("L_")]
    for col in lcols:
        out.loc[out[col] == 3, col] = maximum
    return out


def plan_shapes(n: int):
    remaining = n - 1
    half = int(np.ceil(remaining / 2))
    rest = remaining - half
    remaining2 = n - 2
    half2 = int(np.ceil(remaining2 / 2))
    rest2 = remaining2 - half2
    unit_ladder = tuple([(1, 0.0)]
                        + [(1, 0.5 * j) for j in range(1, n)])
    convex = [(1, 0.0)]
    left = n - 1
    for qty, level in ((1, 0.5), (2, 1.0)):
        take = min(left, qty)
        if take:
            convex.append((take, level))
            left -= take
    if left:
        convex.append((left, 1.5))
    shapes = {
        "allin": None,
        "pyr1_rest_05": ((1, 0.0), (n - 1, 0.5)),
        "pyr2_rest_05": ((2, 0.0), (n - 2, 0.5)),
        "pyr1_half05_rest10": tuple(
            x for x in ((1, 0.0), (half, 0.5), (rest, 1.0)) if x[0]),
        "pyr2_half05_rest10": tuple(
            x for x in ((2, 0.0), (half2, 0.5), (rest2, 1.0)) if x[0]),
        "unit_ladder_05": unit_ladder,
        "convex_ladder": tuple(convex),
    }
    return shapes


def cells():
    for n in SIZES:
        for shape, stages in plan_shapes(n).items():
            base = {} if stages is None else {"stages": stages}
            yield n, f"N{n}_{shape}_normal", dict(base), stages
            yield n, f"N{n}_{shape}_losslock", \
                dict(base, loss_session_rearm=True), stages
        for shape in ("allin", "pyr1_half05_rest10"):
            stages = plan_shapes(n)[shape]
            base = {} if stages is None else {"stages": stages}
            yield n, f"N{n}_{shape}_allstoplock", \
                dict(base, session_rearm=True), stages


def transient_atr_risk(stages, n):
    if stages is None:
        return 2.0 * n
    return sum(qty * (2.0 + level) for qty, level in stages)


def run_grid(lookback=35):
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    # The signal frame is identical in every cell; cache it so the screen spends
    # time on position paths rather than recomputing rolling quantiles.
    frames = {id(B): strategy.signal_frame(B, rg.SCFG) for B in bars.values()}
    original = strategy.signal_frame
    strategy.signal_frame = lambda B, cfg: frames[id(B)]
    try:
        daily = {s: B["close"].resample("1D").last().dropna()
                 for s, B in bars.items()}
        t0, t1 = min(B.index.min() for B in bars.values()), \
                 max(B.index.max() for B in bars.values())
        base = weekly_policy(daily, lookback, t0, t1)["baseline"]
        policies = {n: max_policy(base, n) for n in SIZES}
        rows = []
        for n, name, kw, stages in cells():
            T = pd.concat([simulate(B, sym, policies[n], **kw)
                           for sym, B in bars.items()]) \
                .sort_values("exit_time").reset_index(drop=True)
            s = summarize(T, bars, rg.FR)
            p = hourly_mtm(T, bars, rg.FR)
            gross = s["total"] + rg.FR * s["contracts"]
            rows.append({
                "name": name, "N": n, "total": s["total"], "dd": s["dd"],
                "worst": s["worst"],
                "ex2026": p[p.index.year < 2026].sum(),
                "pnl7": gross - 7.0 * s["contracts"],
                "pnl14": gross - 14.0 * s["contracts"],
                "contracts": s["contracts"],
                "atr_risk": transient_atr_risk(stages, n),
                "kw": kw,
            })
        return pd.DataFrame(rows).sort_values("total", ascending=False), bars, base
    finally:
        strategy.signal_frame = original


def print_table(R, title, count=25):
    print(f"\n{title}")
    print(f"{'name':<43}{'net':>10}{'maxDD':>10}{'worst':>10}"
          f"{'ex26':>10}{'$7':>10}{'$14':>10}{'ct':>9}{'ATRrisk':>9}")
    for r in R.head(count).itertuples():
        print(f"{r.name:<43}{r.total:>10,.0f}{r.dd:>10,.0f}"
              f"{r.worst:>10,.0f}{r.ex2026:>10,.0f}{r.pnl7:>10,.0f}"
              f"{r.pnl14:>10,.0f}{r.contracts:>9,.0f}{r.atr_risk:>9.1f}")


def run():
    R, _, _ = run_grid(35)
    print_table(R, "TOP L35 CELLS BY $3.50 NET PROFIT", 30)
    print_table(R.sort_values("ex2026", ascending=False),
                "TOP L35 CELLS EXCLUDING INCOMPLETE 2026", 20)
    positive14 = R[R.pnl14 > 0].sort_values("pnl14", ascending=False)
    print_table(positive14, "CELLS STILL NET-POSITIVE AT $14 RT", 20)


if __name__ == "__main__":
    run()
