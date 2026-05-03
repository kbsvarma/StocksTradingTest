# broker.py — IBKR connection, contract resolution, data subscriptions,
#             order management, and position reconciliation.

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ib_insync import (
    IB,
    BarDataList,
    Fill,
    Future,
    Index,
    LimitOrder,
    MarketOrder,
    StopOrder,
    Trade,
)

import config
import logger as L


# ── Order tracking ─────────────────────────────────────────────────────────────

@dataclass
class OrderBundle:
    """Tracks the three legs of a bracket: entry, stop, target."""
    parent: Trade
    stop: Trade
    target: Trade

    # Timestamps and prices populated as events arrive
    submitted_at: Optional[datetime] = None
    entry_filled_at: Optional[datetime] = None
    entry_fill_price: Optional[float] = None
    stop_confirmed: bool = False
    target_confirmed: bool = False
    protective_confirmed: bool = False
    close_filled_at: Optional[datetime] = None
    close_fill_price: Optional[float] = None
    close_reason: Optional[str] = None

    @property
    def parent_id(self) -> int:
        return self.parent.order.orderId

    @property
    def stop_id(self) -> int:
        return self.stop.order.orderId

    @property
    def target_id(self) -> int:
        return self.target.order.orderId


# ── Broker client ──────────────────────────────────────────────────────────────

