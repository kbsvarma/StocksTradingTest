#!/usr/bin/env python3
"""Download weather markets settled after the frozen research archive."""

import calendar
import json
import os
import time
import urllib.parse
import urllib.request

from weather_entry_exit_study import SERIES, parse_ts


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
BASE = "https://api.elections.kalshi.com/trade-api/v2"
START = calendar.timegm(time.strptime("2026-05-18T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))


def get(path: str, params: dict, retries: int = 5) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "independent-weather-research/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def chunks(markets: list[dict]) -> list[list[dict]]:
    result = []
    index = 0
    while index < len(markets):
        chunk = [markets[index]]
        cursor = index + 1
        while cursor < len(markets) and len(chunk) < 60:
            trial = chunk + [markets[cursor]]
            span_hours = (
                max(parse_ts(row["close_time"]) for row in trial)
                - min(parse_ts(row["open_time"]) for row in trial)
            ) // 3600 + 2
            if len(trial) * span_hours > 9000:
                break
            chunk = trial
            cursor += 1
        result.append(chunk)
        index = cursor
    return result


def main() -> None:
    for series in SERIES:
        frozen_path = os.path.join(DATA, f"markets_{series}.jsonl")
        frozen = {json.loads(line)["ticker"] for line in open(frozen_path, encoding="utf-8")}
        response = get(
            "/markets",
            {"series_ticker": series, "status": "settled", "min_close_ts": START, "limit": 1000},
        )
        extension = []
        for market in response.get("markets", []):
            if market["ticker"] in frozen or market.get("result") not in ("yes", "no"):
                continue
            extension.append(
                {
                    key: market.get(key)
                    for key in (
                        "ticker",
                        "open_time",
                        "close_time",
                        "result",
                        "floor_strike",
                        "cap_strike",
                        "strike_type",
                        "volume_fp",
                        "event_ticker",
                    )
                }
            )
        extension.sort(key=lambda row: row["open_time"])
        market_path = os.path.join(DATA, f"extension_markets_{series}.jsonl")
        candle_path = os.path.join(DATA, f"extension_candles_{series}.jsonl")
        with open(market_path, "w", encoding="utf-8") as handle:
            for market in extension:
                handle.write(json.dumps(market) + "\n")
        with open(candle_path, "w", encoding="utf-8") as handle:
            for chunk in chunks(extension):
                response = get(
                    "/markets/candlesticks",
                    {
                        "market_tickers": ",".join(row["ticker"] for row in chunk),
                        "start_ts": min(parse_ts(row["open_time"]) for row in chunk) - 3600,
                        "end_ts": max(parse_ts(row["close_time"]) for row in chunk) + 3600,
                        "period_interval": 60,
                    },
                )
                found = {
                    row["market_ticker"]: row.get("candlesticks", [])
                    for row in response.get("markets", [])
                }
                for market in chunk:
                    candles = [
                        {
                            "t": candle["end_period_ts"],
                            "b": candle["yes_bid"].get("close_dollars"),
                            "a": candle["yes_ask"].get("close_dollars"),
                            "v": candle.get("volume_fp"),
                        }
                        for candle in found.get(market["ticker"], [])
                    ]
                    handle.write(json.dumps({"ticker": market["ticker"], "candles": candles}) + "\n")
                time.sleep(0.2)
        print(f"{series}: {len(extension)} new settled contracts")


if __name__ == "__main__":
    main()
