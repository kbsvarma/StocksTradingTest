"""
MES ORB Long-Only Deep Analysis
================================
Implements the deep-analysis recommendation:
  - Long-only (fade short side — it's structurally weak)
  - Configurable hard close (test 15:15 vs 15:30 vs 15:45)
  - Optional trend filter (prior day close > prior-prior close)
  - Train 2019-2022 / Test 2023-2026 split
  - Bootstrap 95% CI on avg/trade
  - 10-contract scaling

Usage:
  python orb_long_only_analysis.py              # sweep + best config detail
  python orb_long_only_analysis.py --contracts 1
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

CSV_PATH   = Path(__file__).parent / "data" / "mes_1min.csv"
MULTIPLIER = 5      # MES: $5/point
COMMISSION = 4.50   # RT per contract

TRAIN = set(range(2019, 2023))
TEST  = set(range(2023, 2027))


# ── load ──────────────────────────────────────────────────────────────
def load() -> pd.DataFrame:
    print("Loading MES 1m data ...", flush=True)
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/New_York")
    df = pd.read_csv(CSV_PATH, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    df = df.sort_index()
    print(f"  {len(df):,} bars  |  {df.index[0].date()} → {df.index[-1].date()}", flush=True)
    return df


# ── day cache ─────────────────────────────────────────────────────────
@dataclass
class Day:
    date:      date
    year:      int
    skip:      str
    rng_h:     float = 0.0
    rng_l:     float = 0.0
    width:     float = 0.0
    prev_up:   bool  = False   # prior day close > prior-prior close
    times:     np.ndarray = field(default_factory=lambda: np.array([]))
    highs:     np.ndarray = field(default_factory=lambda: np.array([]))
    lows:      np.ndarray = field(default_factory=lambda: np.array([]))
    opens:     np.ndarray = field(default_factory=lambda: np.array([]))
    closes:    np.ndarray = field(default_factory=lambda: np.array([]))


def build_days(df: pd.DataFrame) -> list[Day]:
    rth = df.between_time("09:30", "15:59").copy()
    rth["_min"]  = rth.index.hour * 60 + rth.index.minute
    rth["_date"] = rth.index.date

    # Daily closes for trend filter
    daily_close: dict[date, float] = {}
    for d, g in rth.groupby("_date"):
        daily_close[d] = float(g["close"].iloc[-1])
    sorted_dates = sorted(daily_close)
    prev_up: dict[date, bool] = {}
    for i, d in enumerate(sorted_dates):
        if i < 2:
            prev_up[d] = True   # no filter on first two days
        else:
            prev_up[d] = daily_close[sorted_dates[i-1]] > daily_close[sorted_dates[i-2]]

    days = []
    for d in sorted_dates:
        g = rth[rth["_date"] == d]
        range_bars = g[g["_min"] <= 9*60 + 44]
        if len(range_bars) < 5:
            days.append(Day(date=d, year=d.year, skip="THIN_RANGE")); continue
        rh = float(range_bars["high"].max())
        rl = float(range_bars["low"].min())
        w  = round(rh - rl, 4)
        post = g[g["_min"] >= 9*60 + 45]
        if post.empty:
            days.append(Day(date=d, year=d.year, skip="NO_POST")); continue
        days.append(Day(
            date=d, year=d.year, skip="",
            rng_h=rh, rng_l=rl, width=w,
            prev_up=prev_up.get(d, True),
            times=post["_min"].values.astype(np.float32),
            highs=post["high"].values.astype(np.float32),
            lows=post["low"].values.astype(np.float32),
            opens=post["open"].values.astype(np.float32),
            closes=post["close"].values.astype(np.float32),
        ))
    print(f"  Built {len(days)} day caches.", flush=True)
    return days


# ── simulate one day (long-only) ──────────────────────────────────────
def sim_day(day: Day,
            target_mult: float,
            min_w: float,
            max_w: float,
            hard_close_min: int,
            trend_filter: bool,
            contracts: int) -> Optional[dict]:

    if day.skip:
        return None
    if day.width < min_w or day.width > max_w:
        return None
    if trend_filter and not day.prev_up:
        return None

    # First bar at/after 09:45 where close > range high = long signal
    cutoff = 15 * 60 + 30   # 15:30 entry cutoff
    mask = day.times <= cutoff
    c = day.closes[mask]
    hits = np.where(c > day.rng_h)[0]
    if len(hits) == 0:
        return None

    sig_idx   = hits[0]
    entry_idx = sig_idx + 1
    if entry_idx >= len(day.opens):
        return None

    entry  = float(day.opens[entry_idx])
    stop   = day.rng_l
    target = entry + target_mult * day.width

    t_lo    = day.lows[entry_idx:]
    t_hi    = day.highs[entry_idx:]
    t_cl    = day.closes[entry_idx:]
    t_times = day.times[entry_idx:]

    stop_hit   = t_lo <= stop
    target_hit = t_hi >= target
    hc_hit     = t_times >= hard_close_min
    any_hit    = stop_hit | target_hit | hc_hit

    if not np.any(any_hit):
        exit_p  = float(t_cl[-1]) if len(t_cl) else entry
        reason  = "HC"
    else:
        i = int(np.argmax(any_hit))
        if stop_hit[i]:
            exit_p, reason = stop,   "STOP"
        elif target_hit[i]:
            exit_p, reason = target, "TGT"
        else:
            exit_p, reason = float(t_cl[i]), "HC"

    pts = exit_p - entry
    pnl = (pts * MULTIPLIER - COMMISSION) * contracts

    return dict(
        date=day.date, year=day.year,
        width=day.width, entry=entry, exit=exit_p,
        reason=reason, pts=pts, pnl=round(pnl, 2),
    )


# ── run one config ────────────────────────────────────────────────────
def run(days: list[Day],
        target_mult: float,
        min_w: float,
        max_w: float,
        hard_close_min: int,
        trend_filter: bool,
        contracts: int) -> pd.DataFrame:
    rows = [sim_day(d, target_mult, min_w, max_w, hard_close_min, trend_filter, contracts)
            for d in days]
    rows = [r for r in rows if r is not None]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── metrics + CI ─────────────────────────────────────────────────────
def mets(t: pd.DataFrame) -> dict:
    if t.empty:
        return dict(n=0, total=0, avg=0, wr=0, dd=0, sh=0, ci_lo=0, ci_hi=0)
    p   = t["pnl"].values
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1e-9
    sh  = (avg / std) * np.sqrt(252) if std > 0 else 0
    wr  = float((p > 0).mean()) * 100
    # bootstrap CI on avg/trade
    boots = [np.mean(np.random.choice(p, size=len(p), replace=True)) for _ in range(2000)]
    ci_lo, ci_hi = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    return dict(n=len(t), total=round(float(p.sum()),0), avg=round(avg,2),
                wr=round(wr,1), dd=round(dd,0), sh=round(sh,3),
                ci_lo=round(ci_lo,2), ci_hi=round(ci_hi,2))


# ── sweep ─────────────────────────────────────────────────────────────
def sweep(days: list[Day], contracts: int) -> None:
    configs = []
    for tm   in [1.5, 2.0, 2.5, 3.0]:
        for mxw in [15, 20, 25, 30]:
            for hc  in [15*60+15, 15*60+30, 15*60+45]:
                for tf  in [False, True]:
                    t_tr = run(days, tm, 1.0, mxw, hc, tf, contracts)
                    t_te = run(days, tm, 1.0, mxw, hc, tf, contracts)
                    tr = t_tr[t_tr["year"].isin(TRAIN)]
                    te = t_te[t_te["year"].isin(TEST)]
                    if len(tr) < 20 or len(te) < 10:
                        continue
                    m_tr = mets(tr)
                    m_te = mets(te)
                    configs.append(dict(
                        tm=tm, mxw=mxw,
                        hc=f"{hc//60}:{hc%60:02d}",
                        tf=tf,
                        tr_n=m_tr["n"], tr_pnl=m_tr["total"], tr_sh=m_tr["sh"], tr_wr=m_tr["wr"],
                        te_n=m_te["n"], te_pnl=m_te["total"], te_sh=m_te["sh"], te_wr=m_te["wr"],
                        te_ci_lo=m_te["ci_lo"], te_ci_hi=m_te["ci_hi"],
                        robust=(m_tr["total"] > 0 and m_te["total"] > 0),
                        ci_excl_0=(m_te["ci_lo"] > 0),
                    ))

    df = pd.DataFrame(configs).sort_values("te_sh", ascending=False)
    robust = df[df["robust"]]
    ci_ok  = df[df["ci_excl_0"]]

    print(f"\n{'═'*115}")
    print(f"  SWEEP — TOP ROBUST CONFIGS (both train+test positive) sorted by test Sharpe")
    print(f"{'═'*115}")
    hdr = (f"{'tm':>4} {'mxw':>4} {'hc':>6} {'tf':>4}  "
           f"{'Tr.N':>5} {'Tr.PnL':>9} {'Tr.Sh':>7} {'Tr.WR':>6}  "
           f"{'Te.N':>5} {'Te.PnL':>9} {'Te.Sh':>7} {'Te.WR':>6}  "
           f"{'CI 95%':>20}  {'CI>0':>5}")
    print(hdr); print("-"*len(hdr))
    for _, r in robust.head(15).iterrows():
        ci_tag = "✅" if r.ci_excl_0 else "  "
        print(f"{r.tm:>4.1f} {int(r.mxw):>4} {r.hc:>6} {'Y' if r.tf else 'N':>4}  "
              f"{int(r.tr_n):>5} ${r.tr_pnl:>8,.0f} {r.tr_sh:>7.3f} {r.tr_wr:>5.1f}%  "
              f"{int(r.te_n):>5} ${r.te_pnl:>8,.0f} {r.te_sh:>7.3f} {r.te_wr:>5.1f}%  "
              f"[${r.te_ci_lo:>7,.2f}, ${r.te_ci_hi:>7,.2f}]  {ci_tag}")

    print(f"\n  Total robust configs : {len(robust)}")
    print(f"  CI excludes 0       : {len(ci_ok)}")


# ── detailed report ────────────────────────────────────────────────────
def detail(t: pd.DataFrame, label: str, contracts: int) -> None:
    if t.empty:
        print(f"  {label}: no trades"); return

    tr = t[t["year"].isin(TRAIN)]
    te = t[t["year"].isin(TEST)]
    m_all = mets(t)
    m_tr  = mets(tr)
    m_te  = mets(te)

    print(f"\n{'═'*70}")
    print(f"  {label}")
    print(f"{'═'*70}")
    print(f"  Period     : {t['date'].min()} → {t['date'].max()}")
    print(f"  Contracts  : {contracts}  ($5/pt each)")
    wins  = t[t["pnl"] > 0]
    loses = t[t["pnl"] <= 0]
    stops = t[t["reason"] == "STOP"]
    tgts  = t[t["reason"] == "TGT"]
    hcs   = t[t["reason"] == "HC"]
    print(f"  Trades     : {m_all['n']}  (TGT={len(tgts)} STOP={len(stops)} HC={len(hcs)})")
    print(f"  Win rate   : {m_all['wr']}%")
    print(f"  Avg winner : ${wins['pnl'].mean():,.1f}" if len(wins) else "  No winners")
    print(f"  Avg loser  : ${loses['pnl'].mean():,.1f}" if len(loses) else "  No losers")
    if len(wins) and len(loses):
        print(f"  R:R actual : {abs(wins['pnl'].mean()/loses['pnl'].mean()):.2f}×")
    print(f"  Total P&L  : ${m_all['total']:,}")
    ann = m_all["total"] / max((t["date"].max() - t["date"].min()).days / 365.25, 1)
    print(f"  Ann. P&L   : ${ann:,.0f}/yr")
    print(f"  Max DD     : ${m_all['dd']:,}")
    print(f"  Sharpe     : {m_all['sh']}")
    ci_tag = "✅ excludes 0" if m_all["ci_lo"] > 0 else ("⚠️  includes 0" if m_all["ci_hi"] > 0 else "❌ both negative")
    print(f"  95% CI     : [${m_all['ci_lo']:,.2f}, ${m_all['ci_hi']:,.2f}]  {ci_tag}")

    print(f"\n  {'Year':<6} {'N':>5} {'WR':>6} {'P&L':>10} {'MaxDD':>9}")
    print(f"  {'─'*40}")
    for yr, g in t.groupby("year"):
        m = mets(g)
        bar = "█" * max(0, int(m["total"] / 500)) if m["total"] > 0 else "▓" * max(0, int(-m["total"] / 500))
        print(f"  {yr:<6} {m['n']:>5} {m['wr']:>5.1f}%  ${m['total']:>8,}  ${m['dd']:>8,}  {bar}")

    print(f"\n  TRAIN 2019-2022: n={m_tr['n']}  pnl=${m_tr['total']:,}  avg/tr=${m_tr['avg']}  wr={m_tr['wr']}%  sh={m_tr['sh']}")
    ci_tr = "✅" if m_tr["ci_lo"] > 0 else ("⚠️" if m_tr["ci_hi"] > 0 else "❌")
    print(f"    95% CI: [${m_tr['ci_lo']:,.2f}, ${m_tr['ci_hi']:,.2f}]  {ci_tr}")
    print(f"  TEST  2023-2026: n={m_te['n']}  pnl=${m_te['total']:,}  avg/tr=${m_te['avg']}  wr={m_te['wr']}%  sh={m_te['sh']}")
    ci_te = "✅" if m_te["ci_lo"] > 0 else ("⚠️" if m_te["ci_hi"] > 0 else "❌")
    print(f"    95% CI: [${m_te['ci_lo']:,.2f}, ${m_te['ci_hi']:,.2f}]  {ci_te}")


# ── main ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contracts", type=int, default=10)
    parser.add_argument("--no-sweep",  action="store_true")
    args = parser.parse_args()
    c = args.contracts

    df   = load()
    days = build_days(df)

    if not args.no_sweep:
        print("\nRunning sweep …", flush=True)
        sweep(days, c)

    # ── Deep-analysis best config: long-only, maxW=25, HC=15:15, no trend filter
    print("\n\nRunning specific configs from deep analysis …")
    hc1515 = 15*60+15
    hc1530 = 15*60+30
    hc1545 = 15*60+45

    configs_to_show = [
        ("Long-only  maxW=25  HC=15:15  no-trend",  3.0, 25, hc1515, False),
        ("Long-only  maxW=25  HC=15:15  trend-filt", 3.0, 25, hc1515, True),
        ("Long-only  maxW=25  HC=15:30  no-trend",  3.0, 25, hc1530, False),
        ("Long-only  maxW=20  HC=15:15  no-trend",  3.0, 20, hc1515, False),
        ("Long-only  maxW=30  HC=15:15  no-trend",  3.0, 30, hc1515, False),
        ("Long-only  maxW=25  HC=15:45  no-trend",  3.0, 25, hc1545, False),
    ]

    for label, tm, mxw, hc, tf in configs_to_show:
        t = run(days, tm, 1.0, mxw, hc, tf, c)
        detail(t, label, c)


if __name__ == "__main__":
    main()
