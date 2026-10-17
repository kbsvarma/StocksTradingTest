"""
Kalshi Trade API v2 client with RSA-PSS request signing.

Auth scheme (verified against docs.kalshi.com):
  Headers:
    KALSHI-ACCESS-KEY        = API key id
    KALSHI-ACCESS-TIMESTAMP  = current time in ms since epoch (string)
    KALSHI-ACCESS-SIGNATURE  = base64( RSA-PSS-SHA256( timestamp + METHOD + path ) )
  The signed `path` is the request path from the API root WITHOUT the query
  string, e.g. "/trade-api/v2/portfolio/balance".

Safety:
  - `dry_run=True` blocks every non-GET request (orders, cancels) and logs the
    intended payload instead of sending it. The live order path only arms when
    the caller explicitly constructs the client with dry_run=False.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from urllib3.util import Timeout as HTTPTimeout
import io_budget
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

log = logging.getLogger("kalshi.client")
GET_BUDGET_SECONDS = 3.0
GET_ATTEMPTS = 2
GET_RETRY_SECONDS = .25

# Live host confirmed reachable in recon. Demo host kept for reference.
LIVE_BASE = "https://api.elections.kalshi.com"
DEMO_BASE = "https://demo-api.kalshi.co"
API_PREFIX = "/trade-api/v2"


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
@dataclass
class Credentials:
    key_id: str
    private_key: rsa.RSAPrivateKey

    @classmethod
    def load(cls, env_path: str = "~/.kalshi_env") -> "Credentials":
        """
        Load credentials from a mode-600 env file (mirrors ~/.webull_env):

            KALSHI_KEY_ID=<your api key id>
            KALSHI_PRIVATE_KEY_PATH=~/.kalshi_private_key.pem

        Falls back to KALSHI_KEY_ID / KALSHI_PRIVATE_KEY_PATH process env vars.
        """
        vals: dict[str, str] = {}
        p = Path(os.path.expanduser(env_path))
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")

        key_id = vals.get("KALSHI_KEY_ID") or os.environ.get("KALSHI_KEY_ID")
        key_path = (
            vals.get("KALSHI_PRIVATE_KEY_PATH")
            or os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        )
        if not key_id or not key_path:
            raise RuntimeError(
                f"Missing credentials. Create {env_path} with KALSHI_KEY_ID and "
                "KALSHI_PRIVATE_KEY_PATH (see README). Never paste the private key "
                "into chat or commit it."
            )

        key_file = Path(os.path.expanduser(key_path))
        if not key_file.exists():
            raise RuntimeError(f"Private key file not found: {key_file}")
        private_key = serialization.load_pem_private_key(
            key_file.read_bytes(), password=None
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise RuntimeError("Private key is not an RSA key.")
        return cls(key_id=key_id, private_key=private_key)


def _sign(private_key: rsa.RSAPrivateKey, message: str) -> str:
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class KalshiClient:
    def __init__(
        self,
        creds: Optional[Credentials] = None,
        base_url: str = LIVE_BASE,
        dry_run: bool = True,
        timeout: float = 15.0,
    ):
        self.creds = creds
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "kalshi-mm/0.1"})

    # -- low level ---------------------------------------------------------- #
    def _headers(self, method: str, path: str) -> dict[str, str]:
        """Signed auth headers. `path` must start with API_PREFIX and exclude query."""
        if self.creds is None:
            raise RuntimeError("Authenticated request requires credentials.")
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path
        return {
            "KALSHI-ACCESS-KEY": self.creds.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": _sign(self.creds.private_key, msg),
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        signed: bool = True,
    ) -> Any:
        if method.upper() == "GET":
            with io_budget.get_budget(GET_BUDGET_SECONDS):
                return self._request_impl(method, path, params, body, signed)
        return self._request_impl(method, path, params, body, signed)

    def _request_impl(self, method, path, params=None, body=None, signed=True):
        method = method.upper()
        full_path = API_PREFIX + path  # path passed in is relative to API prefix

        # Safety gate: dry_run blocks anything that mutates state.
        if self.dry_run and method != "GET":
            log.warning(
                "[DRY_RUN] suppressed %s %s body=%s", method, full_path, body
            )
            return {"dry_run": True, "method": method, "path": full_path, "body": body}

        url = self.base_url + full_path
        query = f"?{urlencode(params)}" if params else ""
        body_data = json.dumps(body) if body is not None else None

        # Transient-error resilience. GETs are safe to retry; mutating calls
        # (POST/DELETE) are NOT auto-retried on connection errors — a reset after
        # the server received the order would double-act. The caller's loop
        # reconciles against API state next cycle, so a single failed write is safe.
        retriable_get = method == "GET"
        attempts = GET_ATTEMPTS if retriable_get else 1
        for attempt in range(attempts):
            # headers must be re-signed each attempt (timestamp changes)
            headers = self._headers(method, full_path) if signed else None
            request_timeout = self.timeout
            if retriable_get:
                left = io_budget.timeout(self.timeout)
                request_timeout = HTTPTimeout(total=left, connect=min(1., left), read=min(1., left))
            try:
                resp = self.session.request(
                    method, url + query, headers=headers,
                    data=body_data, timeout=request_timeout,
                )
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt < attempts - 1:
                    io_budget.retry_sleep(GET_RETRY_SECONDS)
                    continue
                raise KalshiAPIError(0, f"network: {e}", method, full_path)
            # retry transient server/rate errors on GET
            if retriable_get:
                io_budget.timeout(self.timeout)  # Discard a response arriving past its deadline.
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                io_budget.retry_sleep(GET_RETRY_SECONDS)
                continue
            if resp.status_code >= 400:
                raise KalshiAPIError(resp.status_code, resp.text, method, full_path)
            if not resp.content:
                return None
            return resp.json()

    # -- public (unsigned) market data -------------------------------------- #
    def exchange_status(self) -> dict:
        return self._request("GET", "/exchange/status", signed=False)

    def get_markets(self, **params) -> dict:
        return self._request("GET", "/markets", params=params, signed=False)

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}", signed=False)

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        return self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}, signed=False
        )

    # -- authenticated reads ------------------------------------------------- #
    def get_balance(self) -> dict:
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, **params) -> dict:
        return self._request("GET", "/portfolio/positions", params=params)

    def get_orders(self, **params) -> dict:
        return self._request("GET", "/portfolio/orders", params=params)

    def get_fills(self, **params) -> dict:
        return self._request("GET", "/portfolio/fills", params=params)

    # -- authenticated writes (gated by dry_run) ----------------------------- #
    def create_order(
        self,
        ticker: str,
        side: str,            # "yes" | "no"
        action: str,          # "buy" | "sell"
        count: int,
        price_cents: int,     # limit price in cents (1..99)
        client_order_id: str,
        type_: str = "limit",
        post_only: bool = True,
    ) -> dict:
        """
        Place a limit order. post_only=True keeps us a maker (required for the
        liquidity-incentive edge and to avoid taker fees). The price field name
        depends on side: yes_price for yes, no_price for no.
        """
        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": int(count),
            "type": type_,
            "client_order_id": client_order_id,
            "post_only": post_only,
        }
        if side == "yes":
            body["yes_price"] = int(price_cents)
        else:
            body["no_price"] = int(price_cents)
        return self._request("POST", "/portfolio/orders", body=body)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def batch_cancel(self, order_ids: list[str]) -> dict:
        return self._request(
            "DELETE", "/portfolio/orders/batched", body={"ids": order_ids}
        )


class KalshiAPIError(Exception):
    def __init__(self, status: int, text: str, method: str, path: str):
        self.status = status
        self.text = text
        super().__init__(f"{status} on {method} {path}: {text[:300]}")


# --------------------------------------------------------------------------- #
# V2 order endpoints (deci-cent capable) — added for kalshi_unbroken_bot.
# Single unified price quoted from the YES perspective:
#   side="bid"  buys YES at `price`
#   side="ask"  sells YES at `price` (equivalent to buying NO at 1-price)
# --------------------------------------------------------------------------- #
def _fmt_price(p: float) -> str:
    return f"{p:.4f}"


class KalshiClientV2Mixin:
    def create_order_v2(
        self,
        ticker: str,
        side: str,               # "bid" | "ask"  (YES perspective)
        count: float,
        price: float,            # dollars, deci-cent ok (e.g. 0.9650)
        client_order_id: str,
        time_in_force: str = "good_till_canceled",
        expiration_time: int | None = None,
        post_only: bool = False,
        reduce_only: bool = False,
        cancel_order_on_pause: bool = False,
    ) -> dict:
        body = {
            "ticker": ticker,
            "side": side,
            "count": f"{count:.2f}",
            "price": _fmt_price(price),
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "client_order_id": client_order_id,
            "post_only": post_only,
            "reduce_only": reduce_only,
            "cancel_order_on_pause": cancel_order_on_pause,
        }
        if expiration_time is not None:
            body["expiration_time"] = int(expiration_time)
        return self._request("POST", "/portfolio/events/orders", body=body)

    def cancel_order_v2(self, order_id: str) -> dict:
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")

    def get_settlements(self, **params) -> dict:
        return self._request("GET", "/portfolio/settlements", params=params)


# graft mixin onto KalshiClient
class KalshiClientV2(KalshiClientV2Mixin, KalshiClient):
    pass
