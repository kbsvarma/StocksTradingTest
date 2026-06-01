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

# Last-known data source per fetch type. Updated on every call to the public
# get_*/find_* helpers below. Callers query via last_*_source() to record into
# event log / trades.csv. Single-threaded use within the bot — these helpers
# are called from the main run loop, not from Telegram daemon threads.
_last_spx_source: str = "unknown"
_last_vix_source: str = "unknown"
_last_chain_source: str = "unknown"
# Cached last-fetched values so dashboard can read live IBKR prices via
# heartbeat without calling yfinance (which is 60s-cached + Yahoo lag).
# Populated by get_spx_price / get_vix_price; None until first call.
_last_spx_value: float | None = None
_last_vix_value: float | None = None
_last_quote_ts: str = ""  # ISO timestamp of last spx/vix fetch
# Best available 50pt-spread mid credit seen on the LAST scan, even when it is
# below min_credit (observability only — does NOT affect trade decisions).
# Lets the dashboard show a "Credit GO/NO-GO" light with the real number.
_last_best_credit: float | None = None
# Full top-N spread band from the last scan (observability only — for the
# dashboard's live-chain terminal). List of dicts: short/long/otm/mid/bid/ask.
_last_spread_table: list | None = None


def last_best_credit() -> float | None:
    """Best available spread mid credit from the most recent scan (may be < min_credit)."""
    return _last_best_credit


def last_spread_table() -> list | None:
    """Top-N scanned spreads from the most recent poll (for live-chain display)."""
    return _last_spread_table


def _spread_table_from(results, spx_price) -> list:
    """Build a compact top-5 table (sorted by mid desc) from raw scan results."""
    rows = []
    try:
        for r in sorted(results, key=lambda x: -float(x["mid"]))[:5]:
            sk = float(r["short_strike"]); lk = float(r["long_strike"])
            otm = round((spx_price - sk) / spx_price * 100, 1) if spx_price else None
            rows.append({
                "short": int(sk), "long": int(lk), "otm": otm,
                "mid": round(float(r["mid"]), 2),
                "bid": round(float(r.get("bid", 0) or 0), 2),
                "ask": round(float(r.get("ask", 0) or 0), 2),
            })
    except Exception:
        return []
    return rows


def last_spx_value() -> float | None:
    """Last SPX value cached by get_spx_price. None if no fetch yet."""
    return _last_spx_value


def last_vix_value() -> float | None:
    """Last VIX value cached by get_vix_price. None if no fetch yet."""
    return _last_vix_value


def last_quote_ts() -> str:
    """ISO timestamp of last SPX/VIX fetch. Empty if no fetch yet."""
    return _last_quote_ts


def last_spx_source() -> str:
    """Source ('IBKR' / 'yfinance' / 'unknown') of the most recent SPX price fetch."""
    return _last_spx_source


def last_vix_source() -> str:
    """Source ('IBKR' / 'yfinance' / 'unknown') of the most recent VIX price fetch."""
    return _last_vix_source


def last_chain_source() -> str:
    """Source of the most recent options-chain / spread scan."""
    return _last_chain_source


@dataclass
class SpreadQuote:
    short_strike: float
    long_strike: float
    expiry: str          # YYYY-MM-DD
    mid: float           # net credit (short_put_mid - long_put_mid)
    bid: float           # conservative credit (worst case)
    ask: float           # optimistic credit


def _try_ibkr_spot(symbol: str) -> Optional[float]:
    """IBKR-first spot. Returns None on any failure so caller falls back to yfinance."""
    try:
        from webull_bot.ibkr_market_data import get_index_spot_ibkr
        return get_index_spot_ibkr(symbol)
    except Exception:
        return None


def get_spx_price(yf_symbol: str = "^GSPC") -> float:
    """SPX spot. IBKR-first, yfinance fallback. Updates last_spx_source()."""
    global _last_spx_source, _last_spx_value, _last_quote_ts
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    from webull_bot import data_source_health as _dsh
    # IBKR-first
    ibkr_price = _try_ibkr_spot("SPX")
    if ibkr_price and ibkr_price > 100:
        _last_spx_source = "IBKR"
        _last_spx_value = float(ibkr_price)
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        _dsh.report("ibkr", up=True)
        return float(ibkr_price)
    _dsh.report("ibkr", up=False)
    # yfinance fallback
    _last_spx_source = "yfinance"
    ticker = yf.Ticker(yf_symbol)
    info = ticker.fast_info
    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    if price and price > 100:
        _last_spx_value = float(price)
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        return float(price)
    hist = ticker.history(period="1d", interval="1m")
    if not hist.empty:
        v = float(hist["Close"].iloc[-1])
        _last_spx_value = v
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        return v
    raise RuntimeError(f"Cannot fetch SPX price from {yf_symbol}")


