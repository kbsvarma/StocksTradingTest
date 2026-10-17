"""Local profit plateau around the leading unlevered signal configuration."""
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


PROD = dict(rg.SCFG)


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    policy = weekly_policy(daily, 35, t0, t1)["baseline"]
    original_cfg, original_signal = rg.SCFG, strategy.signal_frame
    rows = []
    try:
        for dn in (12, 15, 18):
            for k in (3.5, 4.0, 4.5):
                for q in (0.925, 0.95):
                    for session in ((2, 6), (2, 7)):
                        cfg = dict(PROD, donchian_n=dn, k_trail=k,
                                   vol_gate_q=q, session_block_et=session)
                        rg.SCFG = cfg
                        frames = {id(B): original_signal(B, cfg)
                                  for B in bars.values()}
                        strategy.signal_frame = lambda B, ignored: frames[id(B)]
                        T = pd.concat([simulate(B, sym, policy)
                                       for sym, B in bars.items()]) \
                            .sort_values("exit_time").reset_index(drop=True)
                        p = hourly_mtm(T, bars, rg.FR)
                        eq = p.cumsum()
                        dd = eq - eq.cummax().clip(lower=0)
                        y = p.groupby(p.index.year).sum()
                        ct = int(T.qty.sum())
                        gross = p.sum() + rg.FR * ct
                        rows.append({
                            "cell": f"D{dn}K{k:g}Q{q:g}S{session[0]}-{session[1]}",
                            "net": p.sum(), "dd": dd.min(), "worst": y.min(),
                            "ex26": p[p.index.year < 2026].sum(),
                            "p7": gross - 7 * ct, "p14": gross - 14 * ct,
                            "a": p[p.index.year <= 2015].sum(),
                            "b": p[(p.index.year >= 2016) &
                                   (p.index.year <= 2020)].sum(),
                            "c": p[p.index.year >= 2021].sum(), "ct": ct,
                            "per": p.sum() / ct,
                        })
    finally:
        rg.SCFG, strategy.signal_frame = original_cfg, original_signal
    R = pd.DataFrame(rows).sort_values("net", ascending=False)
    print(f"{'cell':<22}{'net':>10}{'maxDD':>10}{'worst':>10}"
          f"{'ex26':>10}{'$7':>10}{'$14':>10}{'11-15':>10}"
          f"{'16-20':>10}{'21-end':>10}{'ct':>8}{'$/ct':>8}")
    for r in R.itertuples():
        print(f"{r.cell:<22}{r.net:>10,.0f}{r.dd:>10,.0f}"
              f"{r.worst:>10,.0f}{r.ex26:>10,.0f}{r.p7:>10,.0f}"
              f"{r.p14:>10,.0f}{r.a:>10,.0f}{r.b:>10,.0f}"
              f"{r.c:>10,.0f}{r.ct:>8,.0f}{r.per:>8.2f}")


if __name__ == "__main__":
    run()
