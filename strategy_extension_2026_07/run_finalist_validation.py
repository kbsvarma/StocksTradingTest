"""Attribution and stability diagnostics for the frozen iteration-11 finalist."""
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
from run_episode_search import simulate, summarize  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


SEED = 20260718
BOOTSTRAPS = 10_000
BLOCK_DAYS = 63


def pnl(T, bars, friction):
    return hourly_mtm(T, bars, friction)


def dd_stats(p):
    eq = p.cumsum()
    dd = eq - eq.cummax().clip(lower=0.0)
    trough = dd.idxmin()
    prior = eq.loc[:trough]
    positive = prior[prior >= 0]
    peak = positive.idxmax() if len(positive) else None
    return float(p.sum()), float(dd.min()), peak, trough


def daily_full(p, start, end):
    d = p.groupby(p.index.normalize()).sum()
    ix = pd.date_range(start.normalize(), end.normalize(), freq="D", tz="UTC")
    return d.reindex(ix, fill_value=0.0)


def moving_block_delta(delta):
    values = delta.to_numpy(dtype=float)
    n = len(values)
    rng = np.random.default_rng(SEED)
    totals = np.empty(BOOTSTRAPS)
    blocks = int(np.ceil(n / BLOCK_DAYS))
    offsets = np.arange(BLOCK_DAYS)
    for j in range(BOOTSTRAPS):
        starts = rng.integers(0, n, size=blocks)
        idx = (starts[:, None] + offsets[None, :]) % n
        totals[j] = values[idx.ravel()[:n]].sum()
    return np.quantile(totals, [0.025, 0.5, 0.975]), float((totals > 0).mean())


def trades_for(bars, policy, candidate=False, liquidate=False):
    kw = {"loss_session_rearm": True} if candidate else {}
    if liquidate:
        kw["liquidate_end"] = True
    return pd.concat([simulate(B, sym, policy, **kw)
                      for sym, B in bars.items()]) \
        .sort_values("exit_time").reset_index(drop=True)


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    t0, t1 = min(B.index.min() for B in bars.values()), \
             max(B.index.max() for B in bars.values())
    policies = {lb: weekly_policy(daily, lb, t0, t1)["baseline"]
                for lb in (21, 35, 55)}

    print("FINALIST SUMMARY AND COST BREAK-EVEN")
    saved = {}
    for lb, policy in policies.items():
        B, C = trades_for(bars, policy), trades_for(bars, policy, True)
        saved[lb] = (B, C)
        gb, gc = summarize(B, bars, 0.0), summarize(C, bars, 0.0)
        cb, cc = gb["contracts"], gc["contracts"]
        breakeven = (gb["total"] - gc["total"]) / (cb - cc)
        print(f"L{lb}: gross {gb['total']:,.0f}/{gc['total']:,.0f}; "
              f"contracts {cb:,}/{cc:,}; finalist wins above "
              f"${breakeven:.2f} RT/contract")
        for fr in (3.5, 7.0, 14.0):
            sb, sc = summarize(B, bars, fr), summarize(C, bars, fr)
            print(f"  ${fr:g}: base {sb['total']:,.0f}/{sb['dd']:,.0f}; "
                  f"final {sc['total']:,.0f}/{sc['dd']:,.0f}; "
                  f"delta {sc['total'] - sb['total']:+,.0f}")

    B, C = saved[35]
    print("\nL35 INSTRUMENT ATTRIBUTION ($3.50)")
    sym_pnl = {}
    print(f"{'sym':<5}{'base':>10}{'final':>10}{'delta':>10}"
          f"{'base_ct':>10}{'final_ct':>10}")
    for sym in rg.MULT:
        tb, tc = B.query("sym == @sym"), C.query("sym == @sym")
        pb = pnl(tb, {sym: bars[sym]}, rg.FR)
        pc = pnl(tc, {sym: bars[sym]}, rg.FR)
        sym_pnl[sym] = (pb, pc)
        print(f"{sym:<5}{pb.sum():>10,.0f}{pc.sum():>10,.0f}"
              f"{pc.sum()-pb.sum():>+10,.0f}{int(tb.qty.sum()):>10,}"
              f"{int(tc.qty.sum()):>10,}")

    pb, pc = pnl(B, bars, rg.FR), pnl(C, bars, rg.FR)
    start, end = min(pb.index.min(), pc.index.min()), max(pb.index.max(), pc.index.max())
    db, dc = daily_full(pb, start, end), daily_full(pc, start, end)
    print("\nL35 YEAR ATTRIBUTION ($3.50)")
    y = pd.concat([db.groupby(db.index.year).sum().rename("base"),
                   dc.groupby(dc.index.year).sum().rename("final")], axis=1)
    y["delta"] = y["final"] - y["base"]
    print(y.round().astype(int).to_string())
    print(f"delta-positive years: {(y.delta > 0).sum()}/{len(y)}")

    print("\nL35 LEAVE-ONE-OUT ($3.50 total/maxDD)")
    for excluded in rg.MULT:
        keep = [s for s in rg.MULT if s != excluded]
        xb = pd.concat([sym_pnl[s][0] for s in keep], axis=1) \
            .fillna(0.0).sum(axis=1).sort_index()
        xc = pd.concat([sym_pnl[s][1] for s in keep], axis=1) \
            .fillna(0.0).sum(axis=1).sort_index()
        sb, sc = dd_stats(xb), dd_stats(xc)
        print(f"without {excluded}: base {sb[0]:,.0f}/{sb[1]:,.0f}; "
              f"final {sc[0]:,.0f}/{sc[1]:,.0f}")

    print("\nL35 DRAWDOWN WINDOWS AND CONTRIBUTION")
    for name, p, which in (("base", pb, 0), ("final", pc, 1)):
        total, dd, peak, trough = dd_stats(p)
        print(f"{name}: {dd:,.0f}, peak={peak}, trough={trough}")
        for sym in rg.MULT:
            sp = sym_pnl[sym][which]
            lo = peak if peak is not None else sp.index.min()
            contribution = sp[(sp.index > lo) & (sp.index <= trough)].sum()
            print(f"  {sym}: {contribution:,.0f}")

    q, probability = moving_block_delta(dc - db)
    print("\nPAIRED 63-CALENDAR-DAY MOVING-BLOCK BOOTSTRAP")
    print(f"observed delta={float((dc-db).sum()):,.0f}; "
          f"2.5/50/97.5%={q[0]:,.0f}/{q[1]:,.0f}/{q[2]:,.0f}; "
          f"P(resampled delta>0)={probability:.3f}")
    print("DESCRIPTIVE ONLY: candidate selection used this same history.")

    print("\nDATA-END LIQUIDATION CHECK")
    bl = trades_for(bars, policies[35], liquidate=True)
    cl = trades_for(bars, policies[35], True, liquidate=True)
    sb, sc = summarize(bl, bars, rg.FR), summarize(cl, bars, rg.FR)
    print(f"base {sb['total']:,.0f}/{sb['dd']:,.0f}; "
          f"final {sc['total']:,.0f}/{sc['dd']:,.0f}")


if __name__ == "__main__":
    run()
