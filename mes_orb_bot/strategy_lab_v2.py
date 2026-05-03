# strategy_lab_v2.py — Revised systematic strategy comparison on MES 1-min data
#
# Key fixes vs v1:
#   • VWAP Reversion: ATR scaling bug fixed (was 1.5× daily ATR → now 0.25× daily ATR)
#   • ORB target multiplier corrected to 3.0 (best from grid search in backtest.py)
#   • day_groups precomputation → ~3× faster
#
# New strategies added:
#   • Gap Continuation  — follow the gap direction instead of fading
#   • First-5-min Pattern — all 5 opening bars in same direction = strong signal
#   • Inside Day Breakout — tight prior day range → explosive breakout
#   • 3-Day Trend Follow  — enter in direction of 3-consecutive-day trend
#   • ORB 30-min + Trend Filter — ORB only in direction of prior day's close
#
# Usage:
#   python strategy_lab_v2.py

import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TZ         = ZoneInfo("America/New_York")
CSV_PATH   = Path(__file__).parent / "data" / "mes_1min.csv"
MULTIPLIER = 5      # MES $/pt
COMMISSION = 4.50   # round-trip, dollars
SLIPPAGE   = 1.00   # 1-tick conservative estimate
TOTAL_COST = COMMISSION + SLIPPAGE   # $5.50 per round-trip

