"""Realistic MockTradeClient that simulates KNOWN Webull bugs/quirks.

Lessons from 2026-05-20 and 2026-05-21 incidents:
  - Order status endpoint can lag the actual fill state by N seconds
  - cancel_order returns OAUTH_OPENAPI_ORDER_CAN_NOT_BE_CANCEL when filling
  - replace_order requires client_combo_order_id (not just client_order_id)
  - get_account_position default page_size=10
  - Position aggregation lags fill by 1-2 seconds

This mock RECREATES these conditions deterministically so tests for the new
position-based design can verify it handles them.

NOT a perfect replica of Webull — but captures the failure modes that actually
hit us. Tests using this mock are higher-confidence than tests using the
idealized MockTradeClient.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RealisticMockResp:
    status_code: int = 200
    text: str = "{}"

    def json(self) -> dict:
        return json.loads(self.text) if self.text else {}


@dataclass
class _Order:
    """Server-side state of an order in the realistic mock broker."""
    client_order_id: str
    webull_order_id: str  # mimicking Webull's internal id format
    limit_price: float
    quantity: int
    short_strike: float
    long_strike: float
    expiry: str

    # State tracked by the mock "broker simulation"
    place_time: float            # monotonic at place
    fills_at: Optional[float]    # when broker considers it filled
    filled_price: Optional[float]  # actual fill price (with improvement)
    cancelled_at: Optional[float]

    # Behavior knobs — set by test to recreate specific scenarios
    fill_delay_s: float = 0.5
    """How long after place_order before broker fills the order."""

    status_lag_s: float = 30.0
    """How long status endpoint shows WORKING/UNKNOWN AFTER fill before updating."""

    position_lag_s: float = 2.0
    """How long after fill before positions endpoint shows the new legs."""

    def fill_state(self, now: float) -> str:
        """What broker's internal state actually is RIGHT NOW.
        Different from what status endpoint reports (that has lag)."""
        if self.cancelled_at is not None and self.cancelled_at < (self.fills_at or 1e18):
            return "CANCELLED"
        if self.fills_at is not None and now >= self.fills_at:
            return "FILLED"
        return "WORKING"

    def status_reported(self, now: float) -> str:
        """What get_order_detail would return (with lag)."""
        actual = self.fill_state(now)
        if actual == "FILLED":
            time_since_fill = now - self.fills_at
            if time_since_fill < self.status_lag_s:
                # Bug: status endpoint shows WORKING even though filled
                return "WORKING"
            return "FILLED"
        return actual


class RealisticMockTradeClient:
    """Mock Webull trade client that simulates real bugs."""

    def __init__(self, monotonic_fn=None):
        import time
        self.monotonic = monotonic_fn or time.monotonic
        self.orders: dict[str, _Order] = {}
        self.holdings: list[dict] = []

        # Knobs — adjustable per test
        self.fill_delay_default = 0.5  # 500ms after place
        self.status_lag_default = 30.0  # status endpoint lag after fill
        self.position_lag_default = 2.0  # position endpoint lag after fill
        self.cancel_when_filled_returns_417 = True
        self.replace_requires_combo_id = True
        self.position_page_size_default = 10

        # Endpoints
        self.order_v3 = self._OrderV3(self)
        self.order = self._Order(self)
        self.account = self._Account(self)

    def _now(self) -> float:
        return self.monotonic()

    def _generate_holdings_for_filled_orders(self):
        """Compute current holdings based on which orders are 'visibly filled'
        per position endpoint (accounting for position_lag)."""
        now = self._now()
        # Aggregate fills that should be visible in positions endpoint
        visible = []
        for cid, order in self.orders.items():
            if order.fills_at is None:
                continue
            time_since_fill = now - order.fills_at
            if time_since_fill < order.position_lag_s:
                continue  # not yet visible
            if order.cancelled_at is not None and order.cancelled_at < order.fills_at:
                continue
            visible.append(order)

        # Build holdings list (one entry per leg, per order)
        holdings = []
        for o in visible:
            holdings.append({
                "instrument_id": f"IID-S-{o.client_order_id[:6]}",
                "symbol": "SPXW",
                "instrument_type": "OPTION",
                "qty": str(-o.quantity),
                "unit_cost": f"{(o.filled_price + 1.0):.2f}",  # short leg cost approx
                "last_price": "0.50",
            })
            holdings.append({
                "instrument_id": f"IID-L-{o.client_order_id[:6]}",
                "symbol": "SPXW",
                "instrument_type": "OPTION",
                "qty": str(o.quantity),
                "unit_cost": f"{1.0:.2f}",  # long leg cost approx
                "last_price": "0.20",
            })
        return holdings

    # ── Inner endpoint classes ─────────────────────────────────────────

    class _OrderV3:
        def __init__(self, outer):
            self.outer = outer

        def place_order(self, account_id, new_orders, client_combo_order_id=None):
            o = new_orders[0]
            cid = o["client_order_id"]
            now = self.outer._now()
            order = _Order(
                client_order_id=cid,
                webull_order_id=uuid.uuid4().hex.upper()[:26],
                limit_price=float(o["limit_price"]),
                quantity=int(o["quantity"]),
                short_strike=float(o["legs"][0]["strike_price"]),
                long_strike=float(o["legs"][1]["strike_price"]),
                expiry=o["legs"][0]["option_expire_date"],
                place_time=now,
                fills_at=now + self.outer.fill_delay_default,
                filled_price=float(o["limit_price"]) + 0.10,  # improvement
                cancelled_at=None,
                fill_delay_s=self.outer.fill_delay_default,
                status_lag_s=self.outer.status_lag_default,
                position_lag_s=self.outer.position_lag_default,
            )
            self.outer.orders[cid] = order
            body = {"order_id": order.webull_order_id, "client_order_id": cid}
            return RealisticMockResp(200, json.dumps(body))

        def replace_order(self, account_id, modify_orders, client_combo_order_id=None):
            if self.outer.replace_requires_combo_id and not client_combo_order_id:
                return RealisticMockResp(
                    417, json.dumps({
                        "error_code": "OAUTH_OPENAPI_PARAM_ERR",
                        "message": "Parameter error, invalid order_id",
                    })
                )
            # If combo_id provided, attempt modify
            # (Real Webull may still fail; for now mock accepts)
            return RealisticMockResp(200, json.dumps({"order_id": "modified"}))

        def cancel_order(self, account_id, client_order_id):
            order = self.outer.orders.get(client_order_id)
            if order is None:
                return RealisticMockResp(
                    404, json.dumps({
                        "error_code": "OAUTH_OPENAPI_ORDER_NOT_FOUND",
                        "message": "Order not found",
                    })
                )
            now = self.outer._now()
            actual = order.fill_state(now)
            if actual == "FILLED" and self.outer.cancel_when_filled_returns_417:
                # The exact behavior that bit us 2026-05-21
                return RealisticMockResp(
                    417, json.dumps({
                        "error_code": "OAUTH_OPENAPI_ORDER_CAN_NOT_BE_CANCEL",
                        "message": "Order can not be canceled",
                    })
                )
            if actual == "WORKING":
                order.cancelled_at = now
                return RealisticMockResp(200, "{}")
            # Already cancelled
            return RealisticMockResp(200, "{}")

        def get_order_detail(self, account_id, client_order_id):
            order = self.outer.orders.get(client_order_id)
            if order is None:
                return RealisticMockResp(
                    404, json.dumps({"error_code": "OAUTH_OPENAPI_ORDER_NOT_FOUND"})
                )
            now = self.outer._now()
            reported = order.status_reported(now)
            body = {
                "data": [{
                    "client_order_id": client_order_id,
                    "order_id": order.webull_order_id,
                    "status": reported,
                    "total_quantity": str(order.quantity),
                    "filled_quantity": str(order.quantity) if reported == "FILLED" else "0",
                    "filled_price": f"{order.filled_price:.2f}" if reported == "FILLED" else None,
                    "limit_price": f"{order.limit_price:.2f}",
                }]
            }
            return RealisticMockResp(200, json.dumps(body))

        def preview_order(self, account_id, preview_orders):
            return RealisticMockResp(200, "{}")

    class _Order:
        def __init__(self, outer):
            self.outer = outer

        def list_open_orders(self, account_id):
            now = self.outer._now()
            working = [o for o in self.outer.orders.values()
                       if o.fill_state(now) == "WORKING"]
            return RealisticMockResp(200, json.dumps({
                "pageSize": len(working),
                "orders": [{"client_order_id": o.client_order_id} for o in working],
            }))

        def list_today_orders(self, account_id, page_size=50):
            # Provides iid → strike map via order items
            items = []
            for o in self.outer.orders.values():
                items.append({"items": [
                    {"instrument_id": f"IID-S-{o.client_order_id[:6]}",
                     "strike_price": f"{o.short_strike:.2f}"},
                    {"instrument_id": f"IID-L-{o.client_order_id[:6]}",
                     "strike_price": f"{o.long_strike:.2f}"},
                ]})
            return RealisticMockResp(200, json.dumps({"orders": items}))

        def cancel_order(self, account_id, client_order_id):
            return self.outer.order_v3.cancel_order(account_id, client_order_id)

    class _Account:
        def __init__(self, outer):
            self.outer = outer

        def get_account_position(self, account_id, page_size=None,
                                 last_instrument_id=None):
            # Simulate rate limit if configured
            if hasattr(self.outer, "next_position_429_count") and self.outer.next_position_429_count > 0:
                self.outer.next_position_429_count -= 1
                return RealisticMockResp(
                    429, json.dumps({"error_code": "TOO_MANY_REQUESTS", "message": "to many requests"})
                )
            # Honor SDK default page_size=10 if not specified
            effective_page_size = (page_size if page_size is not None
                                   else self.outer.position_page_size_default)
            holdings = self.outer._generate_holdings_for_filled_orders()
            # Simulate pagination
            start = 0
            if last_instrument_id:
                for i, h in enumerate(holdings):
                    if h["instrument_id"] == last_instrument_id:
                        start = i + 1
                        break
            page = holdings[start:start + effective_page_size]
            has_next = (start + effective_page_size) < len(holdings)
            return RealisticMockResp(200, json.dumps({
                "holdings": page,
                "has_next": has_next,
            }))
