"""Deep attribution of the unlevered K7 profit plateau."""
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
from run_episode_search import simulate  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


PROD = dict(rg.SCFG)
SEED = 20260718


def full_daily(p, start, end):
    d = p.groupby(p.index.normalize()).sum()
    ix = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC")
    return d.reindex(ix, fill_value=0.0)


def bootstrap(delta, paths=10_000, block=63):
    v = delta.to_numpy(); n = len(v); blocks = int(np.ceil(n/block))
    rng = np.random.default_rng(SEED); out = np.empty(paths)
    offsets = np.arange(block)
    for j in range(paths):
        starts = rng.integers(0, n, size=blocks)
        idx = (starts[:, None] + offsets) % n
        out[j] = v[idx.ravel()[:n]].sum()
    return np.quantile(out, [.025, .5, .975]), (out > 0).mean()


def dd(p):
    eq = p.cumsum(); return float((eq-eq.cummax().clip(lower=0)).min())


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    policy = weekly_policy(daily, 35, t0, t1)["baseline"]
    configs = {"production": dict(PROD)} | {
        f"D{dn}K7": dict(PROD, donchian_n=dn, k_trail=7.0,
                          vol_gate_q=.925, session_block_et=(2, 7))
        for dn in (8, 10, 15)}
    original_cfg, original_signal = rg.SCFG, strategy.signal_frame
    trades, pnls, by_sym = {}, {}, {}
    try:
        for name, cfg in configs.items():
            rg.SCFG = cfg
            frames = {id(B): original_signal(B, cfg) for B in bars.values()}
            strategy.signal_frame = lambda B, ignored: frames[id(B)]
            T = pd.concat([simulate(B, sym, policy)
                           for sym, B in bars.items()]) \
                .sort_values("exit_time").reset_index(drop=True)
            trades[name] = T
            pnls[name] = hourly_mtm(T, bars, rg.FR)
            by_sym[name] = {sym: hourly_mtm(T.query("sym == @sym"),
                                            {sym: bars[sym]}, rg.FR)
                            for sym in rg.MULT}
    finally:
        rg.SCFG, strategy.signal_frame = original_cfg, original_signal

    start = min(p.index.min() for p in pnls.values())
    end = max(p.index.max() for p in pnls.values())
    D = {name: full_daily(p, start, end) for name, p in pnls.items()}

    print("SUMMARY")
    print(f"{'name':<12}{'net':>10}{'maxDD':>10}{'worstYr':>10}"
          f"{'ex26':>10}{'$7':>10}{'$14':>10}{'ct':>8}{'worst3y':>11}")
    for name, p in pnls.items():
        T = trades[name]; ct = int(T.qty.sum()); gross=p.sum()+rg.FR*ct
        y = p.groupby(p.index.year).sum()
        y3 = y.rolling(3).sum().dropna()
        print(f"{name:<12}{p.sum():>10,.0f}{dd(p):>10,.0f}{y.min():>10,.0f}"
              f"{p[p.index.year<2026].sum():>10,.0f}{gross-7*ct:>10,.0f}"
              f"{gross-14*ct:>10,.0f}{ct:>8,.0f}{y3.min():>11,.0f}")

    print("\nINSTRUMENT NET")
    print(f"{'name':<12}{'NQ':>11}{'ES':>11}{'YM':>11}")
    for name in configs:
        print(f"{name:<12}" + "".join(
            f"{by_sym[name][s].sum():>11,.0f}" for s in rg.MULT))

    print("\nYEARLY NET")
    Y = pd.DataFrame({name: d.groupby(d.index.year).sum()
                      for name, d in D.items()})
    print(Y.round().astype(int).to_string())

    print("\nLEAVE-ONE-OUT total/maxDD")
    for name in configs:
        cells=[]
        for excluded in rg.MULT:
            x = pd.concat([by_sym[name][s] for s in rg.MULT if s != excluded],
                          axis=1).fillna(0).sum(axis=1).sort_index()
            cells.append(f"without {excluded} {x.sum():,.0f}/{dd(x):,.0f}")
        print(f"{name:<12}" + " | ".join(cells))

    print("\nPAIRED 63-DAY BLOCK BOOTSTRAP VS PRODUCTION")
    for name in ("D8K7", "D10K7", "D15K7"):
        delta = D[name] - D["production"]
        q, prob = bootstrap(delta)
        print(f"{name}: observed {delta.sum():+,.0f}; 2.5/50/97.5% "
              f"{q[0]:+,.0f}/{q[1]:+,.0f}/{q[2]:+,.0f}; P>0={prob:.3f}")
    print("DESCRIPTIVE ONLY AFTER HISTORICAL SELECTION")


if __name__ == "__main__":
    run()