SPLIT_DATE = date(2023, 1, 1)   # walk-forward split


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_data() -> pd.DataFrame:
    print(f"Loading {CSV_PATH} ...", flush=True)
    df = pd.read_csv(CSV_PATH, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df = df.sort_index()
    print(f"  {len(df):,} bars  |  {df.index[0].date()} → {df.index[-1].date()}", flush=True)
    return df


def build_daily_context(df: pd.DataFrame) -> pd.DataFrame:
    """Per-day OHLCV, rolling ATR, multi-day trend flags."""
    rth = df.between_time("09:30", "15:59")
    daily = rth.resample("D").agg(
        open=("open",  "first"),
        high=("high",  "max"),
        low=("low",    "min"),
        close=("close","last"),
        volume=("volume","sum"),
    ).dropna()

    daily["prev_close"]  = daily["close"].shift(1)
    daily["prev2_close"] = daily["close"].shift(2)
    daily["prev3_close"] = daily["close"].shift(3)
    daily["prev_high"]   = daily["high"].shift(1)
    daily["prev_low"]    = daily["low"].shift(1)
    daily["prev_range"]  = daily["prev_high"] - daily["prev_low"]

    # 14-day ATR (EWM)
    tr = pd.concat([
        daily["high"] - daily["low"],
        (daily["high"] - daily["prev_close"]).abs(),
        (daily["low"]  - daily["prev_close"]).abs(),
    ], axis=1).max(axis=1)
    daily["atr14"] = tr.ewm(span=14, adjust=False).mean()

    # 20-day rolling median range (for inside-day detection)
    daily["range_med20"] = daily["prev_range"].rolling(20).median()

    # 3-day consecutive trend flags
    daily["trend_3d_up"]   = (daily["prev_close"]  > daily["prev2_close"]) & \
                             (daily["prev2_close"] > daily["prev3_close"])
    daily["trend_3d_down"] = (daily["prev_close"]  < daily["prev2_close"]) & \
                             (daily["prev2_close"] < daily["prev3_close"])

    # Prev-day direction flag
    daily["prev_day_up"] = daily["prev_close"] > daily["prev2_close"]

    daily.index = daily.index.date
    return daily


def add_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    """bar_min, bar_date, and session VWAP (resets each calendar day)."""
    df = df.copy()
    df["bar_min"]  = df.index.hour * 60 + df.index.minute
    df["bar_date"] = df.index.date

    df["tp"]     = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    df["cum_tp_vol"] = df.groupby("bar_date")["tp_vol"].cumsum()
    df["cum_vol"]    = df.groupby("bar_date")["volume"].cumsum()
    df["vwap"]   = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
    return df


def build_day_groups(df: pd.DataFrame) -> Dict[date, pd.DataFrame]:
    """Pre-index: {date → day_slice}  for O(1) lookup instead of boolean filter."""
    return {d: grp for d, grp in df.groupby("bar_date")}


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    date:        date
    direction:   str
    entry:       float
    stop:        float
    target:      float
    exit_price:  float
    exit_reason: str
    pnl_pts:     float
    pnl_net:     float


@dataclass
class StrategyResult:
    name:        str
    description: str
    trades:      List[Trade] = field(default_factory=list)
    n_trades:    int   = 0
    win_rate:    float = 0.0
    avg_net:     float = 0.0
    total_net:   float = 0.0
    annual_net:  float = 0.0
    max_dd:      float = 0.0
    sharpe:      float = 0.0
    calmar:      float = 0.0


def evaluate(result: StrategyResult, n_days: int) -> StrategyResult:
    t = result.trades
    if not t:
        return result
    pnls = [tr.pnl_net for tr in t]
    result.n_trades  = len(t)
    result.win_rate  = sum(1 for p in pnls if p > 0) / len(pnls)
    result.avg_net   = float(np.mean(pnls))
    result.total_net = sum(pnls)

    years = n_days / 252
    result.annual_net = result.total_net / years if years > 0 else 0

    # Sharpe: fill 0 on no-trade days
    daily_map: Dict[date, float] = {}
    for tr in t:
        daily_map[tr.date] = daily_map.get(tr.date, 0) + tr.pnl_net
    daily_pnls = list(daily_map.values()) + [0.0] * (n_days - len(daily_map))
    std = float(np.std(daily_pnls))
    result.sharpe = (float(np.mean(daily_pnls)) / std * (252 ** 0.5)) if std > 0 else 0

    # Max drawdown
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    result.max_dd = max_dd
    result.calmar = (result.annual_net / max_dd) if max_dd > 0 else 0
    return result


def simulate_exit(
    hi: np.ndarray, lo: np.ndarray, cl: np.ndarray, mn: np.ndarray,
    direction: str, stop: float, target: float,
    hard_close_min: int = 945,
) -> Tuple[float, str]:
    if direction == "LONG":
        stop_hit   = lo <= stop
        target_hit = hi >= target
    else:
        stop_hit   = hi >= stop
        target_hit = lo <= target
    hc_hit  = mn >= hard_close_min
    any_hit = stop_hit | target_hit | hc_hit
    if not np.any(any_hit):
        return float(cl[-1]), "HARD_CLOSE"
    i = int(np.argmax(any_hit))
    if stop_hit[i]:
        return stop, "STOP"
    if target_hit[i]:
        return target, "TARGET"
    return float(cl[i]), "HARD_CLOSE"


def make_trade(d: date, direction: str, entry: float, stop: float,
               target: float, ep: float, er: str) -> Trade:
    pts = (ep - entry) if direction == "LONG" else (entry - ep)
    return Trade(date=d, direction=direction, entry=entry, stop=stop,
                 target=target, exit_price=ep, exit_reason=er,
                 pnl_pts=pts, pnl_net=pts * MULTIPLIER - TOTAL_COST)


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Gap Fade ────────────────────────────────────────────────────────────────

def run_gap_fade(daily_ctx: pd.DataFrame, day_groups: dict,
                 gap_min: float = 0.003, gap_max: float = 0.015) -> List[Trade]:
    """Fade overnight gap back toward prev_close. Hard close 11:00."""
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_close"]) or ctx["prev_close"] <= 0:
            continue
        day = day_groups.get(d)
        if day is None or len(day) < 5:
            continue

        open_price = float(day[day["bar_min"] == 570]["open"].iloc[0]) \
                     if len(day[day["bar_min"] == 570]) else float(day.iloc[0]["open"])
        prev_close = float(ctx["prev_close"])
        gap_pct    = (open_price - prev_close) / prev_close

        if abs(gap_pct) < gap_min or abs(gap_pct) > gap_max:
            continue

        direction = "SHORT" if gap_pct > 0 else "LONG"
        gap_size  = abs(open_price - prev_close)

        entry_bars = day[day["bar_min"] >= 571]
        if len(entry_bars) < 2:
            continue
        entry = float(entry_bars.iloc[0]["open"])
        stop   = (entry + 1.5 * gap_size) if direction == "SHORT" else (entry - 1.5 * gap_size)
        target = prev_close

        tb = entry_bars.iloc[1:]
        tb = tb[tb["bar_min"] <= 660]   # 11:00 hard close
        if len(tb) == 0:
            continue
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 660)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 2. Gap Continuation ────────────────────────────────────────────────────────

