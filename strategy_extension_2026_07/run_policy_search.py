"""Structural portfolio-policy screen defined in SEARCH_PROTOCOL.md.

Print-only research program: it does not touch bot state, config, or brokers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "trend_bot"), str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from rotation import monday_of_week, trailing_returns, weights_for  # noqa: E402


START = pd.Timestamp("2011-01-01", tz="UTC")
POLICIES = (
    "baseline",
    "absolute_cash",
    "dual_switch_1",
    "dual_switch_3",
    "majority_switch_1",
    "balanced_pair",
    "risk_budget_shift",
    "short_weakest_1",
)


def weekly_policy(daily: dict[str, pd.Series], lookback: int,
                  t0: pd.Timestamp, t1: pd.Timestamp) -> dict[str, pd.DataFrame]:
    mondays = pd.date_range(monday_of_week(t0), t1, freq="W-MON", tz="UTC")
    rows = {name: {} for name in POLICIES}
    for mon in mondays:
        rets = trailing_returns(daily, mon, lookback)
        if not all(np.isfinite(list(rets.values()))):
            base = {s: 1 for s in rets}
            for name in POLICIES:
                rows[name][mon] = {f"L_{s}": base[s] for s in rets} | {
                    f"S_{s}": 0 for s in rets}
            continue
        rank = sorted(rets, key=rets.get, reverse=True)
        winner, weakest = rank[0], rank[-1]
        leader, loser = rets[winner], rets[weakest]
        spread_pp = (leader - rets[rank[1]]) * 100
        base = weights_for(rets, "aggressive", 1.0, 3)["weights"]

        def row(long=None, short=None):
            long = dict(long or {s: 0 for s in rets})
            short = dict(short or {s: 0 for s in rets})
            return ({f"L_{s}": int(long.get(s, 0)) for s in rets} |
                    {f"S_{s}": int(short.get(s, 0)) for s in rets})

        rows["baseline"][mon] = row(base)
        rows["absolute_cash"][mon] = row(base if leader > 0 else None)
        rows["dual_switch_1"][mon] = (
            row(base) if leader > 0 else row(short={weakest: 1}))
        rows["dual_switch_3"][mon] = (
            row(base) if leader > 0 else row(short={weakest: 3}))
        rows["majority_switch_1"][mon] = (
            row(short={weakest: 1}) if sum(v < 0 for v in rets.values()) >= 2
            else row(base))
        if leader > 0 and loser < 0 and spread_pp >= 1.0:
            rows["balanced_pair"][mon] = row({winner: 2}, {weakest: 1})
        elif leader <= 0:
            rows["balanced_pair"][mon] = row(short={weakest: 1})
        else:
            rows["balanced_pair"][mon] = row(base)
        if base.get(winner) == 3 and loser < 0:
            rows["risk_budget_shift"][mon] = row({winner: 2}, {weakest: 1})
        else:
            rows["risk_budget_shift"][mon] = row(base)
        rows["short_weakest_1"][mon] = (
            row(short={weakest: 1}) if loser < 0 else row())
    return {name: pd.DataFrame(data).T.sort_index()
            for name, data in rows.items()}


def simulate(B: pd.DataFrame, sym: str, policy: pd.DataFrame) -> pd.DataFrame:
    f = strategy.signal_frame(B, rg.SCFG)
    atr = f["atr"].values
    long_sig = f["signal"].values
    dlow = B["close"].rolling(rg.SCFG["donchian_n"]).min().shift(1)
    short_sig = ((B["close"] < dlow) & f["vol_ok"] & f["session_ok"]
                 & f["atr"].notna()).values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R, ts = B["roll"].values, B.index
    rows = []
    pos = pend = qty = 0
    entry = stop = high_water = low_water = np.nan
    entry_time = None

    def allocation(stamp):
        mon = monday_of_week(stamp)
        if mon not in policy.index:
            return 1, 0
        return int(policy.loc[mon, f"L_{sym}"]), int(policy.loc[mon, f"S_{sym}"])

    for i in range(len(B)):
        if pend and R[i]:
            pend = qty = 0
        if pend:
            pos, pend = pend, 0
            entry, entry_time = O[i], ts[i]
            if pos > 0:
                high_water = entry
                stop = entry - rg.SCFG["k_trail"] * atr[i - 1]
            else:
                low_water = entry
                stop = entry + rg.SCFG["k_trail"] * atr[i - 1]
            if np.isnan(stop):
                pos = qty = 0
        if pos and R[i]:
            exit_px = C[i - 1] if i else O[i]
            points = (exit_px - entry) * pos
            rows.append((entry_time, ts[i], sym, entry, exit_px, points,
                         "roll", pos, qty))
            pos = qty = 0
        if pos > 0:
            if L[i] <= stop:
                exit_px = min(stop, O[i])
                rows.append((entry_time, ts[i], sym, entry, exit_px,
                             exit_px - entry, "stop", 1, qty))
                pos = qty = 0
            else:
                high_water = max(high_water, H[i])
                stop = max(stop, high_water - rg.SCFG["k_trail"] * atr[i])
        elif pos < 0:
            if H[i] >= stop:
                exit_px = max(stop, O[i])
                rows.append((entry_time, ts[i], sym, entry, exit_px,
                             entry - exit_px, "stop", -1, qty))
                pos = qty = 0
            else:
                low_water = min(low_water, L[i])
                stop = min(stop, low_water + rg.SCFG["k_trail"] * atr[i])
        if pos == 0 and pend == 0 and i > rg.SCFG["donchian_n"]:
            lq, sq = allocation(ts[i])
            if long_sig[i] and lq > 0:
                pend, qty = 1, lq
            elif short_sig[i] and sq > 0:
                pend, qty = -1, sq
    T = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "dir", "qty",
    ])
    return T[T["entry_time"] >= START].reset_index(drop=True)


def hourly_mtm(T: pd.DataFrame, bars: dict[str, pd.DataFrame],
               friction: float) -> pd.Series:
    pieces = []
    for sym, trades in T.groupby("sym"):
        B, out = bars[sym], {}
        ix, closes = B.index, B["close"].values

        def add(stamp, value):
            out[stamp] = out.get(stamp, 0.0) + float(value)

        for tr in trades.itertuples():
            j = ix.searchsorted(tr.entry_time)
            k = ix.searchsorted(tr.exit_time)
            scale = tr.dir * tr.qty * rg.MULT[sym]
            if j == k:
                add(ix[k], (tr.exit_px - tr.entry_px) * scale
                    - friction * tr.qty)
                continue
            add(ix[j], (closes[j] - tr.entry_px) * scale)
            for z in range(j + 1, k):
                add(ix[z], (closes[z] - closes[z - 1]) * scale)
            add(ix[k], (tr.exit_px - closes[k - 1]) * scale
                - friction * tr.qty)
        pieces.append(pd.Series(out, dtype=float))
    return pd.concat(pieces, axis=1).fillna(0).sum(axis=1).sort_index()


def summarize(T: pd.DataFrame, bars: dict[str, pd.DataFrame],
              friction: float) -> dict:
    p = hourly_mtm(T, bars, friction)
    eq = p.cumsum()
    # Include initial cash=0 in the peak series.
    dd = eq - eq.cummax().clip(lower=0.0)
    daily = p.groupby(p.index.normalize()).sum()
    yearly = p.groupby(p.index.year).sum()
    sd = daily.std(ddof=1)
    return {
        "total": float(p.sum()),
        "maxDD": float(dd.min()),
        "sharpe": float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan,
        "worst": float(yearly.min()),
        "neg_years": int((yearly < 0).sum()),
        "2011_15": float(p[p.index.year <= 2015].sum()),
        "2016_20": float(p[(p.index.year >= 2016) & (p.index.year <= 2020)].sum()),
        "2021_end": float(p[p.index.year >= 2021].sum()),
        "ex_2022": float(p[p.index.year != 2022].sum()),
        "y2022": float(yearly.get(2022, 0)),
        "trades": int(len(T)),
        "shorts": int((T["dir"] < 0).sum()),
        "contracts": int(T["qty"].sum()),
    }


def baseline_parity(B: pd.DataFrame, sym: str, T: pd.DataFrame,
                    policy: pd.DataFrame):
    def weight(stamp):
        mon = monday_of_week(stamp)
        return int(policy.loc[mon, f"L_{sym}"]) if mon in policy.index else 1
    ref = rg.simulate_weighted(B, sym, weight)
    a = T[T["dir"] > 0][[
        "entry_time", "exit_time", "entry_px", "exit_px", "points", "reason", "qty"]]
    b = ref[["entry_time", "exit_time", "entry_px", "exit_px", "points", "reason", "qty"]]
    if not a.reset_index(drop=True).equals(b.reset_index(drop=True)):
        raise AssertionError(f"{sym}: baseline does not match corrected reference")


def run(lookback: int = 35):
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    policy = weekly_policy(daily, lookback,
                           min(B.index.min() for B in bars.values()),
                           max(B.index.max() for B in bars.values()))
    trades = {}
    for name in POLICIES:
        pieces = [simulate(B, sym, policy[name]) for sym, B in bars.items()]
        trades[name] = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
    for sym, B in bars.items():
        baseline_parity(B, sym,
                        trades["baseline"].query("sym == @sym"),
                        policy["baseline"])

    print(f"L{lookback} policy screen; hourly MTM; baseline parity PASS")
    header = (f"{'policy':<22}{'total':>10}{'maxDD':>10}{'Sharpe':>9}"
              f"{'worst':>10}{'negY':>6}{'11-15':>10}{'16-20':>10}"
              f"{'21-end':>10}{'ex22':>10}{'2022':>9}{'nS':>6}")
    print(header)
    for name in POLICIES:
        s = summarize(trades[name], bars, rg.FR)
        print(f"{name:<22}{s['total']:>10,.0f}{s['maxDD']:>10,.0f}"
              f"{s['sharpe']:>9.2f}{s['worst']:>10,.0f}{s['neg_years']:>6}"
              f"{s['2011_15']:>10,.0f}{s['2016_20']:>10,.0f}"
              f"{s['2021_end']:>10,.0f}{s['ex_2022']:>10,.0f}"
              f"{s['y2022']:>9,.0f}{s['shorts']:>6}")
    print("\nCost stress (total / maxDD):")
    for name in POLICIES:
        cells = []
        for fr in (3.5, 7.0, 14.0):
            s = summarize(trades[name], bars, fr)
            cells.append(f"${fr:g}: {s['total']:,.0f}/{s['maxDD']:,.0f}")
        print(f"  {name:<20} " + " | ".join(cells))
    return trades, bars


if __name__ == "__main__":
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else 35
    run(lookback)
