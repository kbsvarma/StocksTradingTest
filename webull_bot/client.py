"""Webull SDK v2.0.7 client wrapper.

Correct import paths for webull-openapi-python-sdk 2.0.7:
  - ApiClient: webull.core.client.ApiClient
  - TradeClient: webull.trade.trade_client.TradeClient
  - order_v3: trade_client.order_v3  (OrderOperationV3)
"""
from __future__ import annotations

import os

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient


def build_trade_client() -> TradeClient:
    app_key = _require_env("WEBULL_APP_KEY")
    app_secret = _require_env("WEBULL_APP_SECRET")

    api_client = ApiClient(
        app_key=app_key,
        app_secret=app_secret,
        region_id="us",
    )
    return TradeClient(api_client)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value
