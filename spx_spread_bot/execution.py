from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import (
    IB,
    ComboLeg,
    Contract,
    LimitOrder,
    MarketOrder,
    Option,
    Order,
    TagValue,
    Ticker,
    Trade,
)

from config_loader import BotConfig
from models import CandidateSpread, LegDirection, OpenPosition, OptionLegSpec, PositionLeg
from trade_logger import BotLogger


class ExecutionEngine:
    def __init__(self, ib: IB, cfg: BotConfig, logger: BotLogger):
        self.ib = ib
        self.cfg = cfg
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)
        self._contract_cache: dict[int, Contract] = {}

    def place_entry_with_retries(
        self,
        candidate: CandidateSpread,
        contracts: int,
        spx_price: float,
        vix_price: float,
    ) -> tuple[Optional[OpenPosition], str]:
        if not candidate.quote:
            return None, "candidate quote missing"

        try:
            position_legs = self._qualify_position_legs(candidate.expiry, candidate.legs)
            combo = self._build_combo(position_legs)
        except Exception as exc:  # noqa: BLE001
            return None, f"contract qualification failed: {exc}"

        initial_limit = candidate.quote.mid
        limit_price = initial_limit
        placed_at = time.time()

        for attempt in range(1, self.cfg.max_retries + 1):
            order = LimitOrder("SELL", contracts, round(limit_price, 2), tif="DAY")
            self._apply_combo_routing(order, leg_count=len(position_legs))
            trade = self.ib.placeOrder(combo, order)
            self.logger.order_event(
                "ENTRY_ORDER_SUBMITTED",
                {
                    "attempt": attempt,
                    "strategy": candidate.strategy.value,
                    "limit_price": limit_price,
                    "contracts": contracts,
                    "expiry": candidate.expiry,
                    "short_put_strike": candidate.short_put_strike,
                    "short_call_strike": candidate.short_call_strike,
                    "order_id": trade.order.orderId,
                    "max_loss_per_contract": candidate.max_loss_per_contract,
                },
            )

            wait_secs = 5 if self.cfg.paper_trading else self.cfg.retry_wait_seconds
            status = self._wait_for_fill(trade, timeout=wait_secs)

            # Paper engine doesn't fill multi-leg combos reliably — simulate a
            # fill at the submitted limit price so paper bots can actually trade.
            _paper_simulated = False
            if status != "FILLED" and self.cfg.paper_trading:
                self._cancel_trade_if_open(trade)
                status = "FILLED"
                _paper_simulated = True
                self.logger.order_event("PAPER_FILL_SIMULATED", {
                    "attempt": attempt, "strategy": candidate.strategy.value,
                    "simulated_fill": limit_price, "order_id": trade.order.orderId,
                })

            if status == "FILLED":
                fill_price = limit_price if _paper_simulated else self._avg_fill_price(trade)
                stop_price = round(max(
                    fill_price * self.cfg.stop_multiplier,
                    fill_price + self.cfg.min_stop_distance,
                ), 2)
                profit_target = round(fill_price * (1 - self.cfg.profit_target_pct), 2)

                open_position = OpenPosition(
                    strategy=candidate.strategy.value,
                    entry_ts=datetime.now(self.tz),
                    expiry=candidate.expiry,
                    legs=position_legs,
                    contracts=contracts,
                    entry_credit=fill_price,
                    entry_spx=spx_price,
                    entry_vix=vix_price,
                    stop_price=stop_price,
                    profit_target_price=profit_target,
                    combo_order_id=trade.order.orderId,
                    short_put_strike=candidate.short_put_strike,
                    short_call_strike=candidate.short_call_strike,
                    max_loss_per_contract=candidate.max_loss_per_contract,
                )

                ok, reason = self.place_protective_orders(open_position)
                if not ok:
                    self.logger.error(f"protective order failure: {reason}; flattening")
                    flattened, flat_reason = self.close_open_position_market(open_position, "SAFETY_FLATTEN")
                    if not flattened:
                        return None, f"protection failed and flatten failed: {flat_reason}"
                    return None, f"protection failed and flattened: {reason}"

                return open_position, "filled"

            self._cancel_trade_if_open(trade)

            if time.time() - placed_at > self.cfg.fill_timeout_seconds:
                return None, "entry fill timeout exceeded"

            limit_price = round(limit_price - self.cfg.retry_price_step, 2)
            if limit_price <= 0:
                return None, "invalid retry limit price"

        return None, "entry not filled after retries"

    def place_protective_orders(self, pos: OpenPosition) -> tuple[bool, str]:
        """Place broker-side profit target only (GTC limit buy-back at 50% credit).

        IBKR rejects StopOrder on many SMART-routed index-option BAG combos.
        Stop-loss enforcement is therefore handled in the 1-second monitor loop.
        """
        if self.cfg.paper_trading or self.cfg.disable_profit_target:
            pos.stop_order_id = None
            pos.profit_order_id = None
            self.logger.order_event(
                "PROTECTION_ACTIVE",
                {
                    "strategy": pos.strategy,
                    "profit_order_id": None,
                    "stop_price": pos.stop_price,
                    "stop_mechanism": "monitor_market_close",
                    "profit_target": "disabled" if self.cfg.disable_profit_target else pos.profit_target_price,
                },
            )
            return True, "paper_simulated" if self.cfg.paper_trading else "profit_target_disabled"

        combo = self._build_combo(pos.legs)

        target_order = LimitOrder("BUY", pos.contracts, pos.profit_target_price, tif="GTC")
        self._apply_combo_routing(target_order, leg_count=len(pos.legs))

        start = time.time()
        target_trade = self.ib.placeOrder(combo, target_order)

        while time.time() - start < self.cfg.protection_deadline_seconds:
            self.ib.sleep(0.25)
            target_status = (target_trade.orderStatus.status or "").upper()
            if target_status in {"PENDINGSUBMIT", "PRESUBMITTED", "SUBMITTED", "FILLED"}:
                pos.stop_order_id = None
                pos.profit_order_id = target_trade.order.orderId
                self.logger.order_event(
                    "PROTECTION_ACTIVE",
                    {
                        "strategy": pos.strategy,
                        "profit_order_id": pos.profit_order_id,
                        "stop_price": pos.stop_price,
                        "stop_mechanism": "monitor_market_close",
                        "profit_target": pos.profit_target_price,
                    },
                )
                return True, "ok"

        return False, "profit target order not acknowledged in time"

    def close_open_position_market(self, pos: OpenPosition, reason: str) -> tuple[bool, str]:
        combo = self._build_combo(pos.legs)
        order = MarketOrder("BUY", pos.contracts, tif="DAY")
        self._apply_combo_routing(order, leg_count=len(pos.legs))
        trade = self.ib.placeOrder(combo, order)
        self.logger.order_event(
            "FORCE_CLOSE_SUBMITTED",
            {
                "order_id": trade.order.orderId,
                "strategy": pos.strategy,
                "reason": reason,
            },
        )

        status = self._wait_for_fill(trade, timeout=30)
        if status == "FILLED":
            return True, "filled"

        # Combo did not fill within timeout — cancel the dangling order and
        # return False so the monitor retries on the next cycle.  Individual
        # leg fallback is intentionally removed: placing SELL on the long leg
        # without combo context causes IBKR Error 201 (margin deficit for
        # what looks like a naked short).
        try:
            self.ib.cancelOrder(trade.order)
        except Exception as cancel_exc:  # noqa: BLE001
            self.logger.warning(f"cancel after combo timeout failed: {cancel_exc}")
        self.logger.warning(
            f"combo market close timed out (orderId={trade.order.orderId}); "
            "order cancelled — monitor will retry next cycle"
        )
        return False, "combo timed out — will retry"

    def close_open_position_limit(self, pos: OpenPosition, limit_price: float, reason: str) -> tuple[bool, float]:
        # Paper mode: IB cancels limit combo orders immediately (no real position
        # exists since entry was paper-simulated).  Simulate an instant fill at
        # the submitted limit price so profit targets close correctly in paper mode.
        if self.cfg.paper_trading:
            self.logger.order_event(
                "LIMIT_CLOSE_SUBMITTED",
                {
                    "order_id": 0,
                    "strategy": pos.strategy,
                    "reason": reason,
                    "limit_price": limit_price,
                    "paper_simulated": True,
                },
            )
            return True, limit_price

        combo = self._build_combo(pos.legs)
        order = LimitOrder("BUY", pos.contracts, round(limit_price, 2), tif="DAY")
        self._apply_combo_routing(order, leg_count=len(pos.legs))
        trade = self.ib.placeOrder(combo, order)

        self.logger.order_event(
            "LIMIT_CLOSE_SUBMITTED",
            {
                "order_id": trade.order.orderId,
                "strategy": pos.strategy,
                "reason": reason,
                "limit_price": limit_price,
            },
        )

        status = self._wait_for_fill(trade, timeout=45)
        if status == "FILLED":
            return True, self._avg_fill_price(trade)

        self._cancel_trade_if_open(trade)
        return False, 0.0

    def cancel_order(self, order_id: Optional[int]) -> None:
        if not order_id:
            return
        for trade in self.ib.trades():
            if trade.order.orderId == order_id:
                try:
                    self.ib.cancelOrder(trade.order)
                    self.logger.order_event("ORDER_CANCELLED", {"order_id": order_id})
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"order cancel failed order_id={order_id}: {exc}")
                return

    def cancel_protective_orders(self, pos: OpenPosition) -> None:
        self.cancel_order(pos.stop_order_id)
        self.cancel_order(pos.profit_order_id)

    def order_is_active(self, order_id: Optional[int]) -> bool:
        """Return True if the order is working OR already filled.

        FILLED is intentionally included: if the broker-side GTC profit target
        already filled, we must NOT submit a second close order — that would
        open an untracked long position instead of closing an existing short.
        """
        if not order_id:
            return False
        for trade in self.ib.trades():
            if trade.order.orderId != order_id:
                continue
            status = (trade.orderStatus.status or "").upper()
            return status in {"PENDINGSUBMIT", "PRESUBMITTED", "SUBMITTED", "FILLED"}
        return False

    def order_is_filled(self, order_id: Optional[int]) -> bool:
        """Return True if the order exists in ib.trades() with status FILLED."""
        if not order_id:
            return False
        for trade in self.ib.trades():
            if trade.order.orderId != order_id:
                continue
            status = (trade.orderStatus.status or "").upper()
            return status == "FILLED"
        return False

    def spread_mark(self, pos: OpenPosition) -> Optional[float]:
        contracts = [self._contract_from_conid(leg.con_id) for leg in pos.legs]
        tickers = self._stream_tickers(contracts)
        if len(tickers) != len(pos.legs):
            return None

        quote = self._close_debit_quote(pos.legs, tickers)
        if quote is None:
            return None
        return quote[2]

    def spread_quote_components(self, pos: OpenPosition) -> Optional[dict[str, float]]:
        contracts = [self._contract_from_conid(leg.con_id) for leg in pos.legs]
        tickers = self._stream_tickers(contracts)
        if len(tickers) != len(pos.legs):
            return None

        quote = self._close_debit_quote(pos.legs, tickers)
        if quote is None:
            return None

        bid, ask, mid = quote
        return {"bid": bid, "ask": ask, "mid": mid}

    def _stream_tickers(self, contracts: list[Contract]) -> list[Ticker]:
        """Get tickers via streaming subscription (snapshot=False).

        IBKR snapshot market data (reqMktData snapshot=True, used by reqTickers)
        silently drops requests for XSP options on this gateway. Streaming works.
        We subscribe, wait 2s for ticks, read, then cancel.
        """
        stream_tickers = [self.ib.reqMktData(c, "", False, False) for c in contracts]
        self.ib.sleep(2.0)
        for c in contracts:
            try:
                self.ib.cancelMktData(c)
            except Exception:  # noqa: BLE001
                pass
        return stream_tickers

    def leg_contracts(self, pos: OpenPosition) -> list[Contract]:
        return [self._contract_from_conid(leg.con_id) for leg in pos.legs]

    def _qualify_position_legs(self, expiry: str, legs: list[OptionLegSpec]) -> list[PositionLeg]:
        out: list[PositionLeg] = []
        for leg in legs:
            contract = self._build_option_contract(expiry=expiry, strike=leg.strike, right=leg.right)
            out.append(
                PositionLeg(
                    right=leg.right,
                    strike=leg.strike,
                    direction=leg.direction,
                    quantity=leg.quantity,
                    con_id=contract.conId,
                )
            )
        return out

    def _build_option_contract(self, expiry: str, strike: float, right: str) -> Contract:
        option = Option(
            self.cfg.underlying_symbol,
            expiry,
            strike,
            right,
            exchange=self.cfg.option_exchange,
            tradingClass=self.cfg.preferred_trading_class,
            multiplier="100",
            currency=self.cfg.currency,
        )
        qualified = self.ib.qualifyContracts(option)
        if not qualified:
            raise RuntimeError(f"unable to qualify option {expiry} {strike}{right}")
        contract = qualified[0]
        self._contract_cache[contract.conId] = contract
        return contract

    def _contract_from_conid(self, conid: int) -> Contract:
        cached = self._contract_cache.get(conid)
        if cached:
            return cached
        probe = Contract(conId=conid, exchange=self.cfg.option_exchange)
        qualified = self.ib.qualifyContracts(probe)
        if not qualified:
            raise RuntimeError(f"unable to qualify contract conId={conid}")
        self._contract_cache[conid] = qualified[0]
        return qualified[0]

    def _build_combo(self, legs: list[PositionLeg]) -> Contract:
        combo = Contract()
        combo.symbol = self.cfg.underlying_symbol
        combo.secType = "BAG"
        combo.currency = self.cfg.currency
        combo.exchange = self.cfg.combo_exchange

        combo_legs: list[ComboLeg] = []
        for leg in legs:
            # BAG leg action is set to the close-side action. Entry uses SELL and
            # IBKR flips legs accordingly; close uses BUY.
            close_action = "BUY" if leg.direction == LegDirection.SHORT else "SELL"
            combo_legs.append(
                ComboLeg(
                    conId=leg.con_id,
                    ratio=max(int(leg.quantity), 1),
                    action=close_action,
                    exchange=self.cfg.option_exchange,
                )
            )
        combo.comboLegs = combo_legs
        return combo

    @staticmethod
    def _apply_combo_routing(order: Order, leg_count: int) -> None:
        # Use guaranteed combo routing so IBKR margins both legs together as a
        # defined-risk spread.  NonGuaranteed routing causes IBKR to evaluate
        # each leg independently for margin, which makes the short put appear
        # naked and triggers the "uncovered position" Error 201 rejection.
        order.smartComboRoutingParams = []

    def _cancel_trade_if_open(self, trade: Trade) -> None:
        status = (trade.orderStatus.status or "").upper()
        if status in {"FILLED", "CANCELLED", "INACTIVE"}:
            return
        try:
            self.ib.cancelOrder(trade.order)
            self.logger.order_event("ORDER_CANCELLED", {"order_id": trade.order.orderId})
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"cancel failed order_id={trade.order.orderId}: {exc}")

    def _wait_for_fill(self, trade: Trade, timeout: int) -> str:
        start = time.time()
        while time.time() - start < timeout:
            self.ib.sleep(0.25)
            status = (trade.orderStatus.status or "").upper()
            if status in {"FILLED", "CANCELLED", "INACTIVE"}:
                return status
        return (trade.orderStatus.status or "TIMEOUT").upper()

    @staticmethod
    def _avg_fill_price(trade: Trade) -> float:
        avg = float(trade.orderStatus.avgFillPrice or 0.0)
        if avg > 0:
            return round(avg, 2)
        if trade.fills:
            return round(float(trade.fills[-1].execution.avgPrice), 2)
        return 0.0

    @staticmethod
    def _close_debit_quote(legs: list[PositionLeg], tickers: list[Ticker]) -> Optional[tuple[float, float, float]]:
        debit_bid = 0.0
        debit_ask = 0.0

        for leg, ticker in zip(legs, tickers):
            prices = ExecutionEngine._ticker_bid_ask_or_ref(ticker)
            if prices is None:
                return None
            bid, ask = prices

            qty = max(int(leg.quantity), 1)
            if leg.direction == LegDirection.SHORT:
                debit_bid += bid * qty
                debit_ask += ask * qty
            else:
                debit_bid -= ask * qty
                debit_ask -= bid * qty

        mid = round(max((debit_bid + debit_ask) / 2.0, 0.0), 2)
        return round(debit_bid, 2), round(debit_ask, 2), mid

    @staticmethod
    def _ticker_bid_ask_or_ref(ticker: Ticker) -> Optional[tuple[float, float]]:
        bid = float(ticker.bid or 0.0)
        ask = float(ticker.ask or 0.0)
        if bid > 0 and ask > 0:
            return bid, ask

        refs: list[object] = []
        try:
            refs.append(ticker.marketPrice())
        except Exception:  # noqa: BLE001
            pass
        refs.extend([ticker.last, ticker.close, ticker.modelGreeks.optPrice if ticker.modelGreeks else None])

        for raw in refs:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                synthetic = round(value, 2)
                return synthetic, synthetic
        return None
