#!/usr/bin/env python3
"""Pull an untouched BTC15M holdout from Kalshi's public API.

The pull begins after the original crypto study ended. Files are rewritten
atomically so a failed request cannot leave a partially valid research set.
"""

from __future__ import annotations

import calendar
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
START_ISO = "2026-07-27T00:00:00Z"
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")


def to_timestamp(value: str) -> int:
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def get_json(url: str, retries: int = 6) -> dict:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "btc15m-independent-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(1.5 * 2**attempt, 20))
    raise RuntimeError("unreachable")


def write_jsonl(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=os.path.basename(path), dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def pull_markets() -> list[dict]:
    output = []
    cursor = ""
    while True:
        query = {
            "series_ticker": SERIES,
            "status": "settled",
            "min_close_ts": to_timestamp(START_ISO),
            "limit": 1000,
        }
        if cursor:
            query["cursor"] = cursor
        response = get_json(f"{BASE}/markets?{urllib.parse.urlencode(query)}")
        markets = response.get("markets") or []
        for market in markets:
            if market.get("result") not in {"yes", "no"}:
                continue
            output.append(
                {
                    "ticker": market["ticker"],
                    "open_time": market["open_time"],
                    "close_time": market["close_time"],
                    "result": market["result"],
                    "floor_strike": market.get("floor_strike"),
                    "expiration_value": market.get("expiration_value"),
                    "volume_fp": market.get("volume_fp"),
                    "open_interest_fp": market.get("open_interest_fp"),
                    "price_level_structure": market.get("price_level_structure"),
                    "rules_primary": market.get("rules_primary"),
                }
            )
        cursor = response.get("cursor") or ""
        print(f"markets: {len(output)}", flush=True)
        if not cursor or not markets:
            break
        time.sleep(0.15)
    output.sort(key=lambda row: row["close_time"])
    write_jsonl(os.path.join(DATA, "markets_fresh.jsonl"), output)
    return output


def compact_candle(candle: dict) -> dict:
    bid = candle.get("yes_bid") or {}
    ask = candle.get("yes_ask") or {}
    price = candle.get("price") or {}
    return {
        "t": candle["end_period_ts"],
        "bo": bid.get("open_dollars"),
        "bc": bid.get("close_dollars"),
        "bl": bid.get("low_dollars"),
        "bh": bid.get("high_dollars"),
        "ao": ask.get("open_dollars"),
        "ac": ask.get("close_dollars"),
        "al": ask.get("low_dollars"),
        "ah": ask.get("high_dollars"),
        "pc": price.get("close_dollars"),
        "v": candle.get("volume_fp"),
        "oi": candle.get("open_interest_fp"),
    }


def chunks(markets: list[dict]) -> list[list[dict]]:
    output: list[list[dict]] = []
    current: list[dict] = []
    for market in markets:
        trial = current + [market]
        start = min(to_timestamp(row["open_time"]) for row in trial) - 60
        end = max(to_timestamp(row["close_time"]) for row in trial) + 60
        window_minutes = (end - start) // 60
        if current and len(trial) * window_minutes > 9000:
            output.append(current)
            current = [market]
        else:
            current = trial
    if current:
        output.append(current)
    return output


def pull_candles(markets: list[dict]) -> None:
    output = []
    groups = chunks(markets)
    for index, group in enumerate(groups, start=1):
        start = min(to_timestamp(row["open_time"]) for row in group) - 60
        end = max(to_timestamp(row["close_time"]) for row in group) + 60
        query = urllib.parse.urlencode(
            {
                "market_tickers": ",".join(row["ticker"] for row in group),
                "start_ts": start,
                "end_ts": end,
                "period_interval": 1,
            }
        )
        response = get_json(f"{BASE}/markets/candlesticks?{query}")
        found = {
            row["market_ticker"]: row.get("candlesticks") or []
            for row in response.get("markets") or []
        }
        for market in group:
            output.append(
                {
                    "ticker": market["ticker"],
                    "candles": [compact_candle(c) for c in found.get(market["ticker"], [])],
                }
            )
        if index == 1 or index % 25 == 0 or index == len(groups):
            print(f"candle groups: {index}/{len(groups)}", flush=True)
        time.sleep(0.2)
    output.sort(key=lambda row: row["ticker"])
    write_jsonl(os.path.join(DATA, "candles_fresh.jsonl"), output)


def main() -> None:
    markets = pull_markets()
    pull_candles(markets)
    print(f"complete: {len(markets)} settled BTC markets since {START_ISO}")


if __name__ == "__main__":
    main()
