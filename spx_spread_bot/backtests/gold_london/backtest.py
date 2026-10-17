"""
Gold London Close Fade — Backtest
===================================
Strategy:
  Morning trend  = 8:00am → 11:50am ET price direction
  Entry          = 12:00pm ET bar close (London fix)
  Direction      = FADE the morning trend
  Target         = T ticks from entry
  Stop           = S ticks from entry
  Time exit      = 13:30 ET
  Filter         = skip if morning range < MIN_RANGE ticks

MGC specs:
  Contract  : 10 troy oz (Micro Gold)
  Tick size : $0.10/oz → $1.00/tick/contract
  Commission: $1.00 RT/contract (retail IBKR estimate)

Run:
  python backtest.py                          # default params, 10 contracts
  python backtest.py --sweep                  # full param sweep
  python backtest.py --target 20 --stop 10   # specific params
  python backtest.py --contracts 1
"""
from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

DATA    = Path(__file__).parent / "data" / "mgc_1m.csv"
TICK    = 1.0    # $ per tick per contract (MGC $0.10/oz × 10oz)
COMM    = 1.00   # RT commission per contract

TRAIN_YEARS = list(range(2019, 2023))   # 2019-2022
TEST_YEARS  = list(range(2023, 2027))   # 2023-2026


# ── data ──────────────────────────────────────────────────────────────
def load() -> pd.DataFrame:
    print("Loading MGC 1m data ...", flush=True)
    df = pd.read_csv(DATA, index_col=0)
    idx = pd.to_datetime(df.index, utc=True)
    if idx.tzinfo is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert("America/New_York")
    df = df.sort_index()
    print(f"  bars: {len(df):,}  |  {df.index[0].date()} → {df.index[-1].date()}", flush=True)
    return df


# ── core simulation ───────────────────────────────────────────────────
def simulate(df: pd.DataFrame,
             target_t: int,
             stop_t: int,
             min_range_t: int,
             contracts: int = 10) -> pd.DataFrame:
    """Run one parameter set; return per-trade DataFrame."""
    rows = []
    for day, session in df.groupby(df.index.date):
        morning = session.between_time("08:00", "11:50")
        noon    = session.between_time("12:00", "12:00")
        post    = session.between_time("12:01", "13:30")

        if morning.empty or noon.empty or post.empty:
            continue

        m_open  = float(morning.iloc[0]["open"])
        m_close = float(morning.iloc[-1]["close"])
        rng_t   = abs(round((m_close - m_open) / 0.10))

        if rng_t < min_range_t:
            continue

        trend     = 1 if m_close > m_open else -1
        direction = -trend        # fade
        entry     = float(noon.iloc[-1]["close"])
        target_p  = entry + direction * target_t * 0.10
        stop_p    = entry - direction * stop_t   * 0.10

        exit_p = None
        reason = "TIME"
        for _, bar in post.iterrows():
            lo, hi = float(bar["low"]), float(bar["high"])
            if direction == 1:
                if lo <= stop_p:   exit_p = stop_p;   reason = "STOP";   break
                if hi >= target_p: exit_p = target_p; reason = "TARGET"; break
            else:
                if hi >= stop_p:   exit_p = stop_p;   reason = "STOP";   break
                if lo <= target_p: exit_p = target_p; reason = "TARGET"; break

        if exit_p is None:
            exit_p = float(post.iloc[-1]["close"])

        raw_t = round((exit_p - entry) * direction / 0.10)
        pnl   = raw_t * TICK * contracts - COMM * contracts

        rows.append({
            "date":      pd.Timestamp(day),
            "year":      pd.Timestamp(day).year,
            "direction": "L" if direction == 1 else "S",
            "range_t":   rng_t,
            "entry":     entry,
            "exit":      exit_p,
            "reason":    reason,
            "ticks":     raw_t,
            "pnl":       round(pnl, 2),
        })

    return pd.DataFrame(rows)


