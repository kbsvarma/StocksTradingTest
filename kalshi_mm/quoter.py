"""
Quote computation. Pure function: (book, fair, inventory, config) -> desired orders.

Kalshi two-sided maker construction (all maker bids, post_only):
  - To bid for YES at price b cents:  buy YES @ b.
  - To offer YES at price a cents:    buy NO @ (100 - a)   [since yes+no = 100c].
Both rest as maker liquidity and both count toward the Liquidity Incentive Program.

Inventory skew: when we are long YES we shift BOTH quotes down (cheaper bid, more
aggressive offer) to shed inventory; when short we shift up. This is the primary
defense against adverse selection.
"""

from __future__ import annotations

from dataclasses import dataclass

from inventory import Portfolio


@dataclass
class QuoteConfig:
    quote_size: int = 1
    edge_cents: int = 2
    min_spread_cents: int = 3
    inventory_skew_cents: int = 1   # shift per net contract held
    post_only: bool = True


@dataclass
class OrderIntent:
    ticker: str
    side: str        # "yes" | "no"
    action: str      # always "buy" for our maker construction
    price_cents: int
    count: int

    def key(self) -> tuple:
        return (self.ticker, self.side, self.price_cents)


def _clamp_price(c: int) -> int:
    return max(1, min(99, int(round(c))))


def compute_quotes(
    ticker: str,
    yes_bid: int | None,
    yes_ask: int | None,
    fair_cents: float | None,
    portfolio: Portfolio,
    cfg: QuoteConfig,
) -> list[OrderIntent]:
    """
    Return the desired resting maker orders (0, 1, or 2 of them).
    Empty if we shouldn't quote (halted, no book, spread too tight).
    """
    if portfolio.check_halt():
        return []
    if yes_bid is None or yes_ask is None or yes_ask <= yes_bid:
        return []
    if (yes_ask - yes_bid) < cfg.min_spread_cents:
        return []  # no edge to capture as a maker

    fair = fair_cents if fair_cents is not None else (yes_bid + yes_ask) / 2.0

    m = portfolio.state(ticker)
    skew = m.net * cfg.inventory_skew_cents  # long(+) -> shift quotes down
    fair_adj = fair - skew

    bid_px = _clamp_price(fair_adj - cfg.edge_cents)
    ask_px = _clamp_price(fair_adj + cfg.edge_cents)

    # Never quote through the existing market as a "maker" — stay at or inside
    # passively, but not crossing. Keep our bid <= best ask-1 and ask >= best bid+1.
    bid_px = min(bid_px, yes_ask - 1)
    ask_px = max(ask_px, yes_bid + 1)
    if bid_px >= ask_px:
        return []

    intents: list[OrderIntent] = []
    size = cfg.quote_size

    # Bid side: buy YES @ bid_px (only if we can get longer)
    if portfolio.can_add_long(ticker, size):
        intents.append(OrderIntent(ticker, "yes", "buy", bid_px, size))

    # Ask side: buy NO @ (100 - ask_px) (only if we can get shorter)
    if portfolio.can_add_short(ticker, size):
        intents.append(OrderIntent(ticker, "no", "buy", _clamp_price(100 - ask_px), size))

    return intents


def diff_orders(
    desired: list[OrderIntent], resting: list[OrderIntent]
) -> tuple[list[OrderIntent], list[OrderIntent]]:
    """
    Reconcile: return (to_place, to_cancel). An order is kept if an identical
    (ticker, side, price) already rests. Everything else is replaced.
    """
    desired_by_key = {o.key(): o for o in desired}
    resting_by_key = {o.key(): o for o in resting}
    to_place = [o for k, o in desired_by_key.items() if k not in resting_by_key]
    to_cancel = [o for k, o in resting_by_key.items() if k not in desired_by_key]
    return to_place, to_cancel
