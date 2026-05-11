"""Webull execution engine — place, monitor, and cancel bull put spreads via order_v3.

Uses TradeClient.order_v3 (OrderOperationV3) which supports US options.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Optional

from webull.trade.trade_client import TradeClient


@dataclass
class FillResult:
    filled: bool
    client_order_id: str
    fill_price: float        # net credit actually received
    status: str
    detail: str


@dataclass
class OrderStatus:
    client_order_id: str
    status: str              # PENDING, WORKING, FILLED, CANCELLED, REJECTED
    fill_price: Optional[float]
    raw: dict


class ExecutionEngine:
    def __init__(self, trade_client: TradeClient, account_id: str):
        self.trade = trade_client
        self.account_id = account_id

    def preview_spread(
        self,
        symbol: str,
        expiry: str,           # YYYY-MM-DD
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,    # net credit limit (e.g., 2.00)
    ) -> dict:
        """Preview a bull put spread order. Returns raw API response dict."""
        order = self._build_order(symbol, expiry, short_strike, long_strike, quantity, limit_price)
        resp = self.trade.order_v3.preview_order(
            account_id=self.account_id,
            preview_orders=[order],
        )
        return {"status_code": resp.status_code, "body": resp.text}

    def place_spread(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        max_retries: int = 5,
        retry_price_step: float = 0.05,
        retry_wait_seconds: int = 60,
        fill_timeout_seconds: int = 300,
    ) -> FillResult:
        """Place a bull put spread and wait for fill.

        Retries by improving the limit price (lowering the credit we demand)
        if not filled within retry_wait_seconds.
        """
        client_order_id = uuid.uuid4().hex
        current_limit = limit_price

        for attempt in range(max_retries + 1):
            order = self._build_order(
                symbol, expiry, short_strike, long_strike, quantity, current_limit,
                client_order_id=client_order_id,
            )

            resp = self.trade.order_v3.place_order(
                account_id=self.account_id,
                new_orders=[order],
            )

            if resp.status_code not in (200, 201):
                return FillResult(
                    filled=False,
                    client_order_id=client_order_id,
                    fill_price=0.0,
                    status="REJECTED",
                    detail=f"HTTP {resp.status_code}: {resp.text[:500]}",
                )

            # Poll for fill
            deadline = time.monotonic() + retry_wait_seconds
            while time.monotonic() < deadline:
                time.sleep(5)
                status = self.get_order_status(client_order_id)
                if status.status == "FILLED":
                    price = status.fill_price or current_limit
                    return FillResult(
                        filled=True,
                        client_order_id=client_order_id,
                        fill_price=price,
                        status="FILLED",
                        detail=f"filled at {price} on attempt {attempt + 1}",
                    )
                if status.status in ("CANCELLED", "REJECTED"):
                    return FillResult(
                        filled=False,
                        client_order_id=client_order_id,
                        fill_price=0.0,
                        status=status.status,
                        detail=f"order {status.status} on attempt {attempt + 1}",
                    )

            if attempt < max_retries:
                # Cancel current order and retry with lower credit demand
                self.cancel_order(client_order_id)
                time.sleep(2)
                client_order_id = uuid.uuid4().hex
                current_limit = round(current_limit - retry_price_step, 2)
                if current_limit <= 0:
                    break

        return FillResult(
            filled=False,
            client_order_id=client_order_id,
            fill_price=0.0,
            status="TIMEOUT",
            detail=f"not filled after {max_retries + 1} attempts",
        )

    def close_spread_market(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        entry_credit: float,
    ) -> FillResult:
        """Buy back the spread at market to close (debit order).

        For a credit spread, closing means buying back:
        - BUY the short put (was sold to open)
        - SELL the long put (was bought to open)
        We submit as a limit order at a debit of entry_credit * 3 to guarantee fill.
        """
        client_order_id = uuid.uuid4().hex
        # Max debit we're willing to pay = 2x credit (already at stop) + buffer
        max_debit = round(entry_credit * 2.5, 2)

        order = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "BUY",
            "order_type": "LIMIT",
            "limit_price": str(max_debit),
            "quantity": str(quantity),
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "position_intent": "BUY_TO_CLOSE",
            "legs": [
                {
                    "side": "BUY",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": str(int(short_strike)) if short_strike == int(short_strike) else str(short_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
                {
                    "side": "SELL",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": str(int(long_strike)) if long_strike == int(long_strike) else str(long_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
            ],
        }

        resp = self.trade.order_v3.place_order(
            account_id=self.account_id,
            new_orders=[order],
        )

        if resp.status_code not in (200, 201):
            return FillResult(
                filled=False,
                client_order_id=client_order_id,
                fill_price=0.0,
                status="REJECTED",
                detail=f"close HTTP {resp.status_code}: {resp.text[:500]}",
            )

        # Poll up to 2 minutes for close fill
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(5)
            status = self.get_order_status(client_order_id)
            if status.status == "FILLED":
                return FillResult(
                    filled=True,
                    client_order_id=client_order_id,
                    fill_price=status.fill_price or max_debit,
                    status="FILLED",
                    detail="stop-loss close filled",
                )
            if status.status in ("CANCELLED", "REJECTED"):
                break

        return FillResult(
            filled=False,
            client_order_id=client_order_id,
            fill_price=0.0,
            status="TIMEOUT",
            detail="stop-loss close not confirmed within 2 min",
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            resp = self.trade.order_v3.cancel_order(
                account_id=self.account_id,
                client_order_id=client_order_id,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def get_order_status(self, client_order_id: str) -> OrderStatus:
        try:
            resp = self.trade.order_v3.get_order_detail(
                account_id=self.account_id,
                client_order_id=client_order_id,
            )
            if resp.status_code != 200:
                return OrderStatus(client_order_id, "UNKNOWN", None, {})

            import json
            body = json.loads(resp.text)
            # Webull order detail response structure
            data = body.get("data", body)
            if isinstance(data, list):
                data = data[0] if data else {}

            raw_status = str(data.get("status", data.get("orderStatus", "UNKNOWN"))).upper()
            status = _normalize_status(raw_status)

            fill_price = None
            avg_price = data.get("avgFilledPrice") or data.get("filledPrice")
            if avg_price:
                try:
                    fill_price = float(avg_price)
                except (ValueError, TypeError):
                    pass

            return OrderStatus(client_order_id, status, fill_price, data)
        except Exception as exc:
            return OrderStatus(client_order_id, "UNKNOWN", None, {"error": str(exc)})

    @staticmethod
    def _build_order(
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict:
        return ExecutionEngine._build_order_dict(
            symbol, expiry, short_strike, long_strike, quantity, limit_price, client_order_id
        )

    @staticmethod
    def _build_order_dict(
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict:
        def fmt_strike(s: float) -> str:
            return str(int(s)) if s == int(s) else str(s)

        return {
            "client_order_id": client_order_id or uuid.uuid4().hex,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "SELL",
            "order_type": "LIMIT",
            "limit_price": str(limit_price),
            "quantity": str(quantity),
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "position_intent": "SELL_TO_OPEN",
            "legs": [
                {
                    "side": "SELL",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(short_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
                {
                    "side": "BUY",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(long_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
            ],
        }


def _normalize_status(raw: str) -> str:
    mapping = {
        "FILLED": "FILLED",
        "ALL_FILLED": "FILLED",
        "PARTIALLY_FILLED": "WORKING",
        "WORKING": "WORKING",
        "PENDING": "WORKING",
        "SUBMITTED": "WORKING",
        "PENDING_SUBMIT": "WORKING",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "REJECTED": "REJECTED",
        "INACTIVE": "CANCELLED",
    }
    return mapping.get(raw, "UNKNOWN")
