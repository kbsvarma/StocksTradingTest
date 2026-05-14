"""IBKR real-time spread mark via ib_insync — used by the Webull bot monitor.

Connects to IB Gateway on 127.0.0.1:4001. Falls back to None on any error
so the caller can fall back to yfinance.

ClientId convention:
  - 41: the legacy IBKR trading bot (XSP era)
  - 42: this Webull bot's daemon (monitor)
  - 43–47: auto-bump range for ad-hoc scripts (force reports, manual probes)
           when running alongside the daemon — _connect() tries each in order
           if the preferred ID is already in use.

Set env var WEBULL_IBKR_CLIENT_ID to override the starting ID for the current
process. Useful for one-off scripts that want to be explicit about not
colliding with the daemon.

Usage:
    mark = get_spread_mark_ibkr(short_strike, long_strike, expiry)
    # returns float (net mid) or None if unavailable
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Optional

_ib = None  # module-level IB instance — reused across calls

# Default clientId selection:
#   - When invoked with WEBULL_INSTANCE_NAME set (i.e. from the bot's launchd
#     plist or systemd unit), use 42 — the daemon owns this id.
#   - Otherwise (ad-hoc CLI scripts, --report, manual diagnostics), use 46 —
#     reserved for ad-hoc to avoid colliding with the running daemon's 42.
# Override either case with explicit WEBULL_IBKR_CLIENT_ID env var.
_IS_BOT_CONTEXT = bool(os.environ.get("WEBULL_INSTANCE_NAME"))
_DEFAULT_CLIENT_ID = int(os.environ.get(
    "WEBULL_IBKR_CLIENT_ID",
    "42" if _IS_BOT_CONTEXT else "46",
))
# Fallbacks: bot starts at 42 and bumps through these on collision; ad-hoc
# starts at 46 and bumps through 47 / 43-45 (avoiding 42 specifically since
# that would steal the bot's slot).
_FALLBACK_CLIENT_IDS = (
    [43, 44, 45, 46, 47] if _IS_BOT_CONTEXT else [47, 45, 44, 43]
)


def _connect(client_id: Optional[int] = None) -> Optional[object]:
    """Return a connected IB instance, or None if Gateway is unreachable.

    Tries the preferred `client_id` (defaults to env-configured value, then 42).
    If that ID is already in use by another process (Error 326), auto-bumps
    through _FALLBACK_CLIENT_IDS before giving up. This lets ad-hoc scripts
    coexist with the running daemon without manual ID juggling.
    """
    global _ib
    try:
        from ib_insync import IB
        if _ib is not None and _ib.isConnected():
            return _ib

        preferred = client_id if client_id is not None else _DEFAULT_CLIENT_ID
        candidates = [preferred] + [
            cid for cid in _FALLBACK_CLIENT_IDS if cid != preferred
        ]

        for cid in candidates:
            try:
                ib = IB()
                ib.RequestTimeout = 6
                ib.connect("127.0.0.1", 4001, clientId=cid, timeout=5, readonly=True)
                _ib = ib
                return _ib
            except TimeoutError:
                # ib_insync raises bare TimeoutError when IBG rejects the
                # connect (Error 326 = client id already in use). The actual
                # "326" message is logged separately and not in the exception.
                # → try the next clientId.
                continue
            except Exception:
                # Anything else (ConnectionRefusedError when gateway is down,
                # OSError, etc.) means the gateway itself is the problem,
                # not the clientId. No point trying more IDs.
                break
        _ib = None
        return None
    except Exception:
        _ib = None
        return None


def _valid(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) and f > 0 else None
    except (TypeError, ValueError):
        return None


def get_spread_mark_ibkr(
    short_strike: float,
    long_strike: float,
    expiry: str,           # YYYY-MM-DD
    symbol: str = "SPXW",
    right: str = "P",
) -> Optional[float]:
    """Return real-time mid mark of the spread from IBKR, or None on failure.

    mark = short_put_mid - long_put_mid  (net debit to buy back the spread)
    """
    try:
        ib = _connect()
        if ib is None:
            return None

        from ib_insync import Option

        # IBKR expiry format: YYYYMMDD
        exp_ibkr = expiry.replace("-", "")

        short_contract = Option(
            symbol, exp_ibkr, short_strike, right,
            exchange="SMART",
            tradingClass=symbol,
            currency="USD",
            multiplier="100",
        )
        long_contract = Option(
            symbol, exp_ibkr, long_strike, right,
            exchange="SMART",
            tradingClass=symbol,
            currency="USD",
            multiplier="100",
        )

        # Qualify contracts
        q = ib.qualifyContracts(short_contract, long_contract)
        if len(q) != 2:
            return None
        short_contract, long_contract = q[0], q[1]

        # Stream real-time ticks
        short_ticker = ib.reqMktData(short_contract, "", False, False)
        long_ticker  = ib.reqMktData(long_contract,  "", False, False)
        ib.sleep(1.5)

        # Cancel subscriptions immediately after reading
        try:
            ib.cancelMktData(short_contract)
            ib.cancelMktData(long_contract)
        except Exception:
            pass

        def mid(ticker) -> Optional[float]:
            bid = _valid(ticker.bid)
            ask = _valid(ticker.ask)
            if bid and ask:
                return round((bid + ask) / 2.0, 2)
            # fallback to last/close
            return _valid(ticker.last) or _valid(ticker.close)

        short_mid = mid(short_ticker)
        long_mid  = mid(long_ticker)

        if short_mid is None or long_mid is None:
            return None

        return round(short_mid - long_mid, 2)

    except Exception:
        global _ib
        _ib = None   # reset so next call reconnects
        return None


def get_index_spot_ibkr(symbol: str) -> Optional[float]:
    """Return real-time index spot price from IBKR, or None.

    Exchange routing per symbol:
      - SPX, SPXW, VIX → CBOE
      - NDX, NDXP      → NASDAQ
    NASDAQ index quotes require a separate IBKR market-data subscription;
    without it, qualifyContracts succeeds but quotes return NaN. In that
    case this returns None and the caller falls back to yfinance.
    """
    try:
        ib = _connect()
        if ib is None:
            return None
        from ib_insync import Index
        sym = symbol.upper().strip()
        # NDX/NDXP trade on NASDAQ; SPX/SPXW/VIX on CBOE.
        if sym in ("NDX", "NDXP"):
            exchange = "NASDAQ"
        else:
            exchange = "CBOE"
        contract = Index(sym, exchange=exchange, currency="USD")
        q = ib.qualifyContracts(contract)
        if not q:
            return None
        contract = q[0]
        ticker = ib.reqMktData(contract, "", False, False)
        ib.sleep(1.0)
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
        bid = _valid(ticker.bid)
        ask = _valid(ticker.ask)
        if bid and ask:
            return round((bid + ask) / 2.0, 2)
        return _valid(ticker.last) or _valid(ticker.close) or _valid(ticker.marketPrice())
    except Exception:
        global _ib
        _ib = None
        return None


def get_top_spreads_ibkr(
    spx_price: float,
    otm_pct: float,
    spread_width: float,
    expiry: str,           # YYYY-MM-DD
    symbol: str = "SPXW",
    top_n: int = 4,
) -> Optional[list[dict]]:
    """Scan ~10 strikes around the OTM target on IBKR, build top-N spread quotes.

    Returns list of dicts {short_strike, long_strike, expiry, mid, bid, ask}
    sorted by mid descending, or None if IBKR is unreachable / no data.
    """
    try:
        ib = _connect()
        if ib is None:
            return None
        from ib_insync import Option

        exp_ibkr = expiry.replace("-", "")
        target_short = round(spx_price * (1.0 - otm_pct) / 5) * 5

        # Candidate strikes: 10 nearest 5pt strikes around target
        candidate_shorts = sorted(
            {target_short + 5 * i for i in range(-5, 6)}
        )
        candidate_longs = {s - spread_width for s in candidate_shorts}
        all_strikes = sorted(set(candidate_shorts) | candidate_longs)

        contracts = [
            Option(symbol, exp_ibkr, k, "P",
                   exchange="SMART", tradingClass=symbol,
                   currency="USD", multiplier="100")
            for k in all_strikes
        ]
        q = ib.qualifyContracts(*contracts)
        qualified = {c.strike: c for c in q if getattr(c, "conId", 0)}
        if not qualified:
            return None

        # Fire all reqMktData subscriptions in parallel, then sleep once
        tickers = {k: ib.reqMktData(c, "", False, False) for k, c in qualified.items()}
        ib.sleep(2.0)

        def mid_of(t) -> tuple[Optional[float], Optional[float], Optional[float]]:
            b = _valid(t.bid); a = _valid(t.ask)
            m = (b + a) / 2.0 if (b and a) else (_valid(t.last) or _valid(t.close))
            return b, a, (round(m, 2) if m else None)

        leg_quotes: dict[float, tuple] = {}
        for k, t in tickers.items():
            leg_quotes[k] = mid_of(t)
            try: ib.cancelMktData(qualified[k])
            except Exception: pass

        results = []
        for short_k in candidate_shorts:
            long_k = short_k - spread_width
            sb, sa, sm = leg_quotes.get(short_k, (None, None, None))
            lb, la, lm = leg_quotes.get(long_k, (None, None, None))
            if sm is None or lm is None:
                continue
            net_mid = round(sm - lm, 2)
            if net_mid <= 0:
                continue
            spread_bid = round((sb or 0) - (la or 0), 2) if (sb and la) else net_mid
            spread_ask = round((sa or 0) - (lb or 0), 2) if (sa and lb) else net_mid
            results.append({
                "short_strike": float(short_k),
                "long_strike":  float(long_k),
                "expiry":       expiry,
                "mid":          net_mid,
                "bid":          spread_bid,
                "ask":          spread_ask,
            })

        if not results:
            return None
        results.sort(key=lambda r: r["mid"], reverse=True)
        return results[:top_n]
    except Exception:
        global _ib
        _ib = None
        return None


def disconnect() -> None:
    """Cleanly disconnect the IBKR data connection."""
    global _ib
    if _ib is not None:
        try:
            _ib.disconnect()
        except Exception:
            pass
        _ib = None
