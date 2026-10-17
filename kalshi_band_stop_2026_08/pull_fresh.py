#!/usr/bin/env python3
"""Pull the locked July 28+ Kalshi 15-minute crypto holdout."""

from __future__ import annotations

import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")
START_TS = calendar.timegm(time.strptime("2026-07-28T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
DATA = Path(__file__).resolve().parent / "data"


def get_json(url: str, retries: int = 6) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kalshi-band-stop-study/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(20.0, 1.5 * (2**attempt)))
    raise RuntimeError("request retries exhausted")


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def pull_markets(series: str) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {
            "series_ticker": series,
            "status": "settled",
            "min_close_ts": START_TS,
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        payload = get_json(f"{BASE}/markets?{urllib.parse.urlencode(params)}")
        page = payload.get("markets", [])
        for market in page:
            close_ts = parse_ts(market["close_time"])
            if close_ts < START_TS:
                continue
            markets.append(
                {
                    "ticker": market["ticker"],
                    "open_time": market["open_time"],
                    "close_time": market["close_time"],
                    "result": market.get("result"),
                    "floor_strike": market.get("floor_strike"),
                    "expiration_value": market.get("expiration_value"),
                    "volume_fp": market.get("volume_fp"),
                    "open_interest_fp": market.get("open_interest_fp"),
                    "settlement_value_dollars": market.get("settlement_value_dollars"),
                }
            )
        cursor = payload.get("cursor")
        print(f"{series}: {len(markets)} settled markets", flush=True)
        if not cursor or not page:
            break
        time.sleep(0.15)

    markets.sort(key=lambda market: market["close_time"])
    path = DATA / f"markets_{series}.jsonl"
    with path.open("w") as handle:
        for market in markets:
            handle.write(json.dumps(market, sort_keys=True) + "\n")
    return markets


def compact_candle(candle: dict[str, Any]) -> dict[str, Any]:
    yes_bid = candle.get("yes_bid") or {}
    yes_ask = candle.get("yes_ask") or {}
    return {
        "t": candle["end_period_ts"],
        "bo": yes_bid.get("open_dollars"),
        "bc": yes_bid.get("close_dollars"),
        "bl": yes_bid.get("low_dollars"),
        "bh": yes_bid.get("high_dollars"),
        "ao": yes_ask.get("open_dollars"),
        "ac": yes_ask.get("close_dollars"),
        "al": yes_ask.get("low_dollars"),
        "ah": yes_ask.get("high_dollars"),
        "pc": (candle.get("price") or {}).get("close_dollars"),
        "v": candle.get("volume_fp"),
    }


def make_chunks(markets: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for market in markets:
        trial = current + [market]
        start = min(parse_ts(item["open_time"]) for item in trial) - 60
        end = max(parse_ts(item["close_time"]) for item in trial) + 60
        span_minutes = max(1, (end - start) // 60)
        if current and len(trial) * span_minutes > 9000:
            chunks.append(current)
            current = [market]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def pull_candles(series: str, markets: list[dict[str, Any]]) -> None:
    path = DATA / f"candles_{series}.jsonl"
    chunks = make_chunks(markets)
    with path.open("w") as handle:
        for index, chunk in enumerate(chunks, start=1):
            params = {
                "market_tickers": ",".join(item["ticker"] for item in chunk),
                "start_ts": min(parse_ts(item["open_time"]) for item in chunk) - 60,
                "end_ts": max(parse_ts(item["close_time"]) for item in chunk) + 60,
                "period_interval": 1,
            }
            payload = get_json(
                f"{BASE}/markets/candlesticks?{urllib.parse.urlencode(params, safe=',')}"
            )
            by_ticker = {
                item["market_ticker"]: item.get("candlesticks", [])
                for item in payload.get("markets", [])
            }
            for market in chunk:
                candles = [compact_candle(candle) for candle in by_ticker.get(market["ticker"], [])]
                handle.write(json.dumps({"ticker": market["ticker"], "candles": candles}) + "\n")
            handle.flush()
            if index == 1 or index % 25 == 0 or index == len(chunks):
                print(f"{series}: candle chunk {index}/{len(chunks)}", flush=True)
            time.sleep(0.2)


def write_manifest() -> None:
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "start_ts": START_TS,
        "start_utc": datetime.fromtimestamp(START_TS, timezone.utc).isoformat(),
        "series": list(SERIES),
        "purpose": "untouched final holdout for PREREG.md",
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    selected = tuple(sys.argv[1:]) or SERIES
    invalid = sorted(set(selected) - set(SERIES))
    if invalid:
        raise SystemExit(f"unsupported series: {', '.join(invalid)}")
    for series in selected:
        print(f"== {series}", flush=True)
        markets = pull_markets(series)
        pull_candles(series, markets)
    write_manifest()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
