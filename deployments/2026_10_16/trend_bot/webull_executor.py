"""Webull futures executor — the third switch position.

STATUS: BUILT, NOT YET VALIDATED. Webull's OpenAPI has no paper environment,
so this class cannot be exercised end-to-end before its live canary day.
The flip protocol (README §switch) requires: flat book → venue=webull_live →
interlocks → ONE 1-contract canary trade watched end-to-end → normal size.

Credentials come from env only (~/.webull_env: WEBULL_APP_KEY,
WEBULL_APP_SECRET, TRENDBOT_LIVE_ACCOUNT). Nothing account-specific
lives in this file or in config.

API surface verified 2026-07-05 (SDK webull-openapi-python-sdk 2.0.7):
TradeClient order placement (order_v3), OrderType STOP_LOSS, TIF GTC,
futures symbols like MNQU6, category US_FUTURES.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

from deep_log import get_logger
from executor import ExecutorBase, Fill, check_live_interlocks

log = logging.getLogger("trend_bot.exec.webull")


class WebullLiveExecutor(ExecutorBase):
    venue = "webull_live"

    def __init__(self, wb_cfg: dict, stop_arm_timeout_s: float = 2.0,
                 alert=None, dlog=None, cfg_executor: dict | None = None):
        self.wb_cfg = wb_cfg
        self.stop_arm_timeout_s = stop_arm_timeout_s
        self.alert = alert or (lambda msg: None)
        self.dlog = dlog or get_logger()
        self.cfg_executor = cfg_executor or {}
        self.client = None
        self.account_id = None
        self._stop_orders: dict[str, dict] = {}   # sym -> {order_id, px, qty, symbol}

    # ---- lifecycle ----------------------------------------------------
    def connect(self) -> bool:
        if self.client is not None:
            return False
        app_key = os.environ.get("WEBULL_APP_KEY", "")
        app_secret = os.environ.get("WEBULL_APP_SECRET", "")
        if not (app_key and app_secret):
            raise RuntimeError("WEBULL_APP_KEY / WEBULL_APP_SECRET not in env")
        from webull.core.client import ApiClient          # deferred import
        from webull.trade.trade_client import TradeClient
        api = ApiClient(app_key=app_key, app_secret=app_secret, region_id="us")
        self.client = TradeClient(api)
        self.account_id = os.environ.get("TRENDBOT_LIVE_ACCOUNT", "")
        check_live_interlocks(self.cfg_executor, self.venue, self.account_id)
        self.dlog.log("connections", client="executor", venue=self.venue,
                      event="connect")
        log.info("webull executor connected")
        self.alert("⚠️ webull_live executor active — UNVALIDATED until canary "
                   "protocol completes")
        return False

    def disconnect(self):
        self.client = None

    def is_connected(self) -> bool:
        return self.client is not None

    def idle_sleep(self, seconds: float):
        time.sleep(seconds)

    # ---- venue helpers --------------------------------------------------
    def _symbol(self, contract) -> str:
        """IBKR front-contract localSymbol (e.g. MNQU6) is Webull's symbol."""
        return getattr(contract, "localSymbol", None) or str(contract)

    def _place(self, payload: dict) -> str:
        """Submit via order_v3; returns venue order id. Raw payload logged."""
        payload.setdefault("client_order_id", uuid.uuid4().hex)
        self.dlog.log("orders", venue=self.venue, event="submit",
                      payload=payload)
        resp = self.client.order_v3.place_order(self.account_id, payload)
        body = getattr(resp, "json", lambda: resp)()
        self.dlog.log("orders", venue=self.venue, event="submit_ack",
                      response=body)
        oid = (body or {}).get("client_order_id") or payload["client_order_id"]
        return oid

    def _order_detail(self, client_order_id: str) -> dict:
        resp = self.client.order_v3.get_order_detail(self.account_id,
                                                     client_order_id)
        return getattr(resp, "json", lambda: resp)() or {}

    def _wait_fill(self, client_order_id: str, timeout: float = 90.0) -> Fill:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            d = self._order_detail(client_order_id)
            status = (d.get("order_status") or d.get("status") or "").upper()
            if status in ("FILLED",):
                px = float(d.get("filled_price") or d.get("avg_fill_price") or 0)
                self.dlog.log("orders", venue=self.venue, event="filled",
                              order=client_order_id, detail=d)
                return Fill(px, None)
            if status in ("CANCELLED", "REJECTED", "FAILED"):
                raise RuntimeError(f"webull order {client_order_id}: {status}")
            time.sleep(2)
        raise TimeoutError(f"webull order {client_order_id} not filled in "
                           f"{timeout}s — MANUAL CHECK REQUIRED")

    # ---- interface -------------------------------------------------------
    def positions(self) -> dict:
        resp = self.client.account_v2.get_account_positions(self.account_id)
        body = getattr(resp, "json", lambda: resp)() or {}
        out = {}
        for p in body.get("positions", []) or []:
            out[p.get("symbol")] = float(p.get("quantity") or 0)
        self.dlog.log("account", venue=self.venue, event="positions", raw=body)
        return out

    def recover_stops(self) -> dict:
        resp = self.client.order_v3.get_open_orders(self.account_id)
        body = getattr(resp, "json", lambda: resp)() or {}
        found = {}
        for o in body.get("orders", []) or []:
            if (o.get("order_type") or "").upper() == "STOP_LOSS":
                sym_full = o.get("symbol", "")
                for sym, tsym in (("NQ", "MNQ"), ("ES", "MES"),
                                  ("YM", "MYM"), ("GC", "MGC")):
                    if sym_full.startswith(tsym):
                        found[sym] = {"order_id": o.get("client_order_id"),
                                      "px": float(o.get("stop_price") or 0),
                                      "qty": float(o.get("quantity") or 0),
                                      "symbol": sym_full}
        self._stop_orders = dict(found)
        self.dlog.log("account", venue=self.venue, event="recover_stops",
                      recovered=found)
        return found

    def enter_long_with_stop(self, sym, contract, qty, stop_px):
        symbol = self._symbol(contract)
        oid = self._place({"symbol": symbol, "instrument_type": "FUTURES",
                           "order_type": "MARKET", "side": "BUY",
                           "quantity": str(qty), "time_in_force": "DAY"})
        fill = self._wait_fill(oid)
        t_fill = time.monotonic()
        try:
            stop_id = self.place_stop(sym, contract, qty, stop_px)
            armed_ms = round((time.monotonic() - t_fill) * 1000)
            self.dlog.log("stops", venue=self.venue, event="armed", sym=sym,
                          stop_px=stop_px, order_id=stop_id,
                          fill_to_armed_ms=armed_ms)
            return fill, stop_id
        except Exception as e:
            self.alert(f"🚨 {sym}: STOP FAILED TO ARM on webull ({e}) — "
                       "flattening now")
            self.close_market(sym, contract, qty)
            raise

    def place_stop(self, sym, contract, qty, stop_px):
        symbol = self._symbol(contract)
        oid = self._place({"symbol": symbol, "instrument_type": "FUTURES",
                           "order_type": "STOP_LOSS", "side": "SELL",
                           "quantity": str(qty), "stop_price": f"{stop_px}",
                           "time_in_force": "GTC"})
        self._stop_orders[sym] = {"order_id": oid, "px": stop_px, "qty": qty,
                                  "symbol": symbol}
        return oid

    def update_stop(self, sym, new_px, reason="trail", hw=None, atr=None):
        cur = self._stop_orders.get(sym)
        if cur is None:
            raise RuntimeError(f"{sym}: no tracked stop order")
        old_px = cur["px"]
        # webull modify = cancel + replace (atomic replace not exposed)
        self.cancel_stop(sym)
        oid = self.place_stop(sym, type("C", (), {"localSymbol": cur["symbol"]}),
                              cur["qty"], new_px)
        self.dlog.log("stops", venue=self.venue, event="replaced", sym=sym,
                      old_px=old_px, new_px=new_px, reason=reason, hw=hw,
                      atr=atr, order_id=oid)

    def check_stop_filled(self, sym):
        cur = self._stop_orders.get(sym)
        if cur is None:
            return None
        d = self._order_detail(cur["order_id"])
        status = (d.get("order_status") or d.get("status") or "").upper()
        if status == "FILLED":
            px = float(d.get("filled_price") or d.get("avg_fill_price") or 0)
            del self._stop_orders[sym]
            self.dlog.log("stops", venue=self.venue, event="filled", sym=sym,
                          px=px, detail=d)
            return Fill(px, None)
        return None

    def cancel_stop(self, sym):
        cur = self._stop_orders.pop(sym, None)
        if cur is not None:
            resp = self.client.order_v3.cancel_order(self.account_id,
                                                     cur["order_id"])
            self.dlog.log("stops", venue=self.venue, event="cancelled",
                          sym=sym, order_id=cur["order_id"],
                          response=getattr(resp, "json", lambda: resp)())

    def close_market(self, sym, contract, qty):
        self.cancel_stop(sym)
        symbol = self._symbol(contract)
        oid = self._place({"symbol": symbol, "instrument_type": "FUTURES",
                           "order_type": "MARKET", "side": "SELL",
                           "quantity": str(qty), "time_in_force": "DAY"})
        return self._wait_fill(oid)