def run_gap_continuation(daily_ctx: pd.DataFrame, day_groups: dict,
                         gap_min: float = 0.003, gap_max: float = 0.015,
                         target_mult: float = 1.5) -> List[Trade]:
    """
    Follow the gap direction. Gap-and-go: open > prev_close → LONG.
    Entry 9:31, stop = back to prev_close, target = entry + gap_size × target_mult.
    Hard close 12:00.
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_close"]) or ctx["prev_close"] <= 0:
            continue
        day = day_groups.get(d)
        if day is None or len(day) < 5:
            continue

        open_bar = day[day["bar_min"] == 570]
        open_price = float(open_bar["open"].iloc[0]) if len(open_bar) else float(day.iloc[0]["open"])
        prev_close = float(ctx["prev_close"])
        gap_pct    = (open_price - prev_close) / prev_close

        if abs(gap_pct) < gap_min or abs(gap_pct) > gap_max:
            continue

        direction = "LONG" if gap_pct > 0 else "SHORT"
        gap_size  = abs(open_price - prev_close)

        entry_bars = day[day["bar_min"] >= 571]
        if len(entry_bars) < 2:
            continue
        entry  = float(entry_bars.iloc[0]["open"])
        stop   = prev_close
        target = (entry + target_mult * gap_size) if direction == "LONG" \
                 else (entry - target_mult * gap_size)

        tb = entry_bars.iloc[1:]
        tb = tb[tb["bar_min"] <= 720]   # 12:00 hard close
        if len(tb) == 0:
            continue
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 720)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 3/4/5. Opening Range Breakout (generalised) ───────────────────────────────

def run_orb(daily_ctx: pd.DataFrame, day_groups: dict,
            range_end_min: int = 585,
            target_mult: float = 3.0,
            min_width: float = 5.0,
            max_width: float = 30.0) -> List[Trade]:
    """
    ORB: observe range 9:30–range_end_min, enter on breakout.
    range_end_min:
      575 = 9:35  (5-min range)
      585 = 9:45  (15-min range)
      600 = 10:00 (30-min range)
      630 = 10:30 (60-min range)
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        day = day_groups.get(d)
        if day is None or len(day) < 10:
            continue

        range_bars = day[day["bar_min"] < range_end_min]
        if len(range_bars) < 2:
            continue

        rng_hi = float(range_bars["high"].max())
        rng_lo = float(range_bars["low"].min())
        width  = rng_hi - rng_lo

        if width < min_width or width > max_width:
            continue

        post   = day[day["bar_min"] >= range_end_min]
        cutoff = post[post["bar_min"] <= 930]   # entry cutoff 15:30
        if len(cutoff) < 2:
            continue

        long_  = np.where(cutoff["close"].values > rng_hi)[0]
        short_ = np.where(cutoff["close"].values < rng_lo)[0]
        li     = long_[0]  if len(long_)  else len(cutoff)
        si     = short_[0] if len(short_) else len(cutoff)

        if li == len(cutoff) and si == len(cutoff):
            continue

        direction  = "LONG" if li <= si else "SHORT"
        signal_idx = li if li <= si else si

        remaining = post.iloc[signal_idx + 1:]
        if len(remaining) < 2:
            continue

        entry  = float(remaining.iloc[0]["open"])
        stop   = rng_lo if direction == "LONG" else rng_hi
        target = (entry + target_mult * width) if direction == "LONG" \
                 else (entry - target_mult * width)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 945)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 6. ORB 30-min with Trend Filter ───────────────────────────────────────────