# ── metrics ───────────────────────────────────────────────────────────
def metrics(t: pd.DataFrame, label: str = "", contracts: int = 10) -> dict:
    if t is None or t.empty:
        return dict(label=label, n=0, total=0, win_pct=0, avg=0, dd=0, sharpe=0,
                    exp=0, targets=0, stops=0, times=0)
    p   = t["pnl"].values
    cum = np.cumsum(p)
    dd  = float((cum - np.maximum.accumulate(cum)).min())
    avg = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else 1.0
    sh  = (avg / std) * np.sqrt(252) if std > 0 else 0.0
    wr  = float((p > 0).mean())
    tgt = float((t["reason"] == "TARGET").mean()) if "reason" in t.columns else 0
    stp = float((t["reason"] == "STOP").mean())   if "reason" in t.columns else 0
    tim = float((t["reason"] == "TIME").mean())   if "reason" in t.columns else 0
    return dict(label=label, n=len(t), total=round(float(p.sum()), 0),
                win_pct=round(wr * 100, 1), avg=round(avg, 2),
                dd=round(dd, 0), sharpe=round(sh, 3),
                exp=round(avg, 2), targets=round(tgt*100,1),
                stops=round(stp*100,1), times=round(tim*100,1))


def ptable(rows: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{'═'*100}")
        print(f"  {title}")
        print(f"{'═'*100}")
    hdr = (f"{'':38} {'N':>5} {'Total$':>9} {'Win%':>6} {'Avg$':>7} "
           f"{'MaxDD':>9} {'Sharpe':>7} {'Tgt%':>5} {'Stp%':>5} {'Tm%':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r["n"] == 0:
            continue
        print(f"{r['label']:<38} {r['n']:>5} ${r['total']:>8,.0f} "
              f"{r['win_pct']:>5.1f}% ${r['avg']:>6,.1f} ${r['dd']:>8,.0f} "
              f"{r['sharpe']:>7.3f} {r['targets']:>4.0f}% {r['stops']:>4.0f}% {r['times']:>4.0f}%")


# ── bootstrap CI ─────────────────────────────────────────────────────
def bootstrap_ci(t: pd.DataFrame, n_boot: int = 2000, ci: float = 0.95) -> tuple[float, float]:
    if t.empty:
        return (0.0, 0.0)
    p = t["pnl"].values
    means = [np.mean(np.random.choice(p, size=len(p), replace=True)) for _ in range(n_boot)]
    lo = float(np.percentile(means, (1 - ci) / 2 * 100))
    hi = float(np.percentile(means, (1 + ci) / 2 * 100))
    return (round(lo, 2), round(hi, 2))


# ── sweep ─────────────────────────────────────────────────────────────
def run_sweep(df: pd.DataFrame, contracts: int) -> None:
    targets    = [10, 15, 20, 25, 30, 40, 50]
    stops      = [5,  10, 15, 20, 25, 30]
    min_ranges = [0,  10, 20, 30, 40]

    print("\nRunning parameter sweep (train=2019-2022, test=2023-2026) …", flush=True)
    df_train = df[df.index.year.isin(TRAIN_YEARS)]
    df_test  = df[df.index.year.isin(TEST_YEARS)]

    results = []
    total = len(targets) * len(stops) * len(min_ranges)
    i = 0
    for tgt, stp, mr in product(targets, stops, min_ranges):
        if tgt <= stp:
            continue
        i += 1
        tr = simulate(df_train, tgt, stp, mr, contracts)
        te = simulate(df_test,  tgt, stp, mr, contracts)
        if len(tr) < 10 or len(te) < 5:
            continue
        m_tr = metrics(tr, contracts=contracts)
        m_te = metrics(te, contracts=contracts)
        results.append(dict(
            tgt=tgt, stp=stp, mr=mr,
            rr=round(tgt/stp, 1),
            train_n=m_tr["n"],      train_pnl=m_tr["total"],
            train_sh=m_tr["sharpe"],train_wr=m_tr["win_pct"],
            test_n=m_te["n"],       test_pnl=m_te["total"],
            test_sh=m_te["sharpe"], test_wr=m_te["win_pct"],
            both_pos=(m_tr["total"] > 0 and m_te["total"] > 0),
        ))
        if i % 20 == 0:
            print(f"  {i}/{total} combos …", flush=True)

    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("No qualifying combos found."); return

    # Best by test Sharpe, require both periods positive
    robust = df_res[df_res["both_pos"]].sort_values("test_sh", ascending=False)

    print(f"\n{'═'*105}")
    print(f"  TOP 15 ROBUST CONFIGS  (train AND test both positive) — sorted by test Sharpe")
    print(f"{'═'*105}")
    hdr = (f"{'Tgt':>4} {'Stp':>4} {'R:R':>4} {'MnRng':>6} "
           f"{'Tr.N':>5} {'Tr.PnL':>9} {'Tr.Sh':>7} {'Tr.WR':>6}  "
           f"{'Te.N':>5} {'Te.PnL':>9} {'Te.Sh':>7} {'Te.WR':>6}")
    print(hdr)
    print("-" * len(hdr))
    for _, r in robust.head(15).iterrows():
        print(f"{int(r.tgt):>4} {int(r.stp):>4} {r.rr:>4.1f} {int(r.mr):>6} "
              f"{int(r.train_n):>5} ${r.train_pnl:>8,.0f} {r.train_sh:>7.3f} {r.train_wr:>5.1f}%  "
              f"{int(r.test_n):>5} ${r.test_pnl:>8,.0f} {r.test_sh:>7.3f} {r.test_wr:>5.1f}%")

    # Show single best config in detail
    if not robust.empty:
        best = robust.iloc[0]
        print(f"\n→ Best config: target={int(best.tgt)}t  stop={int(best.stp)}t  min_range={int(best.mr)}t  R:R={best.rr}")
        all_t = simulate(df, int(best.tgt), int(best.stp), int(best.mr), contracts)
        detail(all_t, f"BEST CONFIG full run (target={int(best.tgt)}t stop={int(best.stp)}t mr={int(best.mr)}t)", contracts)


# ── detailed report ───────────────────────────────────────────────────
def detail(t: pd.DataFrame, title: str, contracts: int) -> None:
    if t.empty:
        print("No trades."); return

    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")

    wins  = t[t["pnl"] > 0]
    loses = t[t["pnl"] <= 0]
    total_pnl = t["pnl"].sum()
    years = max((t["date"].max() - t["date"].min()).days / 365.25, 1)

    print(f"  Period      : {t['date'].min().date()} → {t['date'].max().date()}")
    print(f"  Contracts   : {contracts}  (× $1/tick each)")
    print(f"  Trades      : {len(t)}")
    print(f"  Win rate    : {len(wins)/len(t)*100:.1f}%")
    print(f"  Exit mix    : TARGET={len(t[t.reason=='TARGET'])} "
          f"STOP={len(t[t.reason=='STOP'])} TIME={len(t[t.reason=='TIME'])}")
    print(f"  Avg winner  : ${wins['pnl'].mean():,.1f}" if len(wins) else "  No winners")
    print(f"  Avg loser   : ${loses['pnl'].mean():,.1f}" if len(loses) else "  No losers")
    print(f"  R:R realised: {abs(wins['pnl'].mean()/loses['pnl'].mean()):.2f}x" if len(wins) and len(loses) else "")
    print(f"  Total P&L   : ${total_pnl:,.0f}")
    print(f"  Ann. P&L    : ${total_pnl/years:,.0f}/yr")
    cum = t["pnl"].cumsum()
    dd  = float((cum - cum.cummax()).min())
    print(f"  Max DD      : ${dd:,.0f}")
    std = t["pnl"].std()
    sh  = (t["pnl"].mean() / std) * np.sqrt(252) if std > 0 else 0
    print(f"  Sharpe      : {sh:.3f}")

    # Bootstrap CI
    lo, hi = bootstrap_ci(t)
    print(f"  95% CI avg/trade: [${lo:,.2f}, ${hi:,.2f}]  "
          f"{'✅ excludes 0' if lo > 0 else ('⚠️  includes 0' if hi > 0 else '❌ both negative')}")

    # Year by year
    print(f"\n  {'Year':<6} {'N':>5} {'WR':>6} {'P&L':>10} {'Ann P&L':>10} {'MaxDD':>9}")
    print(f"  {'-'*50}")
    for yr, g in t.groupby("year"):
        wr  = (g["pnl"] > 0).mean() * 100
        p   = g["pnl"].sum()
        c   = g["pnl"].cumsum()
        yd  = float((c - c.cummax()).min())
        bar = "█" * max(0, int(p / 200)) if p > 0 else "▓" * max(0, int(-p / 200))
        print(f"  {yr:<6} {len(g):>5} {wr:>5.1f}%  ${p:>8,.0f}             ${yd:>8,.0f}  {bar}")

    # Train vs test summary
    tr = t[t["year"].isin(TRAIN_YEARS)]
    te = t[t["year"].isin(TEST_YEARS)]
    print(f"\n  TRAIN 2019-2022: n={len(tr)}  pnl=${tr['pnl'].sum():,.0f}  "
          f"avg=${tr['pnl'].mean():,.1f}  wr={len(tr[tr.pnl>0])/max(len(tr),1)*100:.1f}%")
    lo_tr, hi_tr = bootstrap_ci(tr)
    print(f"    95% CI: [${lo_tr:,.2f}, ${hi_tr:,.2f}]")
    print(f"  TEST  2023-2026: n={len(te)}  pnl=${te['pnl'].sum():,.0f}  "
          f"avg=${te['pnl'].mean():,.1f}  wr={len(te[te.pnl>0])/max(len(te),1)*100:.1f}%")
    lo_te, hi_te = bootstrap_ci(te)
    print(f"    95% CI: [${lo_te:,.2f}, ${hi_te:,.2f}]")


# ── main ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target",    type=int,   default=20)
    parser.add_argument("--stop",      type=int,   default=10)
    parser.add_argument("--min-range", type=int,   default=20)
    parser.add_argument("--contracts", type=int,   default=10)
    parser.add_argument("--sweep",     action="store_true")
    args = parser.parse_args()

    df = load()

    if args.sweep:
        run_sweep(df, args.contracts)
        return

    # Single-config run
    t = simulate(df, args.target, args.stop, args.min_range, args.contracts)

    # Summary table: stop sweep
    rows = []
    for stp in [5, 8, 10, 15, 20, 25]:
        tgt2 = args.target
        if tgt2 <= stp: continue
        tt = simulate(df, tgt2, stp, args.min_range, args.contracts)
        rows.append(metrics(tt, f"target={tgt2}t  stop={stp}t", args.contracts))
    ptable(rows, f"Stop sweep (target={args.target}t, min_range={args.min_range}t, {args.contracts} contracts)")

    rows2 = []
    for mr in [0, 10, 20, 30, 40, 50]:
        tt = simulate(df, args.target, args.stop, mr, args.contracts)
        rows2.append(metrics(tt, f"min_range={mr}t", args.contracts))
    ptable(rows2, f"Min-range filter sweep (target={args.target}t stop={args.stop}t, {args.contracts} contracts)")

    rows3 = []
    for d_filt in ["both", "long", "short"]:
        tt = t.copy()
        if d_filt == "long":  tt = tt[tt["direction"] == "L"]
        if d_filt == "short": tt = tt[tt["direction"] == "S"]
        rows3.append(metrics(tt, f"direction={d_filt}", args.contracts))
    ptable(rows3, "Long vs Short (fade up vs fade down)")

    detail(t, f"DEFAULT CONFIG (target={args.target}t stop={args.stop}t mr={args.min_range}t {args.contracts}c)", args.contracts)


if __name__ == "__main__":
    main()
