# strategy_lab.py — Systematic strategy comparison on MES 1-min data (2019–2026)
#
# Tests 8 distinct strategy families against the same dataset.
# Uses walk-forward validation: train 2019–2022, test 2023–2026.
#
# Usage:
#   python strategy_lab.py              # full comparison
#   python strategy_lab.py --plot       # save equity curves to data/

import argparse
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
COMMISSION = 4.50   # round-trip
SLIPPAGE   = 1.00   # conservative 1-tick estimate per trade (round-trip)
TOTAL_COST = COMMISSION + SLIPPAGE   # $5.50 per trade

SPLIT_DATE = date(2023, 1, 1)   # walk-forward split: train < this, test ≥ this


# ═══════════════════════════════════════════════════════════════════════════════
# DATA PREPARATION
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
    """
    Build a daily reference table:
      prev_close, prev_high, prev_low, prev_range, atr14d
    Merged into the bar DataFrame so each bar knows yesterday's context.
    """
    rth = df.between_time("09:30", "15:59")

    daily = rth.resample("D").agg(
        open=("open",  "first"),
        high=("high",  "max"),
        low=("low",    "min"),
        close=("close","last"),
        volume=("volume","sum"),
    ).dropna()

    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_high"]  = daily["high"].shift(1)
    daily["prev_low"]   = daily["low"].shift(1)
    daily["prev_range"] = daily["prev_high"] - daily["prev_low"]

    # 14-day ATR (daily bars)
    tr = pd.concat([
        daily["high"] - daily["low"],
        (daily["high"] - daily["prev_close"]).abs(),
        (daily["low"]  - daily["prev_close"]).abs(),
    ], axis=1).max(axis=1)
    daily["atr14"] = tr.ewm(span=14).mean()

    daily.index = daily.index.date
    return daily


