"""
Real reward-eligible target selection via the authenticated /incentive_programs
endpoint. Replaces the public scanner's spread-guessing with the actual list of
markets Kalshi is paying liquidity rewards on right now.

Each program: market_ticker, incentive_type, period_reward, target_size_fp,
discount_factor_bps, start_date/end_date, paid_out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from kalshi_client import KalshiClient


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _parse(t: str | None):
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def active_liquidity_programs(client: KalshiClient) -> list[dict]:
    """All liquidity programs whose reward window is open right now."""
    progs = client._request("GET", "/incentive_programs", signed=True)
    if isinstance(progs, dict):
        progs = progs.get("incentive_programs", [])
    now = datetime.now(timezone.utc)
    out = []
    for p in progs:
        if p.get("incentive_type") != "liquidity" or p.get("paid_out"):
            continue
        s, e = _parse(p.get("start_date")), _parse(p.get("end_date"))
        if s and e and s <= now <= e:
            out.append(p)
    return out


@dataclass
class Target:
    ticker: str
    yes_bid: int
    yes_ask: int
    bid_size: float
    ask_size: float
    period_reward: float
    target_size: float
    room_score: float   # our_size / (our_size + resting) — higher = thinner = more share
    close_time: datetime | None = None  # market resolution; prefer soon so inventory settles fast


def parse_top(ob: dict):
    b = ob.get("orderbook_fp") or ob.get("orderbook") or {}
    yes = b.get("yes_dollars") or b.get("yes") or []
    no = b.get("no_dollars") or b.get("no") or []
    if not yes or not no:
        return None
    yb = round(_f(yes[-1][0]) * 100)
    ybs = _f(yes[-1][1])
    nb = round(_f(no[-1][0]) * 100)
    ya = 100 - nb
    yas = _f(no[-1][1])
    return yb, ybs, ya, yas


def recent_move_cents(client: KalshiClient, ticker: str, n: int = 25) -> float | None:
    """Yes-price range over the last n trades, in cents. None if no trades."""
    try:
        tr = client._request("GET", "/markets/trades",
                             params={"ticker": ticker, "limit": n}, signed=False).get("trades", [])
    except Exception:
        return None
    px = [_f(t.get("yes_price_dollars")) * 100 for t in tr if t.get("yes_price_dollars")]
    if not px:
        return None
    return max(px) - min(px)


def select_targets(client: KalshiClient, our_size: float, max_markets: int,
                   min_spread_cents: int,
                   max_spread_cents: int = 7,
                   price_lo: int = 15, price_hi: int = 85,
                   min_resolution_hours: float = 24.0,
                   max_resolution_days: float = 21.0,
                   min_exit_depth: float = 400.0,
                   max_recent_move_cents: float = 5.0,
                   max_probe: int = 45) -> list[Target]:
    """
    SAFETY-FIRST target selection (redesign after the KXTRUMPTIME -$16.80 loss).

    The old version maximized 'room' (thin books) and preferred SOON resolution — which
    walked us straight into thin markets about to gap-resolve. This version instead
    demands the conditions under which resting two-sided quotes are actually safe:

      - enough depth on BOTH sides that we can EXIT inventory (min_exit_depth)
      - NOT about to resolve (>= min_resolution_hours away) — avoid settlement gaps
      - NOT far-dated (<= max_resolution_days) — avoid capital lockup
      - a moderate spread (min..max) — wide spreads signal uncertainty/illiquidity
      - calm recently (price range over last trades <= max_recent_move_cents)
      - non-extreme price

    We accept a SMALLER reward share in exchange for being able to get out. Whether any
    net edge survives these filters is exactly what the paper test must answer.
    """
    progs = active_liquidity_programs(client)
    cands: list[Target] = []
    for p in progs[:max_probe]:
        tk = p["market_ticker"]
        try:
            top = parse_top(client.get_orderbook(tk, depth=3))
        except Exception:
            continue
        if not top:
            continue
        yb, ybs, ya, yas = top
        spread = ya - yb
        mid = (yb + ya) / 2
        if not (min_spread_cents <= spread <= max_spread_cents):
            continue
        if not (price_lo <= mid <= price_hi):
            continue
        if min(ybs, yas) < min_exit_depth:   # must be exitable on BOTH sides
            continue
        room = our_size / (our_size + ybs + yas) if (ybs + yas) > 0 else 0.0
        cands.append(Target(tk, yb, ya, ybs, yas, _f(p.get("period_reward")),
                            _f(p.get("target_size_fp")), room))

    # cheap pre-rank by reward density, then apply the expensive per-market checks
    cands.sort(key=lambda t: t.period_reward, reverse=True)
    now = datetime.now(timezone.utc)
    kept: list[Target] = []
    for t in cands[: max_markets * 6]:
        # resolution window
        try:
            t.close_time = _parse(client.get_market(t.ticker).get("market", {}).get("close_time"))
        except Exception:
            t.close_time = None
        if t.close_time is not None:
            hrs = (t.close_time - now).total_seconds() / 3600.0
            if hrs < min_resolution_hours or hrs > max_resolution_days * 24:
                continue
        # volatility / recent-move filter
        mv = recent_move_cents(client, t.ticker)
        if mv is not None and mv > max_recent_move_cents:
            continue
        kept.append(t)
        if len(kept) >= max_markets:
            break
    return kept