def run_orb_trend_filter(daily_ctx: pd.DataFrame, day_groups: dict,
                         target_mult: float = 3.0,
                         min_width: float = 5.0,
                         max_width: float = 30.0) -> List[Trade]:
    """
    ORB 30-min but only trade in direction of the prior day:
    - prev day was up → only take LONG breakouts
    - prev day was down → only take SHORT breakouts
    Hypothesis: trades aligned with prior-day trend have better follow-through.
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_day_up"]):
            continue
        day = day_groups.get(d)
        if day is None or len(day) < 10:
            continue

        range_bars = day[day["bar_min"] < 600]   # 30-min range
        if len(range_bars) < 3:
            continue

        rng_hi = float(range_bars["high"].max())
        rng_lo = float(range_bars["low"].min())
        width  = rng_hi - rng_lo

        if width < min_width or width > max_width:
            continue

        post   = day[day["bar_min"] >= 600]
        cutoff = post[post["bar_min"] <= 930]
        if len(cutoff) < 2:
            continue

        prev_day_up = bool(ctx["prev_day_up"])

        if prev_day_up:
            sig = np.where(cutoff["close"].values > rng_hi)[0]
            direction = "LONG"
        else:
            sig = np.where(cutoff["close"].values < rng_lo)[0]
            direction = "SHORT"

        if len(sig) == 0:
            continue

        remaining = post.iloc[sig[0] + 1:]
        if len(remaining) < 2:
            continue

        entry  = float(remaining.iloc[0]["open"])
        stop   = rng_lo if direction == "LONG" else rng_hi
        target = (entry + target_mult * width) if direction == "LONG" \
                 else (entry - target_mult * width)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 945)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 7. VWAP Reversion (FIXED) ─────────────────────────────────────────────────

def run_vwap_reversion(daily_ctx: pd.DataFrame, day_groups: dict,
                       dev_atr_mult: float = 0.25,
                       min_dev_pts: float = 4.0,
                       stop_atr_mult: float = 0.15) -> List[Trade]:
    """
    Fade price deviations from session VWAP back toward VWAP.

    FIX vs v1: threshold is now dev_atr_mult × daily_atr (typically 6-15pt)
    instead of 1.5 × daily_atr (which was 45-90pt — basically never fires).

    Additional filter: skip if first 30-min move > 12pt (trending day).
    Window: 10:15–14:00 only (VWAP is meaningful after first 45 min settle).
    Stop: entry ± stop_atr_mult × atr
    Target: session VWAP at entry time
    Hard close: 15:30
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["atr14"]) or ctx["atr14"] <= 0:
            continue
        atr = float(ctx["atr14"])
        day = day_groups.get(d)
        if day is None or len(day) < 20:
            continue

        # Skip trending days: if first 30-min move > 12pt, expect trend not reversion
        first30 = day[day["bar_min"].between(570, 599)]
        if len(first30) >= 2:
            first30_move = abs(float(first30.iloc[-1]["close"]) - float(first30.iloc[0]["open"]))
            if first30_move > 12.0:
                continue

        # Only look during 10:15–14:00
        window = day[day["bar_min"].between(615, 840)]
        if len(window) < 5:
            continue

        threshold = max(min_dev_pts, dev_atr_mult * atr)
        stop_dist = max(2.0, stop_atr_mult * atr)

        for i in range(len(window) - 3):
            row  = window.iloc[i]
            cl   = float(row["close"])
            vwap = float(row["vwap"]) if not pd.isna(row["vwap"]) else 0.0
            if vwap <= 0:
                continue

            dev = cl - vwap
            if abs(dev) < threshold:
                continue

            direction  = "SHORT" if dev > 0 else "LONG"
            remaining  = window.iloc[i + 1:]
            if len(remaining) < 2:
                break

            entry  = float(remaining.iloc[0]["open"])
            stop   = entry + stop_dist if direction == "SHORT" else entry - stop_dist
            target = vwap   # target IS the VWAP level at signal time

            tb = remaining.iloc[1:]
            ep, er = simulate_exit(
                tb["high"].values.astype(float), tb["low"].values.astype(float),
                tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
                direction, stop, target, 930)
            trades.append(make_trade(d, direction, entry, stop, target, ep, er))
            break   # one trade per day

    return trades


# ── 8. Morning Momentum ────────────────────────────────────────────────────────

def run_morning_momentum(daily_ctx: pd.DataFrame, day_groups: dict,
                         lookback_min: int = 30,
                         min_move_pts: float = 8.0,
                         target_mult: float = 1.5) -> List[Trade]:
    """Follow the first 30-min direction if move ≥ min_move_pts."""
    trades = []
    obs_end = 570 + lookback_min
    for d, ctx in daily_ctx.iterrows():
        day = day_groups.get(d)
        if day is None or len(day) < 20:
            continue

        obs  = day[day["bar_min"] < obs_end]
        post = day[day["bar_min"] >= obs_end]
        if len(obs) < 5 or len(post) < 3:
            continue

        move = float(obs.iloc[-1]["close"]) - float(obs.iloc[0]["open"])
        if abs(move) < min_move_pts:
            continue

        direction = "LONG" if move > 0 else "SHORT"
        entry     = float(post.iloc[0]["open"])
        stop      = float(obs["low"].min())  if direction == "LONG" \
                    else float(obs["high"].max())
        target    = (entry + target_mult * abs(move)) if direction == "LONG" \
                    else (entry - target_mult * abs(move))

        tb = post.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 945)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 9. First-5-min Pattern ────────────────────────────────────────────────────