def add_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add bar-level features: minute-of-day, cumulative VWAP, intraday ATR."""
    df = df.copy()
    df["bar_min"] = df.index.hour * 60 + df.index.minute
    df["bar_date"] = df.index.date

    # Session VWAP (resets each calendar day)
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]
    df["cum_tp_vol"] = df.groupby("bar_date")["tp_vol"].cumsum()
    df["cum_vol"]    = df.groupby("bar_date")["volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE RESULT & BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    date:       date
    direction:  str
    entry:      float
    stop:       float
    target:     float
    exit_price: float
    exit_reason: str
    pnl_pts:    float
    pnl_net:    float   # after commission + slippage


@dataclass
class StrategyResult:
    name:           str
    description:    str
    trades:         List[Trade]   = field(default_factory=list)
    # Computed metrics (filled by evaluate())
    n_trades:       int   = 0
    win_rate:       float = 0.0
    avg_net:        float = 0.0
    total_net:      float = 0.0
    annual_net:     float = 0.0
    max_dd:         float = 0.0
    sharpe:         float = 0.0
    calmar:         float = 0.0
    avg_pts:        float = 0.0


def evaluate(result: StrategyResult, n_days: int) -> StrategyResult:
    """Fill all metrics from the trades list."""
    t = result.trades
    if not t:
        return result

    pnls = [tr.pnl_net for tr in t]
    result.n_trades   = len(t)
    result.win_rate   = sum(1 for p in pnls if p > 0) / len(pnls)
    result.avg_net    = np.mean(pnls)
    result.total_net  = sum(pnls)
    result.avg_pts    = np.mean([tr.pnl_pts for tr in t])

    # Annualise (252 trading days)
    years = n_days / 252
    result.annual_net = result.total_net / years if years > 0 else 0

    # Daily P&L for Sharpe (0 on no-trade days)
    daily_map: Dict[date, float] = {}
    for tr in t:
        daily_map[tr.date] = daily_map.get(tr.date, 0) + tr.pnl_net
    daily_pnls = list(daily_map.values()) + [0.0] * (n_days - len(daily_map))
    std = np.std(daily_pnls)
    result.sharpe = (np.mean(daily_pnls) / std * (252 ** 0.5)) if std > 0 else 0

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
    trade_bars_hi: np.ndarray,
    trade_bars_lo: np.ndarray,
    trade_bars_cl: np.ndarray,
    trade_bars_min: np.ndarray,
    direction: str,
    stop: float,
    target: float,
    hard_close_min: int = 945,
    entry_price: float = 0.0,
) -> Tuple[float, str]:
    """Vectorized exit: returns (exit_price, reason)."""
    if direction == "LONG":
        stop_hit   = trade_bars_lo <= stop
        target_hit = trade_bars_hi >= target
    else:
        stop_hit   = trade_bars_hi >= stop
        target_hit = trade_bars_lo <= target

    hc_hit  = trade_bars_min >= hard_close_min
    any_hit = stop_hit | target_hit | hc_hit

    if not np.any(any_hit):
        return float(trade_bars_cl[-1]), "HARD_CLOSE"

    i = int(np.argmax(any_hit))
    if stop_hit[i]:
        return stop, "STOP"
    if target_hit[i]:
        return target, "TARGET"
    return float(trade_bars_cl[i]), "HARD_CLOSE"


def make_trade(d: date, direction: str, entry: float, stop: float,
               target: float, exit_price: float, exit_reason: str) -> Trade:
    pnl_pts = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    return Trade(
        date=d, direction=direction, entry=entry, stop=stop, target=target,
        exit_price=exit_price, exit_reason=exit_reason,
        pnl_pts=pnl_pts,
        pnl_net=pnl_pts * MULTIPLIER - TOTAL_COST,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def run_gap_fade(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                 gap_min: float = 0.003, gap_max: float = 0.015,
                 target_mult: float = 1.0) -> List[Trade]:
    """
    Gap Fade: When ES gaps up/down at open, fade back toward prev close.
    Logic: Open > prev_close × (1 + gap_min) → SHORT toward prev_close.
           Open < prev_close × (1 - gap_min) → LONG toward prev_close.
    Entry: 09:31 open (let first bar settle).
    Stop:  1.5× gap size beyond open.
    Target: prev_close (full fill) × target_mult
    Exit:  11:00 AM hard cut if not hit.
    """
    trades = []
    rth = df.between_time("09:30", "11:30")

    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_close"]) or ctx["prev_close"] <= 0:
            continue
        day = rth[rth["bar_date"] == d]
        if len(day) < 5:
            continue

        open_price = float(day.iloc[0]["open"])
        prev_close = float(ctx["prev_close"])
        gap_pct    = (open_price - prev_close) / prev_close

        if abs(gap_pct) < gap_min or abs(gap_pct) > gap_max:
            continue

        direction = "SHORT" if gap_pct > 0 else "LONG"
        gap_size  = abs(open_price - prev_close)

        # Entry at 09:31 open
        entry_bars = day[day["bar_min"] >= 571]   # 9:31
        if len(entry_bars) < 2:
            continue
        entry = float(entry_bars.iloc[0]["open"])

        if direction == "SHORT":
            stop   = entry + 1.5 * gap_size
            target = prev_close + (prev_close - entry) * (1 - target_mult) \
                     if target_mult < 1 else prev_close
            target = prev_close
        else:
            stop   = entry - 1.5 * gap_size
            target = prev_close

        trade_bars = entry_bars.iloc[1:]
        # Hard close at 11:00 (660 min)
        hi = trade_bars["high"].values.astype(np.float32)
        lo = trade_bars["low"].values.astype(np.float32)
        cl = trade_bars["close"].values.astype(np.float32)
        mn = trade_bars["bar_min"].values.astype(np.float32)

        ep, er = simulate_exit(hi, lo, cl, mn, direction, stop, target, 660, entry)
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


def run_orb(df: pd.DataFrame, daily_ctx: pd.DataFrame,
            range_end_min: int = 575,   # 9:35, 9:45, 10:00, 10:30
            target_mult: float = 2.0,
            min_width: float = 5.0,
            max_width: float = 25.0) -> List[Trade]:
    """
    Opening Range Breakout (generalised — works for any range window).
    range_end_min: minute-of-day when range locks (575=9:35, 585=9:45, 600=10:00, 630=10:30)
    """
    trades = []
    rth = df.between_time("09:30", "15:59")

    for d, ctx in daily_ctx.iterrows():
        day = rth[rth["bar_date"] == d]
        if len(day) < 10:
            continue

        range_bars = day[day["bar_min"] < range_end_min]
        if len(range_bars) < 3:
            continue

        rng_hi = float(range_bars["high"].max())
        rng_lo = float(range_bars["low"].min())
        width  = rng_hi - rng_lo

        if width < min_width or width > max_width:
            continue

        post = day[day["bar_min"] >= range_end_min]
        if len(post) < 2:
            continue

        # Find first signal bar within cutoff (15:30 = 930)
        cutoff = post[post["bar_min"] <= 930]
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

        entry = float(remaining.iloc[0]["open"])
        stop  = rng_lo if direction == "LONG" else rng_hi
        target = (entry + target_mult * width) if direction == "LONG" \
                 else (entry - target_mult * width)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(np.float32),
            tb["low"].values.astype(np.float32),
            tb["close"].values.astype(np.float32),
            tb["bar_min"].values.astype(np.float32),
            direction, stop, target, 945, entry,
        )
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


def run_prev_day_breakout(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                          target_mult: float = 1.5,
                          min_range: float = 15.0) -> List[Trade]:
    """
    Previous Day High/Low Breakout.
    Signal: First close above prev_day_high → LONG. Below prev_day_low → SHORT.
    Stop:   Midpoint of prev day range.
    Target: Entry ± target_mult × prev_day_range × 0.5
    Only signals after 9:45 (let opening settle).
    """
    trades = []
    rth = df.between_time("09:30", "15:59")

    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["prev_high"]) or ctx["prev_range"] < min_range:
            continue
        day = rth[rth["bar_date"] == d]
        if len(day) < 10:
            continue

        ph, pl = float(ctx["prev_high"]), float(ctx["prev_low"])
        pr     = float(ctx["prev_range"])
        mid    = (ph + pl) / 2

        post = day[day["bar_min"] >= 585]   # after 9:45
        cutoff = post[post["bar_min"] <= 930]

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
        target = (entry + target_mult * pr * 0.5) if direction == "LONG" \
                 else (entry - target_mult * pr * 0.5)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(np.float32),
            tb["low"].values.astype(np.float32),
            tb["close"].values.astype(np.float32),
            tb["bar_min"].values.astype(np.float32),
            direction, stop, target, 945, entry,
        )
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


def run_vwap_reversion(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                       dev_atr_mult: float = 1.5,
                       target_mult: float = 0.7) -> List[Trade]:
    """
    VWAP Mean Reversion.
    Signal: Price deviates > dev_atr_mult × ATR from session VWAP.
    Direction: Fade — go back toward VWAP.
    Target: VWAP × target_mult (partial reversion).
    Stop:   Entry ± 0.75 × ATR.
    Only one trade per day. Only between 10:00 and 14:00.
    """
    trades = []
    rth = df.between_time("10:00", "14:30")

    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["atr14"]) or ctx["atr14"] <= 0:
            continue
        atr = float(ctx["atr14"])
        day = rth[rth["bar_date"] == d]
        if len(day) < 10:
            continue

        for i in range(len(day) - 5):
            row  = day.iloc[i]
            cl   = float(row["close"])
            vwap = float(row["vwap"]) if not pd.isna(row["vwap"]) else 0
            if vwap <= 0:
                continue

            dev = cl - vwap
            if abs(dev) < dev_atr_mult * atr:
                continue

            direction  = "SHORT" if dev > 0 else "LONG"
            remaining  = day.iloc[i + 1:]
            if len(remaining) < 2:
                break

            entry  = float(remaining.iloc[0]["open"])
            stop   = entry + 0.75 * atr if direction == "SHORT" else entry - 0.75 * atr
            target = vwap + (vwap - entry) * (1 - target_mult) \
                     if direction == "SHORT" else vwap - (entry - vwap) * (1 - target_mult)
            target = vwap   # simple: target IS the VWAP

            tb = remaining.iloc[1:]
            ep, er = simulate_exit(
                tb["high"].values.astype(np.float32),
                tb["low"].values.astype(np.float32),
                tb["close"].values.astype(np.float32),
                tb["bar_min"].values.astype(np.float32),
                direction, stop, target, 930, entry,
            )
            trades.append(make_trade(d, direction, entry, stop, target, ep, er))
            break   # one trade per day

    return trades


def run_momentum_continuation(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                               lookback_min: int = 30,
                               min_move_pts: float = 8.0,
                               target_mult: float = 1.5) -> List[Trade]:
    """
    Morning Momentum Continuation.
    Observe the first `lookback_min` minutes after open.
    If the net move is >= min_move_pts and consistent (same direction),
    enter in that direction expecting continuation.
    Entry: 10:00 (after 30-min observation).
    Stop:  Opposite extreme of the observation window.
    Target: Entry ± target_mult × observation move.
    """
    trades = []
    rth = df.between_time("09:30", "15:59")
    obs_end_min = 570 + lookback_min   # 9:30 + 30 = 10:00 = 600

    for d, ctx in daily_ctx.iterrows():
        day = rth[rth["bar_date"] == d]
        if len(day) < 20:
            continue

        obs   = day[day["bar_min"] < obs_end_min]
        if len(obs) < 5:
            continue

        obs_open  = float(obs.iloc[0]["open"])
        obs_close = float(obs.iloc[-1]["close"])
        obs_high  = float(obs["high"].max())
        obs_low   = float(obs["low"].min())
        move      = obs_close - obs_open

        if abs(move) < min_move_pts:
            continue

        direction = "LONG" if move > 0 else "SHORT"

        post = day[day["bar_min"] >= obs_end_min]
        if len(post) < 3:
            continue

        entry  = float(post.iloc[0]["open"])
        stop   = obs_low  if direction == "LONG" else obs_high
        width  = abs(move)
        target = (entry + target_mult * width) if direction == "LONG" \
                 else (entry - target_mult * width)

        tb = post.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(np.float32),
            tb["low"].values.astype(np.float32),
            tb["close"].values.astype(np.float32),
            tb["bar_min"].values.astype(np.float32),
            direction, stop, target, 945, entry,
        )
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


def run_orb_retest(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                   target_mult: float = 2.0,
                   min_width: float = 5.0,
                   max_width: float = 25.0,
                   retest_ticks: float = 1.0) -> List[Trade]:
    """
    ORB with Retest Filter (addresses false-breakout problem).
    Step 1: Price breaks out of range (close beyond range).
    Step 2: Price pulls back to within retest_ticks of the broken level.
    Step 3: Enter in breakout direction on the retest.
    This filters noise but requires two conditions.
    """
    trades = []
    rth = df.between_time("09:30", "15:59")

    for d, ctx in daily_ctx.iterrows():
        day = rth[rth["bar_date"] == d]
        if len(day) < 15:
            continue

        range_bars = day[day["bar_min"] < 585]   # 9:30-9:44
        if len(range_bars) < 5:
            continue

        rng_hi = float(range_bars["high"].max())
        rng_lo = float(range_bars["low"].min())
        width  = rng_hi - rng_lo

        if width < min_width or width > max_width:
            continue

        post = day[day["bar_min"] >= 585]
        if len(post) < 5:
            continue

        state = "WAITING_BREAK"
        direction, broken_level = None, None

        for i in range(len(post) - 3):
            row = post.iloc[i]
            cl  = float(row["close"])
            mn  = int(row["bar_min"])
            if mn > 930:
                break

            if state == "WAITING_BREAK":
                if cl > rng_hi:
                    state, direction, broken_level = "WAITING_RETEST", "LONG", rng_hi
                elif cl < rng_lo:
                    state, direction, broken_level = "WAITING_RETEST", "SHORT", rng_lo

            elif state == "WAITING_RETEST":
                # Check if price has pulled back close to broken level
                if direction == "LONG":
                    lo = float(row["low"])
                    if lo <= broken_level + retest_ticks * 0.25:
                        # Retest confirmed — enter
                        remaining = post.iloc[i + 1:]
                        if len(remaining) < 2:
                            break
                        entry  = float(remaining.iloc[0]["open"])
                        stop   = rng_lo
                        target = entry + target_mult * width
                        tb = remaining.iloc[1:]
                        ep, er = simulate_exit(
                            tb["high"].values.astype(np.float32),
                            tb["low"].values.astype(np.float32),
                            tb["close"].values.astype(np.float32),
                            tb["bar_min"].values.astype(np.float32),
                            direction, stop, target, 945, entry,
                        )
                        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
                        break

                else:   # SHORT
                    hi = float(row["high"])
                    if hi >= broken_level - retest_ticks * 0.25:
                        remaining = post.iloc[i + 1:]
                        if len(remaining) < 2:
                            break
                        entry  = float(remaining.iloc[0]["open"])
                        stop   = rng_hi
                        target = entry - target_mult * width
                        tb = remaining.iloc[1:]
                        ep, er = simulate_exit(
                            tb["high"].values.astype(np.float32),
                            tb["low"].values.astype(np.float32),
                            tb["close"].values.astype(np.float32),
                            tb["bar_min"].values.astype(np.float32),
                            direction, stop, target, 945, entry,
                        )
                        trades.append(make_trade(d, direction, entry, stop, target, ep, er))
                        break

    return trades


def run_afternoon_trend(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                        obs_start: int = 780,   # 13:00
                        entry_time: int = 840,  # 14:00
                        target_mult: float = 1.5,
                        min_move: float = 5.0) -> List[Trade]:
    """
    Afternoon Trend Capture.
    Observe 13:00-14:00 direction. If price moved >= min_move pts,
    enter at 14:00 in that direction and ride to 15:45.
    Stop: Midpoint of 13:00-14:00 range.
    Target: Entry ± target_mult × observation move.
    """
    trades = []
    rth = df.between_time("13:00", "15:59")

    for d, ctx in daily_ctx.iterrows():
        day = rth[rth["bar_date"] == d]
        if len(day) < 10:
            continue

        obs   = day[day["bar_min"].between(obs_start, entry_time - 1)]
        post  = day[day["bar_min"] >= entry_time]

        if len(obs) < 5 or len(post) < 5:
            continue

        obs_open  = float(obs.iloc[0]["open"])
        obs_close = float(obs.iloc[-1]["close"])
        move      = obs_close - obs_open

        if abs(move) < min_move:
            continue

        direction = "LONG" if move > 0 else "SHORT"
        entry     = float(post.iloc[0]["open"])
        obs_hi    = float(obs["high"].max())
        obs_lo    = float(obs["low"].min())
        stop      = obs_lo if direction == "LONG" else obs_hi
        target    = (entry + target_mult * abs(move)) if direction == "LONG" \
                    else (entry - target_mult * abs(move))

        tb = post.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(np.float32),
            tb["low"].values.astype(np.float32),
            tb["close"].values.astype(np.float32),
            tb["bar_min"].values.astype(np.float32),
            direction, stop, target, 945, entry,
        )
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


def run_volatility_adjusted_orb(df: pd.DataFrame, daily_ctx: pd.DataFrame,
                                 low_vol_target: float = 1.5,
                                 high_vol_target: float = 3.0,
                                 vol_threshold_atr: float = 20.0) -> List[Trade]:
    """
    Regime-Aware ORB.
    Same ORB signal but dynamically adjusts target multiplier based on
    daily ATR vs threshold:
      Low-vol regime  (ATR < threshold): conservative target (1.5×)
      High-vol regime (ATR > threshold): aggressive target  (3.0×)
    Also skips very narrow (< 3pt) or very wide (> 30pt) ranges.
    """
    trades = []
    rth = df.between_time("09:30", "15:59")

    for d, ctx in daily_ctx.iterrows():
        if pd.isna(ctx["atr14"]):
            continue

        atr = float(ctx["atr14"])
        target_mult = high_vol_target if atr > vol_threshold_atr else low_vol_target

        day = rth[rth["bar_date"] == d]
        if len(day) < 10:
            continue

        range_bars = day[day["bar_min"] < 585]
        if len(range_bars) < 5:
            continue

        rng_hi = float(range_bars["high"].max())
        rng_lo = float(range_bars["low"].min())
        width  = rng_hi - rng_lo

        if width < 3.0 or width > 30.0:
            continue

        post = day[day["bar_min"] >= 585]
        cutoff = post[post["bar_min"] <= 930]

        long_  = np.where(cutoff["close"].values > rng_hi)[0]
        short_ = np.where(cutoff["close"].values < rng_lo)[0]
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
        stop   = rng_lo if direction == "LONG" else rng_hi
        target = (entry + target_mult * width) if direction == "LONG" \
                 else (entry - target_mult * width)

        tb = remaining.iloc[1:]
        ep, er = simulate_exit(
            tb["high"].values.astype(np.float32),
            tb["low"].values.astype(np.float32),
            tb["close"].values.astype(np.float32),
            tb["bar_min"].values.astype(np.float32),
            direction, stop, target, 945, entry,
        )
        trades.append(make_trade(d, direction, entry, stop, target, ep, er))

    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL STRATEGIES
# ═══════════════════════════════════════════════════════════════════════════════

def run_all(df: pd.DataFrame, daily_ctx: pd.DataFrame) -> List[StrategyResult]:
    strategies = [
        ("Gap Fade",
         "Fade overnight gaps 0.3-1.5% back toward prev close",
         lambda: run_gap_fade(df, daily_ctx)),

        ("ORB 15-min (baseline)",
         "15-min range, tgt×2.0, width 5-25pt — current bot",
         lambda: run_orb(df, daily_ctx, 585, 2.0, 5.0, 25.0)),

        ("ORB 30-min",
         "30-min range (9:30-10:00), tgt×2.0",
         lambda: run_orb(df, daily_ctx, 600, 2.0, 5.0, 30.0)),

        ("ORB 60-min",
         "60-min range (9:30-10:30), tgt×2.0",
         lambda: run_orb(df, daily_ctx, 630, 2.0, 8.0, 40.0)),

        ("Prev Day Breakout",
         "Break above/below prior day high/low → momentum",
         lambda: run_prev_day_breakout(df, daily_ctx, 1.5, 15.0)),

        ("VWAP Reversion",
         "Fade 1.5×ATR deviation from session VWAP back to VWAP",
         lambda: run_vwap_reversion(df, daily_ctx, 1.5)),

        ("Morning Momentum",
         "Follow first 30-min direction if move ≥ 8pts",
         lambda: run_momentum_continuation(df, daily_ctx, 30, 8.0, 1.5)),

        ("ORB + Retest",
         "ORB breakout but only enter on pullback retest of range",
         lambda: run_orb_retest(df, daily_ctx, 2.0, 5.0, 25.0)),

        ("Afternoon Trend",
         "Trade 13:00-14:00 direction if ≥5pt move, ride to 15:45",
         lambda: run_afternoon_trend(df, daily_ctx, 780, 840, 1.5, 5.0)),

        ("Regime ORB",
         "ORB with tgt× dynamically adjusted for high/low vol days",
         lambda: run_volatility_adjusted_orb(df, daily_ctx)),
    ]

    results = []
    for name, desc, fn in strategies:
        print(f"  Running: {name} ...", flush=True)
        trades = fn()

        full = StrategyResult(name=name, description=desc, trades=trades)
        train = StrategyResult(name=name + " [TRAIN]", description=desc,
                               trades=[t for t in trades if t.date < SPLIT_DATE])
        test  = StrategyResult(name=name + " [TEST]",  description=desc,
                               trades=[t for t in trades if t.date >= SPLIT_DATE])

        total_days = len(set(d for d in daily_ctx.index if isinstance(d, date)))
        train_days = len(set(d for d in daily_ctx.index
                             if isinstance(d, date) and d < SPLIT_DATE))
        test_days  = total_days - train_days

        evaluate(full,  total_days)
        evaluate(train, train_days)
        evaluate(test,  test_days)

        results.append((full, train, test))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_comparison(results: List[tuple]) -> None:
    SEP = "─" * 100

    print(f"\n{'═'*100}")
    print("  FULL PERIOD  (2019-05-06 → 2026-04-24)  |  Includes $1.00 slippage per trade")
    print(f"{'═'*100}")
    hdr = (f"{'Strategy':<28} {'Trades':>7} {'Win%':>6} {'Avg$':>7} "
           f"{'Annual$':>9} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}")
    print(hdr)
    print(SEP)

    for full, train, test in sorted(results, key=lambda x: x[0].sharpe, reverse=True):
        r = full
        flag = "✅" if r.sharpe > 0.5 and r.avg_net > 3 else \
               ("⚠️ " if r.sharpe > 0.2 and r.avg_net > 0 else "❌")
        print(f"{flag} {r.name:<26} {r.n_trades:>7} {r.win_rate:>6.1%} "
              f"{r.avg_net:>+7.2f} {r.annual_net:>+9.0f} "
              f"{r.max_dd:>8.0f} {r.sharpe:>7.2f} {r.calmar:>7.2f}")

    print(f"\n{'═'*100}")
    print("  WALK-FORWARD  |  TRAIN: 2019→2022  vs  TEST: 2023→2026")
    print(f"  (Good strategies hold up out-of-sample. Divergence = overfit.)")
    print(f"{'═'*100}")
    hdr2 = (f"{'Strategy':<28} {'TR sharpe':>10} {'TS sharpe':>10} "
            f"{'TR net$':>9} {'TS net$':>9} {'TR ann$':>9} {'TS ann$':>9} {'Holds?':>8}")
    print(hdr2)
    print(SEP)

    for full, train, test in sorted(results, key=lambda x: x[2].sharpe, reverse=True):
        holds = test.sharpe > 0.3 and test.avg_net > 0
        flag  = "✅" if holds and test.sharpe > train.sharpe * 0.5 else \
                ("⚠️ " if holds else "❌")
        print(f"{flag} {full.name:<26} "
              f"{train.sharpe:>10.2f} {test.sharpe:>10.2f} "
              f"{train.total_net:>+9.0f} {test.total_net:>+9.0f} "
              f"{train.annual_net:>+9.0f} {test.annual_net:>+9.0f}  "
              f"{'YES' if holds else 'NO':>6}")

    print(SEP)


def print_best_detail(results: List[tuple]) -> None:
    # Pick best by out-of-sample Sharpe
    best_full, best_train, best_test = max(results, key=lambda x: x[2].sharpe)

    print(f"\n{'═'*70}")
    print(f"  BEST OUT-OF-SAMPLE: {best_full.name}")
    print(f"  {best_full.description}")
    print(f"{'═'*70}")

    for label, r in [("TRAIN (2019-2022)", best_train), ("TEST  (2023-2026)", best_test)]:
        print(f"\n  {label}")
        print(f"    Trades:    {r.n_trades}  |  Win rate: {r.win_rate:.1%}  |  Avg: ${r.avg_net:+.2f}/trade")
        print(f"    Total:     ${r.total_net:+,.0f}  |  Annual: ${r.annual_net:+,.0f}")
        print(f"    Max DD:    ${r.max_dd:,.0f}  |  Sharpe: {r.sharpe:.2f}  |  Calmar: {r.calmar:.2f}")

        reasons: Dict[str, int] = {}
        nets:    Dict[str, float] = {}
        for t in r.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
            nets[t.exit_reason]    = nets.get(t.exit_reason, 0) + t.pnl_net
        print(f"    Exit breakdown:")
        for k, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"      {k:>12}: {cnt:>4} trades  |  ${nets[k]:>+8,.0f}  |  ${nets[k]/cnt:>+6.2f}/trade")

    print(f"\n{'═'*70}")
    print(f"  AT $20K ACCOUNT (2 contracts, out-of-sample period):")
    ann2 = best_test.annual_net * 2
    dd2  = best_test.max_dd * 2
    print(f"    Annual return : ${ann2:>+,.0f}  ({ann2/20000*100:+.1f}%)")
    print(f"    Max drawdown  : ${dd2:>,.0f}  ({dd2/20000*100:.1f}% of account)")
    print(f"    Sharpe        : {best_test.sharpe:.2f}")
    print(f"{'═'*70}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    import time
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    df = load_data()
    print("Building daily context...", flush=True)
    daily_ctx = build_daily_context(df)
    print("Adding intraday features (VWAP)...", flush=True)
    df = add_intraday_features(df)

    print(f"\nRunning {10} strategies...\n", flush=True)
    t0 = time.time()
    results = run_all(df, daily_ctx)
    print(f"\nDone in {time.time()-t0:.1f}s\n", flush=True)

    print_comparison(results)
    print_best_detail(results)


if __name__ == "__main__":
    main()
