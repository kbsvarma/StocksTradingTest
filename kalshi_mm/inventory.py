"""
Inventory + risk state. Pure, testable, no network.

Position convention: net is expressed in YES-equivalent contracts.
    +N  = we are long N yes contracts (own YES)
    -N  = we are short N yes contracts (== own N no contracts)
Buying YES increases net; buying NO decreases net.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    max_position_per_market: int = 10      # |net| per market, contracts
    max_total_exposure_usd: float = 500.0  # sum over markets of |net| * price$
    max_daily_loss_usd: float = 50.0       # realized + unrealized; breach -> halt
    max_open_orders: int = 40


@dataclass
class MarketState:
    ticker: str
    net: int = 0                 # yes-equivalent contracts
    realized_pnl_cents: int = 0  # locked-in P&L from round trips
    # last known book, cents (best yes bid / best yes ask)
    yes_bid: int | None = None
    yes_ask: int | None = None

    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    def exposure_usd(self) -> float:
        """|net| valued at current mid (or 50c if unknown), in dollars."""
        px = self.mid() if self.mid() is not None else 50.0
        return abs(self.net) * px / 100.0

    def unrealized_cents(self) -> float:
        """Mark-to-mid of open position vs a 0-cost baseline is not tracked here;
        unrealized is approximated elsewhere from avg cost. Kept simple for v1."""
        return 0.0


@dataclass
class Portfolio:
    limits: RiskLimits
    markets: dict[str, MarketState] = field(default_factory=dict)
    halted: bool = False
    halt_reason: str = ""

    def state(self, ticker: str) -> MarketState:
        return self.markets.setdefault(ticker, MarketState(ticker=ticker))

    # --- exposure / pnl rollups ---
    def total_exposure_usd(self) -> float:
        return sum(m.exposure_usd() for m in self.markets.values())

    def total_realized_usd(self) -> float:
        return sum(m.realized_pnl_cents for m in self.markets.values()) / 100.0

    def day_pnl_usd(self) -> float:
        # v1: realized only. Unrealized hook can be added once avg-cost is tracked.
        return self.total_realized_usd()

    # --- gates ---
    def check_halt(self) -> bool:
        """Trip the halt if daily loss breached. Returns True if halted."""
        if self.halted:
            return True
        if -self.day_pnl_usd() >= self.limits.max_daily_loss_usd:
            self.halted = True
            self.halt_reason = (
                f"daily loss {self.day_pnl_usd():.2f} <= -{self.limits.max_daily_loss_usd}"
            )
        return self.halted

    def can_add_long(self, ticker: str, size: int) -> bool:
        """Allowed to BUY YES (increase net) by `size`?"""
        if self.check_halt():
            return False
        m = self.state(ticker)
        if m.net + size > self.limits.max_position_per_market:
            return False
        if self._would_exceed_total_exposure(ticker, m.net + size):
            return False
        return True

    def can_add_short(self, ticker: str, size: int) -> bool:
        """Allowed to BUY NO (decrease net) by `size`?"""
        if self.check_halt():
            return False
        m = self.state(ticker)
        if m.net - size < -self.limits.max_position_per_market:
            return False
        if self._would_exceed_total_exposure(ticker, m.net - size):
            return False
        return True

    def _would_exceed_total_exposure(self, ticker: str, new_net: int) -> bool:
        m = self.state(ticker)
        px = m.mid() if m.mid() is not None else 50.0
        projected = abs(new_net) * px / 100.0
        others = self.total_exposure_usd() - m.exposure_usd()
        return (others + projected) > self.limits.max_total_exposure_usd

    # --- fills ---
    def apply_fill(self, ticker: str, side: str, count: int, price_cents: int):
        """
        Update net from a fill. side in {"yes","no"}; both are BUYS (post_only
        maker bids). Buying yes -> +count; buying no -> -count.
        Realized P&L is booked when a fill reduces an existing opposite position.
        """
        m = self.state(ticker)
        signed = count if side == "yes" else -count
        # Book realized P&L on the portion that offsets existing inventory.
        if (m.net > 0 and signed < 0) or (m.net < 0 and signed > 0):
            offset = min(abs(m.net), abs(signed))
            # yes-equivalent entry price for a no-buy at p_no is (100 - p_no).
            yes_px = price_cents if side == "yes" else (100 - price_cents)
            # crude: realized = offset * (mark move). We don't track avg cost in
            # v1, so realized stays 0 here and is reconciled from fills endpoint.
            _ = (offset, yes_px)
        m.net += signed