def run_first_5min_pattern(daily_ctx: pd.DataFrame, day_groups: dict,
                            min_move_pts: float = 4.0,
                            target_mult: float = 2.0) -> List[Trade]:
    """
    All 5 opening bars (9:30-9:34) close sequentially in same direction
    AND net move ≥ min_move_pts → strong early signal.
    Entry: 9:35 open.
    Stop:  Low (LONG) or High (SHORT) of 5-bar window.
    Target: entry ± target_mult × bar-window range.
    Hard close: 11:00.
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        day = day_groups.get(d)
        if day is None or len(day) < 20:
            continue

        first5 = day[day["bar_min"].between(570, 574)]
        if len(first5) < 4:
            continue

        closes = first5["close"].values.astype(float)
        # Check monotonic direction
        diffs  = np.diff(closes)
        all_up   = bool(np.all(diffs > 0))
        all_down = bool(np.all(diffs < 0))
        if not all_up and not all_down:
            continue

        net_move = abs(closes[-1] - closes[0])
        if net_move < min_move_pts:
            continue

        direction = "LONG" if all_up else "SHORT"
        bar_hi = float(first5["high"].max())
        bar_lo = float(first5["low"].min())

        post = day[day["bar_min"] >= 575]
        if len(post) < 3:
            continue

        entry  = float(post.iloc[0]["open"])
        stop   = bar_lo if direction == "LONG" else bar_hi
        target = (entry + target_mult * net_move) if direction == "LONG" \
                 else (entry - target_mult * net_move)

        tb = post.iloc[1:]
        tb_hc = tb[tb["bar_min"] <= 660]   # 11:00 hard close
        if len(tb_hc) == 0:
            continue
        ep, er = simulate_exit(
            tb_hc["high"].values.astype(float), tb_hc["low"].values.astype(float),
            tb_hc["close"].values.astype(float), tb_hc["bar_min"].values.astype(float),
            direction, stop, target, 660)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 10. Inside Day Breakout ───────────────────────────────────────────────────

def run_inside_day_breakout(daily_ctx: pd.DataFrame, day_groups: dict,
                             range_pct_threshold: float = 0.6,
                             target_mult: float = 1.5) -> List[Trade]:
    """
    Inside Day: today's prior-day range is below range_pct_threshold × 20-day median range.
    On breakout of prev_high or prev_low, enter in breakout direction.
    Logic: tight consolidation precedes expansion.
    Entry: next bar open after signal.
    Stop:  midpoint of prior-day range.
    Target: entry ± target_mult × prev_range.
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_range"]) or pd.isna(ctx["range_med20"]):
            continue
        # Only trade when prior day was unusually tight (inside/narrow day)
        if ctx["prev_range"] > range_pct_threshold * ctx["range_med20"]:
            continue

        day = day_groups.get(d)
        if day is None or len(day) < 10:
            continue

        ph = float(ctx["prev_high"])
        pl = float(ctx["prev_low"])
        pr = float(ctx["prev_range"])
        if pr <= 0:
            continue
        mid = (ph + pl) / 2.0

        # Signal: first close above prev_high or below prev_low (after 9:45)
        post   = day[day["bar_min"] >= 585]
        cutoff = post[post["bar_min"] <= 930]
        if len(cutoff) < 2:
            continue

        long_  = np.where(cutoff["close"].values > ph)[0]
        short_ = np.where(cutoff["close"].values < pl)[0]
        li     = long_[0]  if len(long_)  else len(cutoff)
        si     = short_[0] if len(short_) else len(cutoff)

        if li == len(cutoff) and si == len(cutoff):
            continue

        direction  = "LONG" if li <= si else "SHORT"
        signal_idx = li if li <= si else si

        remaining = post.iloc[signal_idx + 1:]
        if len(remaining) < 2:
            continue

        entry  = float(remaining.iloc[0]["open"])
        stop   = mid
        target = (entry + target_mult * pr) if direction == "LONG" \
                 else (entry - target_mult * pr)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 945)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 11. 3-Day Trend Follow ────────────────────────────────────────────────────

