"""Market data via yfinance — SPX price, VIX, options chain, strike selection.

yfinance symbol guide:
  ^GSPC  — SPX index price
  ^VIX   — VIX index price
  ^SPX   — SPX options chain (full weekly/daily strikes)
  SPY    — SPY options (backup)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

ET = ZoneInfo("America/New_York")

_CHAIN_CACHE: dict[str, tuple[float, object]] = {}
_CHAIN_TTL = 60.0  # seconds before re-fetching chain


@dataclass
class SpreadQuote:
    short_strike: float
    long_strike: float
    expiry: str          # YYYY-MM-DD
    mid: float           # net credit (short_put_mid - long_put_mid)
    bid: float           # conservative credit (worst case)
    ask: float           # optimistic credit


def get_spx_price(yf_symbol: str = "^GSPC") -> float:
    ticker = yf.Ticker(yf_symbol)
    info = ticker.fast_info
    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    if price and price > 100:
        return float(price)
    hist = ticker.history(period="1d", interval="1m")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    raise RuntimeError(f"Cannot fetch SPX price from {yf_symbol}")


def get_vix_price() -> float:
    ticker = yf.Ticker("^VIX")
    info = ticker.fast_info
    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    if price and price > 0:
        return float(price)
    hist = ticker.history(period="1d", interval="1m")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    raise RuntimeError("Cannot fetch VIX price")


def get_vix_open(today: date) -> float:
    """Return VIX open price for today. Returns 0.0 if unavailable (fails open)."""
    try:
        hist = yf.Ticker("^VIX").history(period="2d", interval="1d")
        if hist.empty:
            return 0.0
        today_rows = hist[hist.index.date == today]
        if today_rows.empty:
            return 0.0
        return float(today_rows["Open"].iloc[0])
    except Exception:
        return 0.0


def get_spx_open(today: date, yf_symbol: str = "^GSPC") -> float:
    """Return SPX open price for today. Returns 0.0 if unavailable (fails open)."""
    try:
        hist = yf.Ticker(yf_symbol).history(period="2d", interval="1d")
        if hist.empty:
            return 0.0
        today_rows = hist[hist.index.date == today]
        if today_rows.empty:
            return 0.0
        return float(today_rows["Open"].iloc[0])
    except Exception:
        return 0.0


def _get_ticker_cached(symbol: str) -> yf.Ticker:
    now = time.monotonic()
    cached = _CHAIN_CACHE.get(symbol)
    if cached and now - cached[0] < _CHAIN_TTL:
        return cached[1]  # type: ignore[return-value]
    ticker = yf.Ticker(symbol)
    _CHAIN_CACHE[symbol] = (now, ticker)
    return ticker


def get_0dte_expiry(yf_options_symbol: str = "^SPX") -> Optional[str]:
    """Return today's 0DTE expiry string (YYYY-MM-DD) if it exists in the chain."""
    today = datetime.now(ET).date()
    today_str = today.strftime("%Y-%m-%d")
    ticker = yf.Ticker(yf_options_symbol)
    try:
        exps = ticker.options
    except Exception:
        return None
    return today_str if today_str in exps else None


def find_best_spread(
    spx_price: float,
    otm_pct: float,
    spread_width: float,
    min_credit: float,
    yf_options_symbol: str = "^SPX",
    expiry: Optional[str] = None,
) -> Optional[SpreadQuote]:
    """Find the best bull put spread near the target OTM level.

    Scans strikes around the 1% OTM target. Returns the spread with credit
    closest to $2.00 that meets the min_credit threshold.
    """
    if expiry is None:
        expiry = get_0dte_expiry(yf_options_symbol)
    if expiry is None:
        return None

    ticker = yf.Ticker(yf_options_symbol)
    try:
        chain = ticker.option_chain(expiry)
    except Exception:
        return None

    puts = chain.puts.copy()
    if puts.empty:
        return None

    # Target short put: 1% OTM, rounded to nearest 5pt
    target_short = round(spx_price * (1.0 - otm_pct) / 5) * 5

    available = sorted(puts["strike"].tolist())
    if not available:
        return None

    # Scan nearest 5 strikes around target
    candidates = sorted(available, key=lambda s: abs(s - target_short))[:5]

    puts_idx = puts.set_index("strike")

    best: Optional[SpreadQuote] = None
    best_diff = float("inf")

    for short_strike in candidates:
        # Find long strike: ideally exactly spread_width below, else nearest available
        ideal_long = short_strike - spread_width
        below = [s for s in available if s <= ideal_long]
        if not below:
            continue
        long_strike = max(below)
        if short_strike - long_strike < spread_width * 0.8:
            continue  # spread too narrow

        try:
            short_row = puts_idx.loc[short_strike]
            long_row = puts_idx.loc[long_strike]
        except KeyError:
            continue

        short_bid = float(short_row.get("bid", 0) or 0)
        short_ask = float(short_row.get("ask", 0) or 0)
        long_bid = float(long_row.get("bid", 0) or 0)
        long_ask = float(long_row.get("ask", 0) or 0)

        if short_bid <= 0 or long_ask <= 0:
            continue

        short_mid = (short_bid + short_ask) / 2
        long_mid = (long_bid + long_ask) / 2
        net_credit_mid = short_mid - long_mid
        spread_bid = short_bid - long_ask
        spread_ask = short_ask - long_bid

        if net_credit_mid < min_credit:
            continue

        diff = abs(net_credit_mid - 2.0)
        if diff < best_diff:
            best_diff = diff
            best = SpreadQuote(
                short_strike=float(short_strike),
                long_strike=float(long_strike),
                expiry=expiry,
                mid=round(net_credit_mid, 2),
                bid=round(spread_bid, 2),
                ask=round(spread_ask, 2),
            )

    return best


def get_spread_mark(
    short_strike: float,
    long_strike: float,
    expiry: str,
    yf_options_symbol: str = "^SPX",
) -> Optional[float]:
    """Fetch current mark of an open spread for stop-loss monitoring (fresh quote)."""
    # Always fetch fresh — remove from cache
    _CHAIN_CACHE.pop(yf_options_symbol, None)

    ticker = yf.Ticker(yf_options_symbol)
    try:
        chain = ticker.option_chain(expiry)
    except Exception:
        return None

    puts = chain.puts.set_index("strike")
    try:
        short_row = puts.loc[short_strike]
        long_row = puts.loc[long_strike]
    except KeyError:
        return None

    short_bid = float(short_row.get("bid", 0) or 0)
    short_ask = float(short_row.get("ask", 0) or 0)
    long_bid = float(long_row.get("bid", 0) or 0)
    long_ask = float(long_row.get("ask", 0) or 0)

    if short_bid <= 0 or long_ask <= 0:
        return None

    short_mid = (short_bid + short_ask) / 2
    long_mid = (long_bid + long_ask) / 2
    return round(short_mid - long_mid, 2)
