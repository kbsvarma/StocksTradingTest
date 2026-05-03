# backtest.py — Vectorized ORB backtest + parameter grid search.
#
# Runs 125 parameter combos × 1,771 days in ~30 seconds by:
#   1. Pre-computing all per-day arrays once (not 125×)
#   2. Using numpy vectorized ops for signal detection + exit simulation
#
# Usage:
#   python backtest.py              # uses data/mes_1min.csv if present
#   python backtest.py --top 10     # show top-N configs
#   python backtest.py --best       # show day-by-day for best config

import argparse
import itertools
import statistics
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

TZ          = ZoneInfo("America/New_York")
MULTIPLIER  = 5       # MES dollars per point
COMMISSION  = 4.50    # round-trip per contract
CSV_PATH    = Path(__file__).parent / "data" / "mes_1min.csv"

ENTRY_CUTOFF_MIN = 15 * 60 + 30   # 15:30
HARD_CLOSE_MIN   = 15 * 60 + 45   # 15:45

PARAM_GRID = {
    "target_mult": [1.0, 1.5, 2.0, 2.5, 3.0],
    "min_width":   [1.0, 2.0, 3.0, 4.0, 5.0],
    "max_width":   [10.0, 15.0, 20.0, 25.0, 30.0],
}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_csv() -> pd.DataFrame:
    print(f"Loading {CSV_PATH}...", flush=True)
    df = pd.read_csv(CSV_PATH, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    print(f"  {len(df):,} bars", flush=True)
    return df


def fetch_yfinance(days: int = 29) -> pd.DataFrame:
    print(f"Fetching ES=F 1-min ({days}d)...", flush=True)
    end, pieces = datetime.now(timezone.utc), []
    cursor = end
    remaining = days
    while remaining > 0:
        pull  = min(7, remaining)
        start = cursor - timedelta(days=pull)
        raw   = yf.download("ES=F", start=start.strftime("%Y-%m-%d"),
                            end=cursor.strftime("%Y-%m-%d"),
                            interval="1m", progress=False)
        if not raw.empty:
            pieces.append(raw)
        cursor, remaining = start, remaining - pull
        time.sleep(0.4)
    if not pieces:
        raise RuntimeError("yfinance returned no data")
    df = pd.concat(pieces[::-1]).sort_index()
    df = df[~df.index.duplicated()]
    df.columns = [c[0].lower() for c in df.columns]
    df.index   = df.index.tz_convert(TZ)
    return df


# ── Pre-computation (runs once, shared across all parameter combos) ────────────

@dataclass
class DayCache:
    """All arrays needed to simulate a single trading day."""
    date:       date
    rng_high:   float
    rng_low:    float
    width:      float
    skip:       str        # reason string if day is not tradable, else ""
    # Arrays below are populated only when skip == ""
    times:      np.ndarray = field(default_factory=lambda: np.array([]))  # bar minutes from midnight
    opens:      np.ndarray = field(default_factory=lambda: np.array([]))
    highs:      np.ndarray = field(default_factory=lambda: np.array([]))
    lows:       np.ndarray = field(default_factory=lambda: np.array([]))
    closes:     np.ndarray = field(default_factory=lambda: np.array([]))


def precompute(df: pd.DataFrame) -> List[DayCache]:
    """
    Build per-day caches from the full DataFrame.
    Only called once regardless of how many param combos we test.
    """
    rth  = df.between_time("09:30", "15:59").copy()
    rth["_min"] = rth.index.hour * 60 + rth.index.minute
    rth["_date"] = rth.index.date

    days   = sorted(set(rth["_date"]))
    caches = []

    for d in days:
        day = rth[rth["_date"] == d]
        if day.empty:
            continue

        # Opening range 09:30–09:44
        range_bars = day[day["_min"] <= 9 * 60 + 44]
        if len(range_bars) < 5:
            caches.append(DayCache(date=d, rng_high=0, rng_low=0,
                                   width=0, skip="THIN_RANGE"))
            continue

        rng_high = float(range_bars["high"].max())
        rng_low  = float(range_bars["low"].min())
        width    = round(rng_high - rng_low, 4)

        # Post-range bars 09:45 onwards (signal + trade management)
        post = day[day["_min"] >= 9 * 60 + 45]
        if post.empty:
            caches.append(DayCache(date=d, rng_high=rng_high, rng_low=rng_low,
                                   width=width, skip="NO_POST_BARS"))
            continue

        caches.append(DayCache(
            date=d,
            rng_high=rng_high,
            rng_low=rng_low,
            width=width,
            skip="",
            times=post["_min"].values.astype(np.float32),
            opens=post["open"].values.astype(np.float32),
            highs=post["high"].values.astype(np.float32),
            lows=post["low"].values.astype(np.float32),
            closes=post["close"].values.astype(np.float32),
        ))

    print(f"  Pre-computed {len(caches)} days.", flush=True)
    return caches


# ── Single-day simulation (vectorized) ────────────────────────────────────────

@dataclass
class DayResult:
    date:       date
    skip:       str
    rng_high:   float = 0.0
    rng_low:    float = 0.0
    width:      float = 0.0
    direction:  Optional[str]   = None
    entry:      Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl_pts:    Optional[float] = None
    pnl_net:    Optional[float] = None


def simulate(cache: DayCache, target_mult: float,
             min_width: float, max_width: float) -> DayResult:

    base = DayResult(date=cache.date, skip=cache.skip,
                     rng_high=cache.rng_high, rng_low=cache.rng_low,
                     width=cache.width)

    if cache.skip:
        return base

    if cache.width < min_width or cache.width > max_width:
        base.skip = "WIDTH_FILTER"
        return base

    times  = cache.times
    closes = cache.closes
    opens  = cache.opens
    highs  = cache.highs
    lows   = cache.lows

    # ── Signal: first bar within cutoff where close breaks range ──────────────
    cutoff_mask = times <= ENTRY_CUTOFF_MIN
    c = closes[cutoff_mask]

    long_hits  = np.where(c > cache.rng_high)[0]
    short_hits = np.where(c < cache.rng_low)[0]

    has_long  = len(long_hits)  > 0
    has_short = len(short_hits) > 0

    if not has_long and not has_short:
        base.skip = "NO_SIGNAL"
        return base

    li = long_hits[0]  if has_long  else len(c)
    si = short_hits[0] if has_short else len(c)
    direction  = "LONG" if li <= si else "SHORT"
    signal_idx = li     if li <= si else si

    # ── Entry: open of the bar after signal ───────────────────────────────────
    entry_idx = signal_idx + 1
    if entry_idx >= len(opens):
        base.skip = "NO_ENTRY_BAR"
        return base

    entry = float(opens[entry_idx])

    if direction == "LONG":
        stop   = cache.rng_low
        target = entry + target_mult * cache.width
    else:
        stop   = cache.rng_high
        target = entry - target_mult * cache.width

    # ── Vectorized exit simulation ────────────────────────────────────────────
    t_lo    = lows[entry_idx:]
    t_hi    = highs[entry_idx:]
    t_cl    = closes[entry_idx:]
    t_times = times[entry_idx:]

    if direction == "LONG":
        stop_hit   = t_lo <= stop
        target_hit = t_hi >= target
    else:
        stop_hit   = t_hi >= stop
        target_hit = t_lo <= target

    hc_hit  = t_times >= HARD_CLOSE_MIN
    any_hit = stop_hit | target_hit | hc_hit

    if not np.any(any_hit):
        exit_price  = float(t_cl[-1]) if len(t_cl) else entry
        exit_reason = "HARD_CLOSE"
    else:
        i = int(np.argmax(any_hit))
        if stop_hit[i]:              # stop wins on same-bar conflict
            exit_price, exit_reason = stop,            "STOP"
        elif target_hit[i]:
            exit_price, exit_reason = target,          "TARGET"
        else:
            exit_price, exit_reason = float(t_cl[i]),  "HARD_CLOSE"

    pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    pnl_net = pnl_pts * MULTIPLIER - COMMISSION

    return DayResult(
        date=cache.date, skip="",
        rng_high=cache.rng_high, rng_low=cache.rng_low, width=cache.width,
        direction=direction, entry=entry,
        exit_price=exit_price, exit_reason=exit_reason,
        pnl_pts=pnl_pts, pnl_net=pnl_net,
    )


# ── Backtest summary ───────────────────────────────────────────────────────────

@dataclass
class BacktestSummary:
    params:          dict
    days_total:      int
    days_traded:     int
    wins:            int
    losses:          int
    win_rate:        float
    total_pts:       float
    total_net:       float
    avg_net_per_trade: float
    avg_net_per_day:   float
    max_drawdown:    float
    sharpe:          float
    day_results:     List[DayResult] = field(default_factory=list)


def summarise(caches: List[DayCache], day_results: List[DayResult],
              params: dict) -> BacktestSummary:
    trades = [r for r in day_results if r.pnl_net is not None]
    wins   = [r for r in trades if r.pnl_net > 0]

    total_net = sum(r.pnl_net for r in trades)
    total_pts = sum(r.pnl_pts for r in trades)

    peak = max_dd = running = 0.0
    for r in trades:
        running += r.pnl_net
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd

    daily = [r.pnl_net if r.pnl_net is not None else 0.0 for r in day_results]
    std   = statistics.pstdev(daily)
    sharpe = (statistics.mean(daily) / std * (252 ** 0.5)) if std > 0 else 0.0

    return BacktestSummary(
        params=params,
        days_total=len(caches),
        days_traded=len(trades),
        wins=len(wins),
        losses=len(trades) - len(wins),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        total_pts=total_pts,
        total_net=total_net,
        avg_net_per_trade=total_net / len(trades) if trades else 0.0,
        avg_net_per_day=total_net / len(caches) if caches else 0.0,
        max_drawdown=max_dd,
        sharpe=sharpe,
        day_results=day_results,
    )


# ── Grid search ────────────────────────────────────────────────────────────────

def grid_search(caches: List[DayCache]) -> List[BacktestSummary]:
    combos = [
        (tm, mnw, mxw)
        for tm, mnw, mxw in itertools.product(
            PARAM_GRID["target_mult"],
            PARAM_GRID["min_width"],
            PARAM_GRID["max_width"],
        )
        if mnw < mxw
    ]
    print(f"Grid search: {len(combos)} combos × {len(caches)} days...", flush=True)
    t0 = time.time()

    results = []
    for tm, mnw, mxw in combos:
        day_results = [simulate(c, tm, mnw, mxw) for c in caches]
        s = summarise(caches, day_results, dict(target_mult=tm, min_width=mnw, max_width=mxw))
        if s.days_traded >= 10:
            results.append(s)

    results.sort(key=lambda r: r.total_net, reverse=True)
    print(f"  Done in {time.time()-t0:.1f}s  ({len(results)} valid configs)", flush=True)
    return results


# ── Display ────────────────────────────────────────────────────────────────────

HDR = (f"{'tgt×':>5} {'minW':>5} {'maxW':>5} {'trades':>7} "
       f"{'win%':>6} {'pts':>8} {'net$':>9} {'$/trade':>8} "
       f"{'$/day':>7} {'maxDD':>8} {'sharpe':>7}")
SEP = "─" * 82


def print_grid(results: List[BacktestSummary], top: int = 20) -> None:
    print(f"\n{SEP}\nTOP {top} CONFIGURATIONS  (sorted by net $)\n{SEP}")
    print(HDR)
    print(SEP)
    for r in results[:top]:
        p = r.params
        print(f"{p['target_mult']:>5.1f} {p['min_width']:>5.1f} {p['max_width']:>5.1f} "
              f"{r.days_traded:>7} {r.win_rate:>6.1%} {r.total_pts:>8.1f} "
              f"{r.total_net:>9.2f} {r.avg_net_per_trade:>8.2f} "
              f"{r.avg_net_per_day:>7.2f} {r.max_drawdown:>8.2f} {r.sharpe:>7.2f}")
    print(SEP)


def print_day_by_day(r: BacktestSummary) -> None:
    p = r.params
    print(f"\n{SEP}")
    print(f"DAY-BY-DAY  tgt×={p['target_mult']}  minW={p['min_width']}  maxW={p['max_width']}")
    print(SEP)
    print(f"{'Date':>12} {'Dir':>6} {'Width':>6} {'Entry':>8} "
          f"{'Exit':>8} {'Reason':>11} {'Pts':>7} {'Net$':>8}  {'Cum$':>9}")
    print(SEP)
    cum = 0.0
    for dr in r.day_results:
        if dr.pnl_net is not None:
            cum += dr.pnl_net
            print(f"{str(dr.date):>12} {dr.direction:>6} {dr.width:>6.2f} "
                  f"{dr.entry:>8.2f} {dr.exit_price:>8.2f} {dr.exit_reason:>11} "
                  f"{dr.pnl_pts:>+7.2f} {dr.pnl_net:>+8.2f}  {cum:>+9.2f}")
        elif dr.skip:
            print(f"{str(dr.date):>12} {'—':>6} {dr.width:>6.2f}  "
                  f"{'—':>8}  {'—':>8} {dr.skip:>11}")
    print(SEP)
    print(f"  Traded {r.days_traded}/{r.days_total} days | "
          f"Wins {r.wins} / Losses {r.losses} | Win {r.win_rate:.1%} | "
          f"Net ${r.total_net:+.2f} | MaxDD ${r.max_drawdown:.2f} | "
          f"Sharpe {r.sharpe:.2f}")


def print_exit_breakdown(r: BacktestSummary) -> None:
    counts: Dict[str, int]   = {}
    net_by: Dict[str, float] = {}
    for dr in r.day_results:
        key = dr.exit_reason if dr.exit_reason else dr.skip
        counts[key] = counts.get(key, 0) + 1
        if dr.pnl_net is not None:
            net_by[key] = net_by.get(key, 0.0) + dr.pnl_net
    print(f"\n{'─'*52}\nEXIT BREAKDOWN (best config)\n{'─'*52}")
    print(f"{'Reason':>15} {'Days':>7} {'Net$':>10}  {'$/day':>8}")
    print("─" * 52)
    for k, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        net = net_by.get(k, 0.0)
        print(f"{k:>15} {cnt:>7} {net:>+10.2f}  {net/cnt:>+8.2f}")


def print_recommendation(best: BacktestSummary) -> None:
    p = best.params
    print(f"""
{'═'*62}
  RECOMMENDED CONFIG  ({best.days_total} trading days, {best.days_traded} trades)
{'═'*62}
  TARGET_MULTIPLIER        = {p['target_mult']}
  MIN_OPENING_RANGE_POINTS = {p['min_width']}
  MAX_OPENING_RANGE_POINTS = {p['max_width']}

  Net P&L    : ${best.total_net:>+10.2f}  over {best.days_traded} trades
  Win rate   : {best.win_rate:.1%}
  Avg/trade  : ${best.avg_net_per_trade:>+8.2f}
  Avg/day    : ${best.avg_net_per_day:>+8.2f}
  Max DD     : ${best.max_drawdown:>8.2f}
  Sharpe     : {best.sharpe:.2f}
{'═'*62}
  Apply in config.py:
    TARGET_MULTIPLIER        = {p['target_mult']}
    MIN_OPENING_RANGE_POINTS = {p['min_width']}
    MAX_OPENING_RANGE_POINTS = {p['max_width']}
""")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MES ORB Vectorized Backtest")
    parser.add_argument("--top",  type=int, default=20)
    parser.add_argument("--best", action="store_true",
                        help="Print day-by-day for best config")
    parser.add_argument("--days", type=int, default=29,
                        help="Days for yfinance fallback")
    args = parser.parse_args()

    df = load_csv() if CSV_PATH.exists() else fetch_yfinance(args.days)

    t0     = time.time()
    caches = precompute(df)
    print(f"  Pre-computation: {time.time()-t0:.1f}s", flush=True)

    days = sorted(set(c.date for c in caches))
    print(f"  Trading days: {len(days)}  ({days[0]} → {days[-1]})\n")

    results = grid_search(caches)

    if not results:
        print("No valid configurations — try wider param grid or more data.")
        return

    print_grid(results, top=args.top)

    if args.best:
        print_day_by_day(results[0])

    print_exit_breakdown(results[0])
    print_recommendation(results[0])


if __name__ == "__main__":
    main()