def run_3day_trend(daily_ctx: pd.DataFrame, day_groups: dict,
                   target_mult: float = 1.5,
                   stop_atr_mult: float = 0.5) -> List[Trade]:
    """
    If 3 consecutive prior days all closed in the same direction,
    enter in that direction at 9:31.
    Stop:   entry ± stop_atr_mult × ATR (daily ATR as risk unit).
    Target: entry ± target_mult × ATR.
    Hard close: 15:30 (shorter day to avoid holding against reversals).
    """
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["trend_3d_up"]) or pd.isna(ctx["atr14"]):
            continue
        if not ctx["trend_3d_up"] and not ctx["trend_3d_down"]:
            continue

        day = day_groups.get(d)
        if day is None or len(day) < 10:
            continue

        direction = "LONG" if ctx["trend_3d_up"] else "SHORT"
        atr       = float(ctx["atr14"])

        entry_bars = day[day["bar_min"] >= 571]   # 9:31
        if len(entry_bars) < 2:
            continue
        entry  = float(entry_bars.iloc[0]["open"])
        stop   = entry - stop_atr_mult * atr if direction == "LONG" \
                 else entry + stop_atr_mult * atr
        target = entry + target_mult * atr if direction == "LONG" \
                 else entry - target_mult * atr

        tb = entry_bars.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 930)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ── 12. Prev-Day High/Low Breakout ────────────────────────────────────────────

def run_prev_day_breakout(daily_ctx: pd.DataFrame, day_groups: dict,
                          target_mult: float = 1.5,
                          min_range: float = 15.0) -> List[Trade]:
    """Break above prev_day_high or below prev_day_low → momentum trade."""
    trades = []
    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_high"]) or ctx["prev_range"] < min_range:
            continue
        day = day_groups.get(d)
        if day is None or len(day) < 10:
            continue

        ph, pl = float(ctx["prev_high"]), float(ctx["prev_low"])
        pr     = float(ctx["prev_range"])
        mid    = (ph + pl) / 2.0

        post   = day[day["bar_min"] >= 585]
        cutoff = post[post["bar_min"] <= 930]
        if len(cutoff) < 2:
            continue

        long_  = np.where(cutoff["close"].values > ph)[0]
        short_ = np.where(cutoff["close"].values < pl)[0]
        li     = long_[0]  if len(long_)  else len(cutoff)
        si     = short_[0] if len(short_) else len(cutoff)

        if li == len(cutoff) and si == len(cutoff):
            continue

        direction  = "LONG" if li <= si else "SHORT"
        signal_idx = li if li <= si else si
        remaining  = post.iloc[signal_idx + 1:]
        if len(remaining) < 2:
            continue

        entry  = float(remaining.iloc[0]["open"])
        stop   = mid
        target = (entry + target_mult * pr * 0.5) if direction == "LONG" \
                 else (entry - target_mult * pr * 0.5)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(float), tb["low"].values.astype(float),
            tb["close"].values.astype(float), tb["bar_min"].values.astype(float),
            direction, stop, target, 945)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL + EVALUATE
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_DEFS = [
    ("Gap Fade",
     "Fade 0.3-1.5% overnight gaps back toward prev close (exit 11:00)",
     lambda dc, dg: run_gap_fade(dc, dg)),

    ("Gap Continuation",
     "Follow 0.3-1.5% overnight gaps — gap-and-go (exit 12:00)",
     lambda dc, dg: run_gap_continuation(dc, dg)),

    ("ORB 15-min  tgt×3",
     "15-min range, tgt×3.0, width 5-30pt  [best params from grid search]",
     lambda dc, dg: run_orb(dc, dg, 585, 3.0, 5.0, 30.0)),

    ("ORB 30-min  tgt×3",
     "30-min range (9:30-10:00), tgt×3.0, width 5-30pt",
     lambda dc, dg: run_orb(dc, dg, 600, 3.0, 5.0, 30.0)),

    ("ORB 60-min  tgt×3",
     "60-min range (9:30-10:30), tgt×3.0, width 8-40pt",
     lambda dc, dg: run_orb(dc, dg, 630, 3.0, 8.0, 40.0)),

    ("ORB 30+Trend",
     "ORB 30-min only in prior-day's direction (trend-filtered)",
     lambda dc, dg: run_orb_trend_filter(dc, dg, 3.0, 5.0, 30.0)),

    ("VWAP Reversion",
     "Fade 0.25×ATR deviation from VWAP back to VWAP (FIXED — was 1.5×ATR)",
     lambda dc, dg: run_vwap_reversion(dc, dg, 0.25, 4.0, 0.15)),

    ("Morning Momentum",
     "Follow first 30-min direction if move ≥ 8pts, tgt×1.5",
     lambda dc, dg: run_morning_momentum(dc, dg, 30, 8.0, 1.5)),

    ("First-5-min Pattern",
     "All 5 opening bars monotone + move ≥ 4pt → enter at 9:35",
     lambda dc, dg: run_first_5min_pattern(dc, dg, 4.0, 2.0)),

    ("Inside Day Breakout",
     "Prior day range < 60% of 20-day median → breakout setup",
     lambda dc, dg: run_inside_day_breakout(dc, dg, 0.6, 1.5)),

    ("3-Day Trend",
     "3 consecutive prior days same direction → enter at 9:31",
     lambda dc, dg: run_3day_trend(dc, dg, 1.5, 0.5)),

    ("Prev Day Breakout",
     "Break above/below prior day H/L → momentum, tgt×1.5",
     lambda dc, dg: run_prev_day_breakout(dc, dg, 1.5, 15.0)),
]


