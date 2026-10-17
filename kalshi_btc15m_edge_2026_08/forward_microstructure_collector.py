#!/usr/bin/env python3
"""Read-only synchronized BRTI, order-book, ticker, and trade collector."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import websockets


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "kalshi_unbroken_bot"))

from kalshi_client import Credentials, _sign  # noqa: E402


REST_BASE = "https://external-api.kalshi.com/trade-api/v2"
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
DEFAULT_OUTPUT = ROOT / "data" / "forward_brti_books.jsonl"


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def open_btc_market() -> dict | None:
    response = requests.get(
        f"{REST_BASE}/markets",
        params={"series_ticker": "KXBTC15M", "status": "open", "limit": 20},
        timeout=15,
    )
    response.raise_for_status()
    markets = response.json().get("markets") or []
    now = time.time()
    active = [
        market
        for market in markets
        if parse_time(market["open_time"]) <= now <= parse_time(market["close_time"]) + 5
    ]
    return min(active, key=lambda market: market["close_time"], default=None)


def auth_headers(credentials: Credentials) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    return {
        "KALSHI-ACCESS-KEY": credentials.key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": _sign(
            credentials.private_key, timestamp + "GET" + WS_PATH
        ),
    }


def record(handle, event: dict) -> None:
    row = {
        "client_received_at_ms": time.time_ns() // 1_000_000,
        "client_monotonic_ns": time.monotonic_ns(),
        **event,
    }
    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    handle.flush()


async def collect_market(credentials: Credentials, market: dict, output: Path) -> dict:
    ticker = market["ticker"]
    close_ts = parse_time(market["close_time"])
    output.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with output.open("a") as handle:
        record(
            handle,
            {
                "type": "collector_market",
                "ticker": ticker,
                "open_time": market["open_time"],
                "close_time": market["close_time"],
                "floor_strike": market.get("floor_strike"),
            },
        )
        async with websockets.connect(
            WS_URL,
            additional_headers=auth_headers(credentials),
            ping_interval=20,
            ping_timeout=20,
            max_queue=100_000,
        ) as websocket:
            subscriptions = (
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["cfbenchmarks_value"],
                        "index_ids": ["BRTI"],
                    },
                },
                {
                    "id": 2,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_ticker": ticker,
                    },
                },
                {
                    "id": 3,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["ticker", "trade"],
                        "market_ticker": ticker,
                    },
                },
            )
            for subscription in subscriptions:
                await websocket.send(json.dumps(subscription))

            while time.time() <= close_ts + 5:
                timeout = max(0.1, min(10.0, close_ts + 5 - time.time()))
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                message = json.loads(raw)
                message_type = message.get("type", "unknown")
                counts[message_type] = counts.get(message_type, 0) + 1
                record(handle, message)
                if message_type == "error":
                    raise RuntimeError(f"WebSocket subscription error: {message}")
    return {"ticker": ticker, "counts": counts}


async def run(intervals: int, output: Path) -> None:
    credentials = Credentials.load()
    completed = 0
    while intervals <= 0 or completed < intervals:
        market = open_btc_market()
        if market is None:
            await asyncio.sleep(2)
            continue
        result = await collect_market(credentials, market, output)
        completed += 1
        print(json.dumps(result, sort_keys=True), flush=True)
        if intervals <= 0 or completed < intervals:
            await asyncio.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intervals", type=int, default=1, help="0 runs forever")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(run(args.intervals, args.output))


if __name__ == "__main__":
    main()
