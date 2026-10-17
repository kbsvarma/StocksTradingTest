"""
Shadow simulator. Places VIRTUAL two-sided quotes against the REAL order book and
REAL trade prints, then measures what we'd have earned — with no money at risk.

Two outputs that together answer "does this give back money as expected?":

  1) Inventory P&L (REAL, measured): avg-cost book updated from simulated fills.
     This is where adverse selection shows up — if we get filled and price keeps
     running against us, unrealized loss grows. Spread is realized on round trips.

  2) LIP subsidy (ESTIMATED): we measure our time-weighted share of liquidity at
     the touch (this part is real and measured), then multiply by an ASSUMED daily
     pool to get dollars. The pool size is the one number we cannot observe without
     authenticated reward data, so it is a clearly-labelled assumption and reported
     across a pessimistic/expected/optimistic range.

All internal prices are in cents (yes-equivalent). Sizes are contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimAssumptions:
    # Daily LIP pool per market we assume for the $ estimate. Reported as a range.
    pool_usd_low: float = 10.0
    pool_usd_expected: float = 50.0
    pool_usd_high: float = 200.0
    seconds_per_day: float = 86400.0
    maker_fee_cents_per_contract: float = 0.0   # assume 0 maker on targets; verify later


@dataclass
class MarketSim:
    ticker: str
    # open position (yes-equivalent)
    net: float = 0.0
    avg_cost: float = 0.0          # yes cents
    realized_cents: float = 0.0
    fees_cents: float = 0.0
    fills: int = 0
    filled_contracts: float = 0.0
    trades_seen: int = 0           # real prints observed — proxy for "market is active/real"
    # liquidity-share measurement
    share_time: float = 0.0        # integral of share * dt
    score_seconds: float = 0.0     # integral of our_touch_size * dt  (raw LIP score proxy)
    elapsed: float = 0.0
    # last book mark
    last_mid: float | None = None
    # trade dedup
    last_trade_ts: str = ""
    # current virtual quotes (price cents, size); ask stored as yes-equivalent price
    bid: tuple[float, float] | None = None
    ask: tuple[float, float] | None = None

    # -- quoting ----------------------------------------------------------- #
    def set_quotes(self, bid: tuple[float, float] | None, ask: tuple[float, float] | None):
        self.bid = bid
        self.ask = ask

    # -- liquidity-share accrual ------------------------------------------- #
    def accrue(self, dt: float, best_bid_size: float, best_ask_size: float,
               our_at_bid: bool, our_at_ask: bool):
        """
        Integrate our share of touch liquidity over dt. Conservative: we only count
        size that sits at/inside the best price; we treat the existing best size as
        competition even when we improve it.
        """
        our_bid_sz = self.bid[1] if (self.bid and our_at_bid) else 0.0
        our_ask_sz = self.ask[1] if (self.ask and our_at_ask) else 0.0
        our_touch = our_bid_sz + our_ask_sz
        comp = best_bid_size + best_ask_size
        share = (our_touch / (our_touch + comp)) if (our_touch + comp) > 0 else 0.0
        self.share_time += share * dt
        self.score_seconds += our_touch * dt
        self.elapsed += dt

    def avg_share(self) -> float:
        return (self.share_time / self.elapsed) if self.elapsed > 0 else 0.0

    # -- fills (the adverse-selection engine) ------------------------------ #
    def _apply(self, signed_qty: float, price_yes: float, fee_cents: float):
        """signed_qty>0 = bought yes; <0 = bought no (sold yes) at yes-equiv price."""
        self.fills += 1
        self.filled_contracts += abs(signed_qty)
        self.fees_cents += fee_cents
        if self.net == 0 or (self.net > 0) == (signed_qty > 0):
            new_net = self.net + signed_qty
            denom = abs(new_net)
            if denom > 0:
                self.avg_cost = (self.avg_cost * abs(self.net) + price_yes * abs(signed_qty)) / denom
            self.net = new_net
        else:
            close_qty = min(abs(signed_qty), abs(self.net))
            direction = 1 if self.net > 0 else -1
            self.realized_cents += direction * (price_yes - self.avg_cost) * close_qty
            self.net += signed_qty
            if self.net != 0 and (self.net > 0) != (direction > 0):
                self.avg_cost = price_yes  # flipped through zero
            if abs(self.net) < 1e-9:
                self.net = 0.0
                self.avg_cost = 0.0

    def process_trade(self, yes_price: float, count: float, taker_side: str,
                      our_at_bid: bool, our_at_ask: bool, size_ahead_bid: float,
                      size_ahead_ask: float, fee_cents_per: float):
        """
        Fill model with queue priority. A trade only reaches us after the size
        resting ahead at our price is consumed.
          - taker bought NO (sold yes) at yes_price <= our bid -> our bid fills (long)
          - taker bought YES at yes_price >= our ask -> our ask fills (short)
        """
        self.trades_seen += 1
        if self.bid and taker_side == "no":
            bid_px, bid_sz = self.bid
            if yes_price <= bid_px and our_at_bid:
                avail = max(0.0, count - size_ahead_bid)
                q = min(bid_sz, avail)
                if q > 0:
                    self._apply(+q, bid_px, q * fee_cents_per)
        if self.ask and taker_side == "yes":
            ask_px, ask_sz = self.ask
            if yes_price >= ask_px and our_at_ask:
                avail = max(0.0, count - size_ahead_ask)
                q = min(ask_sz, avail)
                if q > 0:
                    self._apply(-q, ask_px, q * fee_cents_per)

    # -- stop loss (the key protection added after the -$16.80 loss) ------- #
    def stop_flatten(self, stop_cents: float, yes_bid: float, yes_ask: float) -> bool:
        """If the open position is underwater by more than stop_cents per contract,
        simulate crossing the spread to exit and realize the loss. Returns True if
        it flattened. This caps a bad fill at ~stop_cents*size instead of full settlement."""
        if self.net == 0:
            return False
        exit_px = yes_bid if self.net > 0 else yes_ask   # long sells at bid; short covers at ask
        loss_per = (self.avg_cost - yes_bid) if self.net > 0 else (yes_ask - self.avg_cost)
        if loss_per > stop_cents:
            self.realized_cents += (exit_px - self.avg_cost) * self.net
            self.fees_cents += abs(self.net) * 0.0   # exit is a taker but maker-fee markets; keep 0
            self.net = 0.0
            self.avg_cost = 0.0
            self.fills += 1
            return True
        return False

    # -- valuation --------------------------------------------------------- #
    def unrealized_cents(self) -> float:
        if self.net == 0 or self.last_mid is None:
            return 0.0
        return self.net * (self.last_mid - self.avg_cost)

    def inventory_pnl_usd(self) -> float:
        return (self.realized_cents + self.unrealized_cents() - self.fees_cents) / 100.0

    def est_reward_usd(self, pool_usd: float, seconds_per_day: float) -> float:
        return self.avg_share() * pool_usd * (self.elapsed / seconds_per_day)


@dataclass
class ShadowPortfolio:
    assume: SimAssumptions = field(default_factory=SimAssumptions)
    markets: dict[str, MarketSim] = field(default_factory=dict)

    def m(self, ticker: str) -> MarketSim:
        return self.markets.setdefault(ticker, MarketSim(ticker=ticker))

    def inventory_pnl_usd(self) -> float:
        return sum(s.inventory_pnl_usd() for s in self.markets.values())

    def est_reward_usd(self, which: str = "expected") -> float:
        pool = {
            "low": self.assume.pool_usd_low,
            "expected": self.assume.pool_usd_expected,
            "high": self.assume.pool_usd_high,
        }[which]
        # only markets that are quoted AND actually trade earn a pool share —
        # a dead market with a wide empty book almost certainly has no LIP pool.
        return sum(
            s.est_reward_usd(pool, self.assume.seconds_per_day)
            for s in self.markets.values()
            if s.elapsed > 0 and s.score_seconds > 0 and s.trades_seen > 0
        )

    def net_usd(self, which: str = "expected") -> float:
        return self.est_reward_usd(which) + self.inventory_pnl_usd()

    def summary(self) -> dict:
        active = [s for s in self.markets.values() if s.score_seconds > 0]
        return {
            "markets_quoted": len(active),
            "markets_with_real_trades": sum(1 for s in active if s.trades_seen > 0),
            "total_fills": sum(s.fills for s in self.markets.values()),
            "total_filled_contracts": round(sum(s.filled_contracts for s in self.markets.values()), 1),
            "inventory_pnl_usd": round(self.inventory_pnl_usd(), 4),
            "est_reward_usd_low": round(self.est_reward_usd("low"), 4),
            "est_reward_usd_expected": round(self.est_reward_usd("expected"), 4),
            "est_reward_usd_high": round(self.est_reward_usd("high"), 4),
            "net_usd_low": round(self.net_usd("low"), 4),
            "net_usd_expected": round(self.net_usd("expected"), 4),
            "net_usd_high": round(self.net_usd("high"), 4),
            "avg_share_pct": round(
                100 * (sum(s.share_time for s in active) / max(1e-9, sum(s.elapsed for s in active))), 2
            ) if active else 0.0,
            "per_market": {
                s.ticker: {
                    "net": round(s.net, 1),
                    "fills": s.fills,
                    "avg_share_pct": round(100 * s.avg_share(), 2),
                    "inv_pnl_usd": round(s.inventory_pnl_usd(), 4),
                    "est_reward_exp_usd": round(
                        s.est_reward_usd(self.assume.pool_usd_expected, self.assume.seconds_per_day), 4),
                }
                for s in sorted(active, key=lambda x: -x.score_seconds)[:15]
            },
        }