def run_all(daily_ctx: pd.DataFrame, day_groups: dict) -> List[tuple]:
    trading_days = sorted(d for d in daily_ctx.index if isinstance(d, date))
    total_days   = len(trading_days)
    train_days   = sum(1 for d in trading_days if d < SPLIT_DATE)
    test_days    = total_days - train_days

    results = []
    for name, desc, fn in STRATEGY_DEFS:
        print(f"  {name} ...", flush=True)
        trades = fn(daily_ctx, day_groups)

        full  = StrategyResult(name=name, description=desc, trades=trades)
        train = StrategyResult(name=name, description=desc,
                               trades=[t for t in trades if t.date < SPLIT_DATE])
        test  = StrategyResult(name=name, description=desc,
                               trades=[t for t in trades if t.date >= SPLIT_DATE])

        evaluate(full,  total_days)
        evaluate(train, train_days)
        evaluate(test,  test_days)
        results.append((full, train, test))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# PER-YEAR BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def yearly_pnl(trades: List[Trade]) -> Dict[int, float]:
    by_year: Dict[int, float] = {}
    for t in trades:
        yr = t.date.year
        by_year[yr] = by_year.get(yr, 0) + t.pnl_net
    return by_year


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_full_table(results: List[tuple]) -> None:
    SEP = "─" * 104
    print(f"\n{'═'*104}")
    print("  FULL PERIOD  (2019-05-06 → 2026-04-24)  |  $5.50 cost/trade  ($4.50 commission + $1.00 slippage)")
    print(f"{'═'*104}")
    hdr = (f"{'Strategy':<26} {'Trades':>7} {'Win%':>6} {'Avg$':>7} "
           f"{'Annual$':>9} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}")
    print(hdr)
    print(SEP)

    for full, train, test in sorted(results, key=lambda x: x[0].sharpe, reverse=True):
        r = full
        flag = "✅" if r.sharpe > 0.4 and r.avg_net > 3 else \
               ("⚠️ " if r.sharpe > 0.15 and r.avg_net > 0 else "❌")
        print(f"{flag} {r.name:<24} {r.n_trades:>7} {r.win_rate:>6.1%} "
              f"{r.avg_net:>+7.2f} {r.annual_net:>+9.0f} "
              f"{r.max_dd:>8.0f} {r.sharpe:>7.2f} {r.calmar:>7.2f}")


def print_walkforward_table(results: List[tuple]) -> None:
    SEP = "─" * 104
    print(f"\n{'═'*104}")
    print("  WALK-FORWARD  |  TRAIN: 2019-2022  vs  TEST: 2023-2026")
    print("  Good = OOS Sharpe > 0.3, positive avg$, no significant decay from IS.")
    print(f"{'═'*104}")
    hdr = (f"{'Strategy':<26} {'TR sharpe':>10} {'TS sharpe':>10} "
           f"{'TR net$':>9} {'TS net$':>9} {'TR ann$':>9} {'TS ann$':>9} {'Holds?':>8}")
    print(hdr)
    print(SEP)

    for full, train, test in sorted(results, key=lambda x: x[2].sharpe, reverse=True):
        holds = test.sharpe > 0.3 and test.avg_net > 0
        stable = test.sharpe >= train.sharpe * 0.4   # OOS ≥ 40% of IS
        flag  = "✅" if holds and stable else \
                ("⚠️ " if holds else "❌")
        print(f"{flag} {full.name:<24} "
              f"{train.sharpe:>10.2f} {test.sharpe:>10.2f} "
              f"{train.total_net:>+9.0f} {test.total_net:>+9.0f} "
              f"{train.annual_net:>+9.0f} {test.annual_net:>+9.0f}  "
              f"{'YES' if holds else 'NO':>6}")
    print(SEP)


