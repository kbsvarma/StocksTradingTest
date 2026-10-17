"""Intraday futures backtest engine — proper, cost-aware, no look-ahead.

Design goals (this is meant to be rigorous, not a quick pass):
  - Real RTH sessions (09:30-16:00 ET), flat overnight (no gap/tail risk).
  - Signals use only information available at decision time; fills on the NEXT
    bar's open. No same-bar look-ahead.
  - Realistic costs: commission + slippage per round turn, in points, applied
    to every entry and exit.
  - Per-strategy daily P&L -> the metrics that matter for "steady income":
    avg $/day, % positive days, worst day, daily Sharpe, max drawdown,
    trades/day, win rate, profit factor — all NET of cost.

Contract economics passed in per market (point value, tick, costs).
Everything is reported per ONE micro contract so it maps to what you'd trade.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass
class Market:
    name: str
    point_value: float      # $ per 1.0 price point on the MICRO contract
    tick: float             # min price increment
    slip_ticks: float       # assumed slippage per side, in ticks
    commission_rt: float    # $ commission per round turn (micro)


# Micro-contract economics (approx, conservative)
MARKETS = {
    "ES": Market("Micro S&P (MES)", point_value=5.0,  tick=0.25, slip_ticks=1.0, commission_rt=1.0),
    "NQ": Market("Micro Nasdaq (MNQ)", point_value=2.0, tick=0.25, slip_ticks=1.0, commission_rt=1.0),
    "GC": Market("Micro Gold (MGC)", point_value=10.0, tick=0.10, slip_ticks=1.0, commission_rt=1.2),
    "CL": Market("Micro Crude (MCL)", point_value=100.0, tick=0.01, slip_ticks=1.0, commission_rt=1.2),
}


def load_rth(path: str, mkt: Market) -> dict:
    """Load 1-min parquet -> {date: intraday RTH DataFrame}. Roll days where the
    front contract changes are dropped (price discontinuity)."""
    df = pd.read_parquet(path)
    df = df.tz_convert(ET) if df.index.tz else df.tz_localize("UTC").tz_convert(ET)
    df = df.sort_index()
    df["date"] = df.index.normalize()
    # drop days where the continuous contract rolled (instrument_id changes mid-series)
    roll_days = df.groupby("date")["instrument_id"].nunique()
    bad = set(roll_days[roll_days > 1].index)
    df = df[~df["date"].isin(bad)]
    # RTH only: 09:30 <= t < 16:00
    t = df.index.time
    from datetime import time as dtime
    df = df[(t >= dtime(9, 30)) & (t < dtime(16, 0))]
    sessions = {d: g for d, g in df.groupby("date") if len(g) >= 300}  # need a full-ish day
    return sessions


def _cost_pts(mkt: Market) -> float:
    """Round-turn cost expressed in price POINTS (slippage both sides + commission)."""
    slip = 2 * mkt.slip_ticks * mkt.tick            # entry + exit slippage
    comm = mkt.commission_rt / mkt.point_value       # commission as points
    return slip + comm


def _day_metrics(daily_pnl_usd: pd.Series) -> dict:
    r = daily_pnl_usd.dropna()
    if len(r) < 50:
        return {}
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    sharpe = (r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0
    wins = r[r > 0]; losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf
    return {
        "days": len(r),
        "avg_$/day": r.mean(),
        "%pos": (r > 0).mean() * 100,
        "worst": r.min(),
        "best": r.max(),
        "daily_Sharpe": sharpe,
        "maxDD_$": dd,
        "profit_factor": pf,
        "total_$": r.sum(),
    }


# ── Strategy library — each returns a per-day P&L in POINTS (gross), plus #trades
# Each gets the day's RTH frame (1-min). Fills on next bar. ──────────────────

def orb(day: pd.DataFrame, or_min=15, stop_R=1.0, target_R=2.0):
    """Opening-range breakout. OR = first `or_min` minutes. Enter on first close
    beyond OR; stop = OR opposite edge * stop_R; target = range * target_R; else
    exit at session close. One trade max per day."""
    o = day.iloc[:or_min]
    hi, lo = o["high"].max(), o["low"].min()
    rng = hi - lo
    if rng <= 0:
        return 0.0, 0
    rest = day.iloc[or_min:]
    pos = 0; entry = 0.0
    for i in range(len(rest) - 1):
        c = rest["close"].iloc[i]
        nxt = rest["open"].iloc[i + 1]   # fill next bar open
        if pos == 0:
            if c > hi:
                pos, entry = 1, nxt
            elif c < lo:
                pos, entry = -1, nxt
        else:
            stop = (lo - rng*(stop_R-1)) if pos == 1 else (hi + rng*(stop_R-1))
            tgt = entry + pos * rng * target_R
            px = rest["close"].iloc[i]
            if (pos == 1 and (px <= stop or px >= tgt)) or (pos == -1 and (px >= stop or px <= tgt)):
                return pos * (rest["open"].iloc[i+1] - entry), 1
    if pos != 0:
        return pos * (rest["close"].iloc[-1] - entry), 1
    return 0.0, 0


def vwap_revert(day: pd.DataFrame, ext=1.0, decide_min=120):
    """Intraday mean-reversion to VWAP. After `decide_min`, if price is > `ext`%
    away from session VWAP, fade toward VWAP; exit on VWAP touch or session close."""
    tp = (day["high"] + day["low"] + day["close"]) / 3
    vwap = (tp * day["volume"]).cumsum() / day["volume"].cumsum()
    after = day.iloc[decide_min:]
    pos = 0; entry = 0.0; vw0 = 0.0
    for i in range(len(after) - 1):
        px = after["close"].iloc[i]; vw = vwap.iloc[decide_min + i]
        if pos == 0:
            dev = (px - vw) / vw * 100
            if dev > ext:
                pos, entry, vw0 = -1, after["open"].iloc[i+1], vw
            elif dev < -ext:
                pos, entry, vw0 = 1, after["open"].iloc[i+1], vw
        else:
            if (pos == 1 and px >= vw) or (pos == -1 and px <= vw):
                return pos * (after["open"].iloc[i+1] - entry), 1
    if pos != 0:
        return pos * (after["close"].iloc[-1] - entry), 1
    return 0.0, 0


def gap_fade(day: pd.DataFrame, prev_close: float):
    """If today opens far from yesterday's close, fade the gap toward it; exit EOD."""
    if prev_close is None or prev_close <= 0:
        return 0.0, 0
    op = day["open"].iloc[0]
    gap = (op - prev_close) / prev_close * 100
    if abs(gap) < 0.15:
        return 0.0, 0
    pos = -1 if gap > 0 else 1     # fade
    entry = day["open"].iloc[1] if len(day) > 1 else op
    return pos * (day["close"].iloc[-1] - entry), 1


def last_hour_mom(day: pd.DataFrame):
    """If the day is up by 15:00, buy into the close; if down, short. (Trend-day
    continuation in the last hour.)"""
    if len(day) < 60:
        return 0.0, 0
    px_3pm = day["close"].iloc[-61]
    op = day["open"].iloc[0]
    pos = 1 if px_3pm > op else -1
    return pos * (day["close"].iloc[-1] - px_3pm), 1
