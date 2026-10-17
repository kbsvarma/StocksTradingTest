"""Shared pytest fixtures + mocks.

CRITICAL: every test sets WEBULL_DRY_RUN=0 explicitly to avoid the dry-run gate
masking real code paths. Tests use MockTradeClient instead — no real Webull
network calls, ever.
"""
from __future__ import annotations

import os
import sys
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

# Ensure repo root on path so `from webull_bot...` works when pytest invoked
# from the tests directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@pytest.fixture(autouse=True)
def _disable_dry_run(monkeypatch):
    """Force live-code paths in all tests. Override per-test if needed."""
    monkeypatch.delenv("WEBULL_DRY_RUN", raising=False)
    # Bypass the SSM active-instance check (returns True from test env)
    monkeypatch.setattr(
        "webull_bot.active_instance.is_active_instance",
        lambda: True, raising=False,
    )


@pytest.fixture(autouse=True)
def _disable_alerts(monkeypatch):
    """Stub Telegram. Tests assert via recorded calls if needed."""
    sent = []
    def _stub(text, *a, **kw):
        sent.append(text)
        return True
    import webull_bot.alerts as _alerts
    monkeypatch.setattr(_alerts, "send_alert", _stub, raising=False)
    yield sent


# ── Mock HTTP response ────────────────────────────────────────────────────────

@dataclass
class MockResp:
    status_code: int = 200
    text: str = "{}"

    def json(self) -> dict:
        return json.loads(self.text) if self.text else {}


# ── Programmable order lifecycle helpers ─────────────────────────────────────

@dataclass
class FakeOrder:
    """Server-side state of an order in the mock broker."""
    client_order_id: str
    limit_price: float
    quantity: int
    short_strike: float
    long_strike: float
    status: str = "WORKING"           # WORKING | FILLED | CANCELLED | REJECTED
    fill_price: Optional[float] = None
    fill_at_replace_attempt: Optional[int] = None  # fill on Nth replace
    replace_count: int = 0
    cancel_should_fail_with: Optional[int] = None  # e.g. 417
    place_calls: int = 0