def get_vix_price() -> float:
    """VIX spot. IBKR-first, yfinance fallback. Updates last_vix_source()."""
    global _last_vix_source, _last_vix_value, _last_quote_ts
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    from webull_bot import data_source_health as _dsh
    # IBKR-first
    ibkr_price = _try_ibkr_spot("VIX")
    if ibkr_price and ibkr_price > 0:
        _last_vix_source = "IBKR"
        _last_vix_value = float(ibkr_price)
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        _dsh.report("ibkr", up=True)
        return float(ibkr_price)
    _dsh.report("ibkr", up=False)
    # yfinance fallback
    _last_vix_source = "yfinance"
    ticker = yf.Ticker("^VIX")
    info = ticker.fast_info
    price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
    if price and price > 0:
        _last_vix_value = float(price)
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        return float(price)
    hist = ticker.history(period="1d", interval="1m")
    if not hist.empty:
        v = float(hist["Close"].iloc[-1])
        _last_vix_value = v
        _last_quote_ts = _dt.now(_Z("America/New_York")).isoformat()
        return v
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


_SPX_OPEN_CACHE: dict = {}  # {date: open_price} — open is fixed once market opens


def get_spx_open(today: date, yf_symbol: str = "^GSPC") -> float:
    """Return SPX open price for today. Returns 0.0 if unavailable (fails open).

    Cached per-day: the open is constant once the market opens, so we only
    hit yfinance until we get a positive value, then serve from cache for the
    rest of the session. This removes a redundant network call from both the
    direction-filter scan loop and the heartbeat (which run every ~30s).
    Pre-market (no open yet) returns 0.0 and is NOT cached, so the first real
    open after 9:30 populates the cache.
    """
    cached = _SPX_OPEN_CACHE.get(today)
    if cached and cached > 0:
        return cached
    try:
        hist = yf.Ticker(yf_symbol).history(period="2d", interval="1d")
        if hist.empty:
            return 0.0
        today_rows = hist[hist.index.date == today]
        if today_rows.empty:
            return 0.0
        val = float(today_rows["Open"].iloc[0])
        if val > 0:
            _SPX_OPEN_CACHE[today] = val  # cache only real positive opens
        return val
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

    IBKR-first (real-time OPRA bid/ask), yfinance fallback (~15min delayed).
    Returns the spread with credit closest to $2.00 that meets min_credit.

    Bug-fix 2026-05-20: was yfinance-only — caused fills to miss because limit
    prices were computed off 15-min-stale mids. find_top_spreads (force reports)
    already used IBKR-first; the live-trading path was missed. Now both consistent.
    """
    global _last_chain_source, _last_best_credit, _last_spread_table

    if expiry is None:
        expiry = get_0dte_expiry(yf_options_symbol)
    if expiry is None:
        return None

    # ── IBKR-first: real-time OPRA quotes ──────────────────────────────
    # If IBKR is reachable (no exception), trust its result entirely — even
    # when it returns empty/None. Reason: yfinance is ~15min delayed; if IBKR
    # says "no qualifying spread" that's the real-time truth, not a fallback case.
    ibkr_reachable = False
    try:
        from webull_bot.ibkr_market_data import get_top_spreads_ibkr
        ibkr_results = get_top_spreads_ibkr(
            spx_price=spx_price,
            otm_pct=otm_pct,
            spread_width=spread_width,
            expiry=expiry,
            symbol="SPXW",
            top_n=5,
        )
        ibkr_reachable = True   # no exception = IBKR responded (even if empty)
        from webull_bot import data_source_health as _dsh
        _dsh.report("ibkr", up=True)
        if ibkr_results:
            qualified = [r for r in ibkr_results if r["mid"] >= min_credit]
            _last_chain_source = "IBKR"
            # Observability: record best available mid + full band even if below floor.
            try:
                _last_best_credit = max(float(r["mid"]) for r in ibkr_results)
                _last_spread_table = _spread_table_from(ibkr_results, spx_price)
            except Exception:
                pass
            if qualified:
                best = min(qualified, key=lambda r: abs(r["mid"] - 2.0))
                return SpreadQuote(**best)
            return None  # IBKR has chain but no qualifying spread — TRUST IT
        # IBKR responded with empty list — also trust it
        _last_chain_source = "IBKR"
        _last_best_credit = None  # no chain → no credit to show
        _last_spread_table = []
        return None
    except Exception:
        pass

    # ── yfinance fallback ONLY if IBKR truly unreachable (exception) ───
    if ibkr_reachable:
        # Shouldn't get here, but defensive: don't lie about source
        return None
    from webull_bot import data_source_health as _dsh
    _dsh.report("ibkr", up=False)
    _last_chain_source = "yfinance"

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

    _yf_best_credit = [None]  # mutable holder for best mid seen (observability)
    _yf_rows = []             # full band for live-chain display

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

        # Observability: track best available mid + full band even if below floor.
        # Fully wrapped — this must never break the spread scan / order path.
        try:
            if _yf_best_credit[0] is None or net_credit_mid > _yf_best_credit[0]:
                _yf_best_credit[0] = net_credit_mid
            _yf_rows.append({
                "short": int(short_strike), "long": int(long_strike),
                "otm": round((spx_price - short_strike) / spx_price * 100, 1) if spx_price else None,
                "mid": round(net_credit_mid, 2), "bid": round(spread_bid, 2), "ask": round(spread_ask, 2),
            })
        except Exception:
            pass

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

    try:
        _last_best_credit = round(_yf_best_credit[0], 2) if _yf_best_credit[0] is not None else None
        _last_spread_table = sorted(_yf_rows, key=lambda x: -x["mid"])[:5]
    except Exception:
        pass
    return best


def find_top_spreads(
    spx_price: float,
    otm_pct: float,
    spread_width: float,
    yf_options_symbol: str = "^SPX",
    expiry: Optional[str] = None,
    top_n: int = 4,
) -> list[SpreadQuote]:
    """Return the top N bull put spread candidates around the OTM target.

    Unlike find_best_spread this:
    - Scans a wider window (10 strikes around target, not 5)
    - Applies NO min_credit filter — returns everything with a positive mid
      so the user can see the full picture and choose
    - Sorts by credit descending (highest credit first)
    - Returns up to top_n results

    Used for the force-entry recommendation display only.
    """
    if expiry is None:
        expiry = get_0dte_expiry(yf_options_symbol)
    if expiry is None:
        return []

    global _last_chain_source
    from webull_bot import data_source_health as _dsh
    # IBKR-first: real-time bid/ask from the gateway
    try:
        from webull_bot.ibkr_market_data import get_top_spreads_ibkr
        ibkr_results = get_top_spreads_ibkr(
            spx_price=spx_price,
            otm_pct=otm_pct,
            spread_width=spread_width,
            expiry=expiry,
            symbol="SPXW",
            top_n=top_n,
        )
        if ibkr_results:
            _last_chain_source = "IBKR"
            _dsh.report("ibkr", up=True)
            return [SpreadQuote(**r) for r in ibkr_results]
    except Exception:
        pass
    _dsh.report("ibkr", up=False)

    # yfinance fallback
    _last_chain_source = "yfinance"
    ticker = yf.Ticker(yf_options_symbol)
    try:
        chain = ticker.option_chain(expiry)
    except Exception:
        return []

    puts = chain.puts.copy()
    if puts.empty:
        return []

    target_short = round(spx_price * (1.0 - otm_pct) / 5) * 5
    available = sorted(puts["strike"].tolist())
    if not available:
        return []

    # Wider window: 10 nearest strikes around target
    candidates = sorted(available, key=lambda s: abs(s - target_short))[:10]
    puts_idx = puts.set_index("strike")

    results: list[SpreadQuote] = []

    for short_strike in candidates:
        ideal_long = short_strike - spread_width
        below = [s for s in available if s <= ideal_long]
        if not below:
            continue
        long_strike = max(below)
        if short_strike - long_strike < spread_width * 0.8:
            continue

        try:
            short_row = puts_idx.loc[short_strike]
            long_row  = puts_idx.loc[long_strike]
        except KeyError:
            continue

        short_bid = float(short_row.get("bid", 0) or 0)
        short_ask = float(short_row.get("ask", 0) or 0)
        long_bid  = float(long_row.get("bid", 0) or 0)
        long_ask  = float(long_row.get("ask", 0) or 0)

        if short_bid <= 0 or long_ask <= 0:
            continue

        short_mid = (short_bid + short_ask) / 2
        long_mid  = (long_bid + long_ask) / 2
        net_credit_mid = short_mid - long_mid
        spread_bid = short_bid - long_ask
        spread_ask = short_ask - long_bid

        if net_credit_mid <= 0:
            continue

        results.append(SpreadQuote(
            short_strike=float(short_strike),
            long_strike=float(long_strike),
            expiry=expiry,
            mid=round(net_credit_mid, 2),
            bid=round(spread_bid, 2),
            ask=round(spread_ask, 2),
        ))

    # Sort by credit descending — richest credit first
    results.sort(key=lambda q: q.mid, reverse=True)
    return results[:top_n]


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