class BrokerClient:
    """
    Thin, event-driven wrapper around ib_insync.
    All IBKR interactions live here. Bot.py consumes callbacks.
    """

    def __init__(self, log: logging.Logger) -> None:
        self._ib = IB()
        self._log = log
        self._tz = ZoneInfo(config.TIMEZONE)
        self._contract = None
        self._bars: Optional[BarDataList] = None
        self._bundle: Optional[OrderBundle] = None
        self._hard_close_order_id: Optional[int] = None
        self._pending_close_reason: str = "HARD_CLOSE"  # tracks reason for market close
        self._last_close: Optional[float] = None  # latest bar close (for dashboard)
        self._connected: bool = False
        self._intentional_disconnect: bool = False

        # ── Callbacks set by Bot ───────────────────────────────────────────────
        self.on_bar: Optional[Callable] = None
        self.on_entry_fill: Optional[Callable] = None
        self.on_exit_fill: Optional[Callable] = None
        self.on_hard_close_fill: Optional[Callable] = None
        self.on_protective_confirmed: Optional[Callable] = None
        self.on_order_rejected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        # Fired after async reconnect resolves (success / exhausted all attempts)
        self.on_reconnected: Optional[Callable] = None
        self.on_reconnect_failed: Optional[Callable] = None

        # ── Wire IBKR events ───────────────────────────────────────────────────
        # Use lambdas to prevent ib_insync's weak-reference system from dropping
        # the bound method when self is still alive but weakly held.
        self._ib.disconnectedEvent += lambda: self._on_ib_disconnect()
        self._ib.orderStatusEvent += lambda trade: self._on_order_status(trade)
        self._ib.execDetailsEvent += lambda trade, fill: self._on_exec_details(trade, fill)
        self._ib.errorEvent += lambda reqId, code, msg, contract: self._on_error(reqId, code, msg, contract)

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        port = self._port()
        label = "IB Gateway" if config.USE_IB_GATEWAY else "TWS"
        mode = "PAPER" if config.PAPER_TRADING else "⚠️  LIVE"

        L.log_info(self._log, "STARTUP", "CONNECT",
                   f"Connecting → {label} {mode} {config.TWS_HOST}:{port} clientId=1")
        try:
            self._intentional_disconnect = False
            self._ib.connect(config.TWS_HOST, port, clientId=1, timeout=20)
            self._connected = True

            # Critical: set delayed data for paper accounts immediately
            self._ib.reqMarketDataType(config.MARKET_DATA_TYPE)
            L.log_info(self._log, "STARTUP", "CONNECTED",
                       f"Connected | serverVer={self._ib.client.serverVersion()} "
                       f"marketDataType={config.MARKET_DATA_TYPE}"
                       f"({'delayed' if config.MARKET_DATA_TYPE == 3 else 'live'})")
            return True

        except Exception as exc:
            L.log_error(self._log, "STARTUP", "CONNECT_FAILED", str(exc))
            return False

    def disconnect(self) -> None:
        self._intentional_disconnect = True
        try:
            if self._bars:
                self._ib.cancelHistoricalData(self._bars)
        except Exception:
            pass
        if self._ib.isConnected():
            self._ib.disconnect()
        self._connected = False
        L.log_info(self._log, "CLOSED", "DISCONNECT", "Disconnected from IBKR")

    async def _async_reconnect(self) -> None:
        """
        Async reconnect loop — scheduled via asyncio.ensure_future() from
        _on_ib_disconnect so it runs on the *existing* event loop rather than
        trying to start a blocking connect() inside a loop callback (which raises
        "This event loop is already running").

        On success fires on_reconnected; on exhaustion fires on_reconnect_failed.
        """
        for attempt in range(1, config.RECONNECT_MAX + 1):
            L.log_info(self._log, "BROKER", "RECONNECT_ATTEMPT",
                       f"Attempt {attempt}/{config.RECONNECT_MAX} — "
                       f"waiting {config.RECONNECT_INTERVAL}s...")
            await asyncio.sleep(config.RECONNECT_INTERVAL)
            try:
                await self._ib.connectAsync(
                    config.TWS_HOST, self._port(), clientId=1, timeout=10
                )
                self._ib.reqMarketDataType(config.MARKET_DATA_TYPE)
                self._connected = True
                self._intentional_disconnect = False
                L.log_info(self._log, "BROKER", "RECONNECT_SUCCESS",
                           f"Reconnected on attempt {attempt}")
                if self.on_reconnected:
                    self.on_reconnected()
                return
            except Exception as exc:
                L.log_warning(self._log, "BROKER", "RECONNECT_ATTEMPT_FAILED",
                              f"Attempt {attempt}/{config.RECONNECT_MAX} failed: {exc}")

        L.log_critical(self._log, "BROKER", "RECONNECT_EXHAUSTED",
                       f"All {config.RECONNECT_MAX} reconnect attempts failed. "
                       "Check TWS/IB Gateway. Position may be open.")
        if self.on_reconnect_failed:
            self.on_reconnect_failed()

    def _port(self) -> int:
        if config.USE_IB_GATEWAY:
            return config.GW_PAPER_PORT if config.PAPER_TRADING else config.GW_LIVE_PORT
        return config.TWS_PAPER_PORT if config.PAPER_TRADING else config.TWS_LIVE_PORT

    # ── Contract resolution ────────────────────────────────────────────────────

    def resolve_contract(self):
        """Resolve the nearest non-expired liquid MES quarterly contract."""
        L.log_info(self._log, "STARTUP", "CONTRACT_RESOLVE",
                   f"Resolving {config.SYMBOL} front-month contract...")

        probe = Future(symbol=config.SYMBOL, exchange="CME", currency=config.CURRENCY)
        details_list = self._ib.reqContractDetails(probe)

        if not details_list:
            L.log_error(self._log, "STARTUP", "CONTRACT_FAILED",
                        "reqContractDetails returned empty list")
            return None

        now = datetime.now(self._tz)
        candidates = []

        L.log_info(self._log, "STARTUP", "CONTRACT_LIST",
                   f"reqContractDetails returned {len(details_list)} contract(s)")

        for d in details_list:
            c = d.contract
            exp_str = c.lastTradeDateOrContractMonth
            local_sym = getattr(c, "localSymbol", "?")
            try:
                # IBKR returns YYYYMMDD or YYYYMM
                raw = exp_str[:8] if len(exp_str) >= 8 else (exp_str + "01")
                exp_dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=self._tz)
                days_left = (exp_dt - now).days
                L.log_info(self._log, "STARTUP", "CONTRACT_CANDIDATE",
                           f"localSymbol={local_sym} expiry={exp_str} "
                           f"days_left={days_left} conId={c.conId}")
                if exp_dt > now + timedelta(days=1):
                    candidates.append((exp_dt, c))
            except ValueError as ve:
                L.log_warning(self._log, "STARTUP", "CONTRACT_SKIP",
                              f"localSymbol={local_sym} expStr={exp_str!r} parse error: {ve}")
                continue

        if not candidates:
            L.log_error(self._log, "STARTUP", "CONTRACT_FAILED",
                        "No non-expired contracts with > 1 day remaining")
            return None

        candidates.sort(key=lambda x: x[0])
        _, front_contract = candidates[0]

        qualified = self._ib.qualifyContracts(front_contract)
        if not qualified:
            L.log_error(self._log, "STARTUP", "CONTRACT_FAILED", "qualifyContracts returned empty")
            return None

        self._contract = qualified[0]
        L.log_info(self._log, "STARTUP", "CONTRACT_RESOLVED",
                   f"localSymbol={self._contract.localSymbol} | "
                   f"conId={self._contract.conId} | "
                   f"exchange={self._contract.exchange} | "
                   f"expiry={self._contract.lastTradeDateOrContractMonth} | "
                   f"multiplier={self._contract.multiplier}")
        return self._contract

    # ── Market data ────────────────────────────────────────────────────────────

    def get_vix(self) -> Optional[float]:
        """Snapshot VIX. Returns None if unavailable."""
        L.log_debug(self._log, "STARTUP", "VIX_REQUEST", "Requesting VIX snapshot...")
        try:
            vix_contract = Index("VIX", "CBOE", "USD")
            ticker = self._ib.reqMktData(vix_contract, "", True, False)
            self._ib.sleep(3)  # wait for snapshot delivery

            vix = ticker.last
            if vix is None or vix != vix:  # NaN check
                vix = ticker.close
            if vix is None or vix != vix:
                vix = ticker.marketPrice()

            self._ib.cancelMktData(vix_contract)

            if vix and vix > 0:
                L.log_info(self._log, "STARTUP", "VIX_OK", f"VIX = {vix:.2f}")
                return float(vix)

            L.log_warning(self._log, "STARTUP", "VIX_EMPTY",
                          "VIX snapshot returned empty/NaN")
            return None

        except Exception as exc:
            L.log_error(self._log, "STARTUP", "VIX_ERROR", str(exc))
            return None

    def get_prev_session_close(self) -> Optional[float]:
        """Pull the previous regular-session close for gap calculation.
        Retries once after a short delay — HMDS error 162 is transient on paper."""
        if not self._contract:
            return None
        L.log_info(self._log, "STARTUP", "PREV_CLOSE_REQUEST",
                    "Requesting previous session close...")

        for attempt in (1, 2):
            try:
                bars = self._ib.reqHistoricalData(
                    self._contract,
                    endDateTime="",
                    durationStr="3 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                )
                if bars and len(bars) >= 2:
                    prev = bars[-2].close
                    L.log_info(self._log, "STARTUP", "PREV_CLOSE",
                               f"Previous close = {prev:.2f}")
                    return prev

                L.log_warning(self._log, "STARTUP", "PREV_CLOSE_WARN",
                              f"Attempt {attempt}: only {len(bars) if bars else 0} daily bars returned")
                if attempt == 1:
                    time.sleep(3)   # brief pause before retry

            except Exception as exc:
                L.log_error(self._log, "STARTUP", "PREV_CLOSE_ERROR",
                            f"Attempt {attempt}: {exc}")
                if attempt == 1:
                    time.sleep(3)

        return None

    def get_daily_atr(self, period: int = None) -> Optional[float]:
        """
        Fetch the last (period + buffer) daily bars via IBKR historical data
        and compute the mean true range as a proxy ATR.
        Returns None if data is unavailable or insufficient.
        """
        if period is None:
            period = config.ATR_PERIOD

        if not self._contract:
            return None

        duration_days = max(period * 2, 60)   # request extra to account for weekends/holidays
        L.log_info(self._log, "STARTUP", "ATR_REQUEST",
                   f"Fetching {duration_days}-calendar-day daily bars for {period}-day ATR...")
        try:
            bars = self._ib.reqHistoricalData(
                self._contract,
                endDateTime="",
                durationStr=f"{duration_days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if not bars or len(bars) < period:
                L.log_warning(self._log, "STARTUP", "ATR_WARN",
                              f"Only {len(bars) if bars else 0} daily bars returned "
                              f"(need {period}). ATR unavailable.")
                return None

            # True range: max(H-L, |H-prev_close|, |L-prev_close|).
            # Using H-L only understates range on gap days — fixed here.
            recent = bars[-(period + 1):]   # one extra bar for prev_close
            true_ranges = []
            for i in range(1, len(recent)):
                prev_c = recent[i - 1].close
                h, l = recent[i].high, recent[i].low
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                true_ranges.append(tr)
            true_ranges = true_ranges[-period:]   # keep exactly `period` values
            atr = sum(true_ranges) / len(true_ranges)

            L.log_info(self._log, "STARTUP", "ATR_OK",
                       f"{period}-day true-range ATR = {atr:.2f} pts "
                       f"(from {len(true_ranges)} bars)")
            return round(atr, 4)

        except Exception as exc:
            L.log_error(self._log, "STARTUP", "ATR_ERROR", str(exc))
            return None

    def get_trend_direction(self) -> Optional[str]:
        """
        Determine prior-session trend direction for the trend filter.
        Returns 'LONG' if prev close > prev-prev close,
                'SHORT' if prev close < prev-prev close,
                None if insufficient historical data.

        Uses the same 3-day daily bar request.  Retries once on HMDS errors.
        Shares infrastructure with get_prev_session_close() but is called
        separately so each can log and fail independently.
        """
        if not self._contract:
            return None

        L.log_info(self._log, "STARTUP", "TREND_DIR_REQUEST",
                   "Requesting last 2 daily closes for trend filter...")

        for attempt in (1, 2):
            try:
                bars = self._ib.reqHistoricalData(
                    self._contract,
                    endDateTime="",
                    durationStr="5 D",   # 5 calendar days → at least 3 trading days
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                )
                if bars and len(bars) >= 3:
                    prev_close      = bars[-2].close   # yesterday's close
                    prev_prev_close = bars[-3].close   # two days ago
                    direction = "LONG" if prev_close > prev_prev_close else "SHORT"
                    L.log_info(self._log, "STARTUP", "TREND_DIR",
                               f"prev_close={prev_close:.2f} prev2_close={prev_prev_close:.2f} "
                               f"→ trend_direction={direction}")
                    return direction

                L.log_warning(self._log, "STARTUP", "TREND_DIR_WARN",
                              f"Attempt {attempt}: only {len(bars) if bars else 0} daily bars "
                              "(need ≥ 3). Trend filter will be disabled today.")
                if attempt == 1:
                    time.sleep(3)

            except Exception as exc:
                L.log_error(self._log, "STARTUP", "TREND_DIR_ERROR",
                            f"Attempt {attempt}: {exc}")
                if attempt == 1:
                    time.sleep(3)

        return None

    # ── Bar subscription ───────────────────────────────────────────────────────

    def subscribe_bars(self) -> bool:
        if not self._contract:
            L.log_error(self._log, "STARTUP", "BARS_ERROR", "No contract to subscribe")
            return False

        L.log_info(self._log, "STARTUP", "BARS_SUBSCRIBE",
                   f"Subscribing 1-min bars for {self._contract.localSymbol}...")
        try:
            self._bars = self._ib.reqHistoricalData(
                self._contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting=config.BAR_SIZE,
                whatToShow="TRADES",
                useRTH=False,      # include pre/post but strategy ignores them
                formatDate=2,      # ib_insync returns datetime objects
                keepUpToDate=True,
            )
            self._bars.updateEvent += self._on_bar_update
            L.log_info(self._log, "STARTUP", "BARS_SUBSCRIBED",
                       f"Bar subscription active | historical bars loaded: {len(self._bars)}")
            return True

        except Exception as exc:
            L.log_error(self._log, "STARTUP", "BARS_ERROR", str(exc))
            return False

    def _on_bar_update(self, bars: BarDataList, has_new_bar: bool) -> None:
        """
        Fired by ib_insync each time the bar list updates.
        has_new_bar=True  → a completed bar was appended (act on it)
        has_new_bar=False → in-progress bar updated (ignore)
        """
        if not has_new_bar or not bars:
            return

        bar = bars[-1]
        receipt_time = datetime.now(self._tz)

        # Normalize timestamp to ET
        if isinstance(bar.date, datetime):
            bar_dt = bar.date.astimezone(self._tz)
        else:
            bar_dt = datetime.fromtimestamp(float(bar.date), tz=self._tz)

        # Delivery latency: how long after bar close did we receive it?
        bar_close_wall = bar_dt + timedelta(minutes=1)
        latency_ms = (receipt_time - bar_close_wall).total_seconds() * 1000

        L.log_debug(self._log, "DATA", "BAR_RECEIVED",
                    f"{bar_dt.strftime('%H:%M')} | "
                    f"O={bar.open:.2f} H={bar.high:.2f} "
                    f"L={bar.low:.2f} C={bar.close:.2f} V={bar.volume} | "
                    f"latency={latency_ms:+.0f}ms | "
                    f"receipt={receipt_time.strftime('%H:%M:%S.%f')[:-3]}")

        self._last_close = bar.close   # dashboard uses this for unrealized P&L
        if self.on_bar:
            self.on_bar(bar_dt, bar, receipt_time)

    # ── Order management ───────────────────────────────────────────────────────

    def submit_bracket(
        self,
        direction: str,
        stop_price: float,
        target_price: float,
    ) -> Optional[OrderBundle]:
        """
        Submit a market-entry bracket order (entry + stop + target).
        The three legs are linked via parent/child so IBKR manages OCA internally.
        """
        if not self._contract:
            L.log_error(self._log, "ORDER", "SUBMIT_ERROR", "No contract resolved")
            return None

        action = "BUY" if direction == "LONG" else "SELL"
        exit_action = "SELL" if direction == "LONG" else "BUY"
        submitted_at = datetime.now(self._tz)

        L.log_info(self._log, "ORDER", "BRACKET_SUBMIT",
                   f"{action} {config.CONTRACTS} {self._contract.localSymbol} MKT | "
                   f"stop={stop_price:.2f} target={target_price:.2f} | "
                   f"submitAt={submitted_at.strftime('%H:%M:%S.%f')[:-3]}")
        try:
            # Build bracket manually so entry is a market order, not limit
            entry_order = MarketOrder(action, config.CONTRACTS)
            entry_order.transmit = False

            entry_trade = self._ib.placeOrder(self._contract, entry_order)
            parent_id = entry_trade.order.orderId

            target_order = LimitOrder(exit_action, config.CONTRACTS, target_price)
            target_order.parentId = parent_id
            target_order.transmit = False

            stop_order = StopOrder(exit_action, config.CONTRACTS, stop_price)
            stop_order.parentId = parent_id
            stop_order.transmit = True   # transmits the whole bracket atomically

            target_trade = self._ib.placeOrder(self._contract, target_order)
            stop_trade = self._ib.placeOrder(self._contract, stop_order)

            bundle = OrderBundle(
                parent=entry_trade,
                stop=stop_trade,
                target=target_trade,
                submitted_at=submitted_at,
            )
            self._bundle = bundle

            L.log_info(self._log, "ORDER", "BRACKET_SUBMITTED",
                       f"parentId={parent_id} "
                       f"stopId={stop_trade.order.orderId} "
                       f"targetId={target_trade.order.orderId}")
            return bundle

        except Exception as exc:
            L.log_error(self._log, "ORDER", "SUBMIT_FAILED",
                        f"Bracket submission raised: {exc}\n{traceback.format_exc()}")
            return None

    def close_position_market(self, direction: str, reason: str = "MANUAL") -> bool:
        """Cancel all open orders then submit a market close."""
        if not self._contract:
            return False

        exit_action = "SELL" if direction == "LONG" else "BUY"
        L.log_info(self._log, "ORDER", "MARKET_CLOSE",
                   f"Closing {direction} with MKT {exit_action} | reason={reason}")

        # Cancel protective orders first to avoid double-closing
        self.cancel_all_orders()
        self._ib.sleep(0.5)

        try:
            close_order = MarketOrder(exit_action, config.CONTRACTS)
            close_order.transmit = True
            submitted_at = datetime.now(self._tz)
            trade = self._ib.placeOrder(self._contract, close_order)
            self._hard_close_order_id = trade.order.orderId
            self._pending_close_reason = reason  # remembered until fill arrives

            if self._bundle:
                self._bundle.close_reason = reason

            L.log_info(self._log, "ORDER", "CLOSE_SUBMITTED",
                       f"orderId={trade.order.orderId} "
                       f"submitAt={submitted_at.strftime('%H:%M:%S.%f')[:-3]}")
            return True

        except Exception as exc:
            L.log_error(self._log, "ORDER", "CLOSE_FAILED", str(exc))
            return False

    def cancel_all_orders(self) -> None:
        try:
            open_orders = self._ib.openOrders()
            for order in open_orders:
                self._ib.cancelOrder(order)
                L.log_info(self._log, "ORDER", "ORDER_CANCELLED",
                            f"Cancelled orderId={order.orderId}")
        except Exception as exc:
            L.log_error(self._log, "ORDER", "CANCEL_ALL_ERROR", str(exc))

    # ── Reconciliation ─────────────────────────────────────────────────────────

    def reconcile(self) -> dict:
        """
        Query IBKR for current positions and open orders.
        Returns a summary dict for the bot to act on.
        """
        positions = self._ib.positions()
        open_orders = self._ib.openOrders()

        mes_positions = [p for p in positions if p.contract.symbol == config.SYMBOL]

        summary = {
            "mes_position_count": len(mes_positions),
            "open_order_count": len(open_orders),
            "positions": [
                {
                    "localSymbol": p.contract.localSymbol,
                    "position": p.position,
                    "avgCost": p.avgCost,
                }
                for p in mes_positions
            ],
            "orders": [
                {
                    "orderId": o.orderId,
                    "action": o.action,
                    "orderType": o.orderType,
                    "qty": o.totalQuantity,
                }
                for o in open_orders
            ],
        }

        L.log_info(self._log, "STARTUP", "RECONCILE",
                   f"MES positions={len(mes_positions)} | open orders={len(open_orders)}")

        for p in summary["positions"]:
            L.log_info(self._log, "STARTUP", "POSITION_FOUND",
                       f"{p['localSymbol']} pos={p['position']} avgCost={p['avgCost']:.2f}")

        for o in summary["orders"]:
            L.log_info(self._log, "STARTUP", "ORDER_FOUND",
                       f"orderId={o['orderId']} {o['action']} {o['orderType']} qty={o['qty']}")

        return summary

    # ── IBKR event handlers ────────────────────────────────────────────────────

    def _on_order_status(self, trade: Trade) -> None:
        oid = trade.order.orderId
        status = trade.orderStatus.status
        filled = trade.orderStatus.filled
        avg = trade.orderStatus.avgFillPrice
        remaining = trade.orderStatus.remaining
        avg_str = f"{avg:.4f}" if avg is not None else "N/A"

        L.log_info(self._log, "ORDER", "ORDER_STATUS",
                   f"orderId={oid} status={status} "
                   f"filled={filled} avgFill={avg_str} "
                   f"remaining={remaining}")

        # Track protective order confirmation after entry fill
        if self._bundle and status in ("Submitted", "PreSubmitted"):
            if oid == self._bundle.stop_id and not self._bundle.stop_confirmed:
                self._bundle.stop_confirmed = True
                L.log_debug(self._log, "ORDER", "STOP_CONFIRMED",
                            f"Stop order {oid} confirmed live")

            if oid == self._bundle.target_id and not self._bundle.target_confirmed:
                self._bundle.target_confirmed = True
                L.log_debug(self._log, "ORDER", "TARGET_CONFIRMED",
                            f"Target order {oid} confirmed live")

            if (self._bundle.stop_confirmed and self._bundle.target_confirmed
                    and not self._bundle.protective_confirmed):
                self._bundle.protective_confirmed = True
                L.log_info(self._log, "ORDER", "PROTECTIVE_CONFIRMED",
                           "Both stop and target orders confirmed live")
                if self.on_protective_confirmed:
                    self.on_protective_confirmed()

    def _on_exec_details(self, trade: Trade, fill: Fill) -> None:
        oid = trade.order.orderId
        price = fill.execution.price
        qty = fill.execution.shares
        exec_time = fill.execution.time
        receipt = datetime.now(self._tz)

        L.log_info(self._log, "ORDER", "FILL_RECEIVED",
                   f"orderId={oid} price={price:.2f} qty={qty} "
                   f"execTime={exec_time} "
                   f"receiptAt={receipt.strftime('%H:%M:%S.%f')[:-3]}")

        if not self._bundle:
            # Could be a manual close or hard-close order
            if oid == self._hard_close_order_id and self.on_hard_close_fill:
                close_reason = self._pending_close_reason
                self._pending_close_reason = "HARD_CLOSE"   # reset for next trade
                self.on_hard_close_fill(oid, price, receipt, close_reason)
            return

        b = self._bundle

        if oid == b.parent_id:
            b.entry_filled_at = receipt
            b.entry_fill_price = price
            latency = _ms(b.submitted_at, receipt)
            L.log_info(self._log, "ORDER", "ENTRY_FILLED",
                       f"ENTRY filled @ {price:.2f} | submit→fill latency={latency}")
            if self.on_entry_fill:
                self.on_entry_fill(oid, price, receipt)

        elif oid == b.stop_id:
            b.close_filled_at = receipt
            b.close_fill_price = price
            b.close_reason = "STOP"
            L.log_info(self._log, "ORDER", "STOP_FILLED",
                       f"STOP filled @ {price:.2f}")
            self._cancel_leg(b.target, "target after stop fill")
            if self.on_exit_fill:
                self.on_exit_fill(oid, price, receipt, "STOP")

        elif oid == b.target_id:
            b.close_filled_at = receipt
            b.close_fill_price = price
            b.close_reason = "TARGET"
            L.log_info(self._log, "ORDER", "TARGET_FILLED",
                       f"TARGET filled @ {price:.2f}")
            self._cancel_leg(b.stop, "stop after target fill")
            if self.on_exit_fill:
                self.on_exit_fill(oid, price, receipt, "TARGET")

        else:
            # Unrecognised order — could be the hard-close market order
            if oid == self._hard_close_order_id and self.on_hard_close_fill:
                close_reason = self._pending_close_reason
                self._pending_close_reason = "HARD_CLOSE"   # reset for next trade
                b.close_filled_at = receipt
                b.close_fill_price = price
                b.close_reason = close_reason
                self.on_hard_close_fill(oid, price, receipt, close_reason)

    def _cancel_leg(self, trade: Trade, label: str) -> None:
        try:
            self._ib.cancelOrder(trade.order)
            L.log_info(self._log, "ORDER", "LEG_CANCELLED", f"Cancelled {label}")
        except Exception as exc:
            L.log_error(self._log, "ORDER", "LEG_CANCEL_ERROR",
                        f"Failed to cancel {label}: {exc}")

    def _on_error(self, req_id: int, code: int, msg: str, contract) -> None:
        # Benign informational codes IBKR always emits
        # 321 = Read-Only mode during Gateway startup handshake — transient, not a real rejection
        _BENIGN = {321, 2100, 2103, 2104, 2105, 2106, 2107, 2108, 2119, 2158}
        if code in _BENIGN:
            L.log_debug(self._log, "BROKER", "IBKR_INFO",
                        f"code={code} reqId={req_id} {msg}")
            return

        # Data / historical service errors — log as warning, do NOT halt the bot.
        # 162 = Historical Market Data Service error (common on paper / off-hours).
        # 366 = No historical data query found (benign on bar resubscribe).
        _DATA_WARN = {162, 366}
        if code in _DATA_WARN:
            L.log_warning(self._log, "BROKER", "DATA_WARNING",
                          f"reqId={req_id} code={code} msg={msg}")
            return

        # True order rejection codes — escalate to bot so it can HALT safely.
        # 321 removed: fires during Gateway startup handshake before any real order
        # exists (API in Read-Only mode transiently). Not a trade rejection.
        _REJECT = {103, 104, 105, 106, 107, 109, 110, 161, 201, 202, 322}
        if code in _REJECT:
            L.log_error(self._log, "ORDER", "ORDER_REJECTED",
                        f"reqId={req_id} code={code} msg={msg}")
            if self.on_order_rejected:
                self.on_order_rejected(req_id, f"code={code}: {msg}")
            return

        L.log_warning(self._log, "BROKER", "IBKR_ERROR",
                      f"reqId={req_id} code={code} msg={msg}")

    def _on_ib_disconnect(self) -> None:
        self._connected = False
        if self._intentional_disconnect:
            self._intentional_disconnect = False
            L.log_info(self._log, "BROKER", "DISCONNECT_INTENTIONAL",
                       "Disconnected from IBKR (intentional shutdown).")
            return
        L.log_critical(self._log, "BROKER", "DISCONNECTED",
                       "Lost connection to IBKR. Position may be open. "
                       "Scheduling async reconnect...")
        # Notify bot immediately (for logging / state awareness).
        if self.on_disconnected:
            self.on_disconnected()
        # Schedule the reconnect coroutine on the *running* ib_insync event loop.
        # Using ensure_future avoids "This event loop is already running" that occurs
        # when self._ib.connect() is called synchronously from inside an event callback.
        asyncio.ensure_future(self._async_reconnect())

    # ── Utilities ──────────────────────────────────────────────────────────────

    def clear_bundle(self) -> None:
        self._bundle = None
        self._hard_close_order_id = None

    def run(self) -> None:
        """Block and run the ib_insync event loop."""
        self._ib.run()

    def sleep(self, seconds: float) -> None:
        self._ib.sleep(seconds)

    @property
    def bundle(self) -> Optional[OrderBundle]:
        return self._bundle

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib.isConnected()

    @property
    def contract(self):
        return self._contract


# ── Internal helpers ───────────────────────────────────────────────────────────

def _ms(start: Optional[datetime], end: datetime) -> str:
    if start is None:
        return "N/A"
    return f"{(end - start).total_seconds() * 1000:.0f}ms"