class MockTradeClient:
    """Mimics enough of webull.TradeClient for ExecutionEngine tests.

    Each instance is stateful: place_order creates orders, replace_order
    mutates them, get_order_detail reports current status, cancel_order
    drops to CANCELLED (or fails per cancel_should_fail_with).

    Position tracking is separate: `holdings` is a list of dicts the engine
    sees via account.get_account_position().
    """

    def __init__(self):
        self.orders: dict[str, FakeOrder] = {}
        self.holdings: list[dict] = []
        self.today_strike_map: dict[str, float] = {}   # iid → strike
        # Knobs — tests set these to control behavior
        self.next_place_http: int = 200
        self.next_cancel_http: int = 200
        self.next_replace_http: int = 200
        self.next_replace_fail_count: int = 0  # fail N replaces then succeed
        self.fill_on_walk_step: Optional[int] = None  # 0=initial, 1=after 1 walk
        self.poll_calls_before_fill: int = 0
        self._poll_calls: dict[str, int] = {}

        # Endpoints
        self.order_v3 = self._OrderV3(self)
        self.order = self._Order(self)
        self.account = self._Account(self)

    # ── inner endpoint objects ─────────────────────────────────────────
    class _OrderV3:
        def __init__(self, outer):
            self.outer = outer

        def place_order(self, account_id, new_orders):
            o = new_orders[0]
            cid = o["client_order_id"]
            self.outer.orders[cid] = FakeOrder(
                client_order_id=cid,
                limit_price=float(o["limit_price"]),
                quantity=int(o["quantity"]),
                short_strike=float(o["legs"][0]["strike_price"]),
                long_strike=float(o["legs"][1]["strike_price"]),
            )
            return MockResp(self.outer.next_place_http,
                            json.dumps({"order_id": cid}))

        def replace_order(self, account_id, modify_orders):
            mo = modify_orders[0]
            cid = mo["client_order_id"]
            order = self.outer.orders.get(cid)
            if order is None:
                return MockResp(404, json.dumps({"error_code": "NOT_FOUND"}))
            if self.outer.next_replace_fail_count > 0:
                self.outer.next_replace_fail_count -= 1
                return MockResp(417, json.dumps({
                    "error_code": "INVALID_PARAMETER",
                    "message": "cannot modify"}))
            order.limit_price = float(mo["limit_price"])
            order.replace_count += 1
            return MockResp(self.outer.next_replace_http,
                            json.dumps({"order_id": cid}))

        def cancel_order(self, account_id, client_order_id):
            order = self.outer.orders.get(client_order_id)
            if order is None:
                return MockResp(404, json.dumps({"error_code": "NOT_FOUND"}))
            if order.cancel_should_fail_with is not None:
                code = order.cancel_should_fail_with
                order.cancel_should_fail_with = None  # one-shot
                return MockResp(code, json.dumps({
                    "error_code": "INVALID_PARAMETER",
                    "message": "cancel failed"}))
            if order.status == "WORKING":
                order.status = "CANCELLED"
            return MockResp(self.outer.next_cancel_http, "{}")

        def get_order_detail(self, account_id, client_order_id):
            order = self.outer.orders.get(client_order_id)
            if order is None:
                return MockResp(404, json.dumps({"error_code": "NOT_FOUND"}))
            self.outer._poll_calls[client_order_id] = (
                self.outer._poll_calls.get(client_order_id, 0) + 1)
            # Honor "fill after N polls" trigger
            if (order.status == "WORKING"
                and self.outer.poll_calls_before_fill > 0
                and self.outer._poll_calls[client_order_id]
                    >= self.outer.poll_calls_before_fill):
                order.status = "FILLED"
                order.fill_price = order.limit_price
            # Honor "fill at walk step N" trigger
            if (order.status == "WORKING"
                and self.outer.fill_on_walk_step is not None
                and order.replace_count >= self.outer.fill_on_walk_step):
                order.status = "FILLED"
                order.fill_price = order.limit_price
            body = {
                "status": order.status,
                "avgFilledPrice": order.fill_price,
            }
            return MockResp(200, json.dumps(body))

        def preview_order(self, account_id, preview_orders):
            return MockResp(200, "{}")

    class _Order:
        def __init__(self, outer):
            self.outer = outer

        def list_open_orders(self, account_id):
            working = [o for o in self.outer.orders.values()
                       if o.status == "WORKING"]
            return MockResp(200, json.dumps({
                "pageSize": len(working),
                "orders": [{"client_order_id": o.client_order_id}
                           for o in working],
            }))

        def list_today_orders(self, account_id, page_size=50):
            items = []
            for o in self.outer.orders.values():
                items.append({"items": [
                    {"instrument_id": f"IID-S-{o.client_order_id[:6]}",
                     "strike_price": str(o.short_strike)},
                    {"instrument_id": f"IID-L-{o.client_order_id[:6]}",
                     "strike_price": str(o.long_strike)},
                ]})
            # also add any caller-injected strike map
            for iid, strike in self.outer.today_strike_map.items():
                items.append({"items": [{
                    "instrument_id": iid, "strike_price": str(strike)
                }]})
            return MockResp(200, json.dumps({"orders": items}))

        def cancel_order(self, account_id, client_order_id):
            return self.outer.order_v3.cancel_order(account_id, client_order_id)

    class _Account:
        def __init__(self, outer):
            self.outer = outer

        def get_account_position(self, account_id, last_instrument_id=None):
            return MockResp(200, json.dumps({
                "holdings": self.outer.holdings,
                "has_next": False,
            }))


@pytest.fixture
def mock_tc():
    return MockTradeClient()


@pytest.fixture
def engine(mock_tc):
    from webull_bot.execution import ExecutionEngine
    return ExecutionEngine(mock_tc, account_id="TEST-ACCT")


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Make time.sleep advance a fake monotonic clock instead of waiting.

    Without monotonic-patching, walk-down loops with retry_wait_seconds=60
    spin at full CPU for 60 real seconds because the deadline check uses
    time.monotonic(). Patch both so test sleeps move virtual time forward
    while real-time stays at zero.
    """
    import time as _t
    state = {"now": 1000.0}
    real_monotonic = _t.monotonic

    def _sleep(secs=0, *_a, **_kw):
        try:
            state["now"] += float(secs)
        except (TypeError, ValueError):
            pass

    def _monotonic():
        return state["now"]

    monkeypatch.setattr(_t, "sleep", _sleep)
    monkeypatch.setattr(_t, "monotonic", _monotonic)
