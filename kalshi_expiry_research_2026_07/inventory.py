#!/usr/bin/env python3
"""Inventory recurring Kalshi series and screen for usable settlement history."""

from __future__ import annotations

import argparse
import calendar
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
FREQUENCIES = frozenset(("fifteen_min", "hourly", "daily", "weekly"))


def get_json(path: str, params: dict[str, object] | None = None) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{BASE_URL}{path}{query}",
        headers={"User-Agent": "kalshi-expiry-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def to_timestamp(value: str) -> int:
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def get_recent_settled(ticker: str, limit: int) -> list[dict]:
    response = get_json(
        "/markets",
        {"series_ticker": ticker, "status": "settled", "limit": limit},
    )
    return response.get("markets", [])


def normalized_series_ticker(ticker: str) -> str:
    return ticker if ticker.startswith("KX") else f"KX{ticker}"


def summarize_series(series: dict, recent_limit: int) -> dict:
    ticker = normalized_series_ticker(series["ticker"])
    markets = get_recent_settled(ticker, recent_limit)
    settled = [market for market in markets if market.get("result") in ("yes", "no")]
    volumes = sorted(float(market.get("volume_fp") or 0) for market in settled)
    events = {market.get("event_ticker") for market in settled if market.get("event_ticker")}
    close_times = [
        to_timestamp(market["close_time"])
        for market in settled
        if market.get("close_time")
    ]
    return {
        "ticker": ticker,
        "title": series.get("title"),
        "category": series.get("category"),
        "frequency": series.get("frequency"),
        "fee_type": series.get("fee_type"),
        "fee_multiplier": series.get("fee_multiplier"),
        "recent_settled_markets": len(settled),
        "recent_settled_events": len(events),
        "median_market_volume": statistics.median(volumes) if volumes else 0.0,
        "max_market_volume": max(volumes, default=0.0),
        "earliest_recent_close": min(close_times, default=None),
        "latest_recent_close": max(close_times, default=None),
        "sample_ticker": settled[0]["ticker"] if settled else None,
        "sample_strike_type": settled[0].get("strike_type") if settled else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recent-limit", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("data/inventory.jsonl"))
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    response = get_json("/series")
    recurring = {
        series["ticker"]: series
        for series in response.get("series", [])
        if series.get("frequency") in FREQUENCIES
    }

    with args.output.open("w") as output:
        for index, ticker in enumerate(sorted(recurring), start=1):
            try:
                record = summarize_series(recurring[ticker], args.recent_limit)
            except Exception as exc:  # Keep a complete, auditable rejection ledger.
                record = {
                    "ticker": normalized_series_ticker(ticker),
                    "title": recurring[ticker].get("title"),
                    "category": recurring[ticker].get("category"),
                    "frequency": recurring[ticker].get("frequency"),
                    "error": repr(exc),
                }
            output.write(json.dumps(record, sort_keys=True) + "\n")
            if index % 25 == 0:
                print(f"screened {index}/{len(recurring)}", flush=True)
            time.sleep(0.08)

    print(f"wrote {len(recurring)} recurring series to {args.output}")


if __name__ == "__main__":
    main()