def print_yearly_breakdown(results: List[tuple]) -> None:
    print(f"\n{'═'*104}")
    print("  YEAR-BY-YEAR P&L  (all strategies, $)")
    print(f"{'═'*104}")

    years = list(range(2019, 2027))
    header = f"{'Strategy':<26}" + "".join(f"{y:>9}" for y in years) + f"{'TOTAL':>9}"
    print(header)
    print("─" * 104)

    # Sort by total OOS net
    for full, train, test in sorted(results, key=lambda x: x[2].total_net, reverse=True):
        by_yr = yearly_pnl(full.trades)
        row   = f"{full.name:<26}"
        for y in years:
            v = by_yr.get(y, 0)
            row += f"{v:>+9.0f}"
        row += f"{full.total_net:>+9.0f}"
        print(row)


def print_best_detail(results: List[tuple]) -> None:
    """Detailed breakdown for the best OOS strategy."""
    best = max(results, key=lambda x: x[2].sharpe)
    full, train, test = best

    print(f"\n{'═'*70}")
    print(f"  BEST OUT-OF-SAMPLE: {full.name}")
    print(f"  {full.description}")
    print(f"{'═'*70}")

    for label, r in [("TRAIN (2019-2022)", train), ("TEST  (2023-2026)", test)]:
        if r.n_trades == 0:
            print(f"\n  {label}  — no trades")
            continue
        print(f"\n  {label}")
        print(f"    Trades   : {r.n_trades}  |  Win rate: {r.win_rate:.1%}  |  "
              f"Avg: ${r.avg_net:+.2f}/trade")
        print(f"    Total    : ${r.total_net:+,.0f}  |  Annual: ${r.annual_net:+,.0f}")
        print(f"    Max DD   : ${r.max_dd:,.0f}  |  Sharpe: {r.sharpe:.2f}  |  "
              f"Calmar: {r.calmar:.2f}")
        reasons: Dict[str, list] = {}
        for t in r.trades:
            reasons.setdefault(t.exit_reason, []).append(t.pnl_net)
        print(f"    Exit mix :")
        for k, pnls in sorted(reasons.items(), key=lambda x: -len(x[1])):
            print(f"      {k:>12}: {len(pnls):4} trades | "
                  f"avg ${np.mean(pnls):>+7.2f} | total ${sum(pnls):>+8,.0f}")

    print(f"\n{'═'*70}")
    print(f"  $20K ACCOUNT PROJECTION (2 contracts, out-of-sample):")
    ann2 = test.annual_net * 2
    dd2  = test.max_dd * 2
    print(f"    Annual return : ${ann2:>+,.0f}  ({ann2/20000*100:+.1f}% of $20K)")
    print(f"    Max drawdown  : ${dd2:>,.0f}  ({dd2/20000*100:.1f}% of $20K)")
    print(f"    Sharpe        : {test.sharpe:.2f}")
    print(f"{'═'*70}\n")


def print_synthesis(results: List[tuple]) -> None:
    """High-level strategic synthesis and recommendations."""
    print(f"\n{'═'*70}")
    print("  QUANT SYNTHESIS — KEY FINDINGS")
    print(f"{'═'*70}")

    # Top 3 by OOS Sharpe
    sorted_oos = sorted(results, key=lambda x: x[2].sharpe, reverse=True)
    print("\n  Top 3 by OOS Sharpe:")
    for i, (full, train, test) in enumerate(sorted_oos[:3], 1):
        print(f"    {i}. {full.name:<26} | OOS Sharpe={test.sharpe:.2f} | "
              f"OOS avg=${test.avg_net:+.2f}/trade | {test.n_trades} trades")

    # Avg profit per trade perspective
    print("\n  Cost context: $5.50/trade. To be viable, avg profit must beat this.")
    print("  At 2 contracts with 250 trade-days/yr:")
    for full, train, test in sorted_oos[:5]:
        annual_2c = test.annual_net * 2
        print(f"    {full.name:<26}: ${annual_2c:>+,.0f}/yr at 2 contracts")

    print(f"\n{'═'*70}")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    import time
    t0 = time.time()

    df = load_data()
    print("Building daily context...", flush=True)
    daily_ctx = build_daily_context(df)
    print("Adding intraday features (VWAP)...", flush=True)
    df = add_intraday_features(df)
    print("Pre-indexing day groups...", flush=True)
    day_groups = build_day_groups(df)

    print(f"\nRunning {len(STRATEGY_DEFS)} strategies...\n", flush=True)
    results = run_all(daily_ctx, day_groups)
    print(f"\nDone in {time.time()-t0:.1f}s\n", flush=True)

    print_full_table(results)
    print_walkforward_table(results)
    print_yearly_breakdown(results)
    print_best_detail(results)
    print_synthesis(results)


if __name__ == "__main__":
    main()
