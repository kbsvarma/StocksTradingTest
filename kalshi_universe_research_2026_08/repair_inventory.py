#!/usr/bin/env python3
"""Retry rate-limited inventory rows without silently dropping series."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


BASE = "https://external-api.kalshi.com/trade-api/v2"


def get_markets(series: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"series_ticker": series, "status": "settled", "limit": 100}
    )
    for attempt in range(8):
        try:
            request = urllib.request.Request(
                f"{BASE}/markets?{query}",
                headers={"User-Agent": "kalshi-universe-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response).get("markets", [])
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 7:
                raise
            time.sleep(2.0 + attempt * 1.5)
        except Exception:
            if attempt == 7:
                raise
            time.sleep(1.0 + attempt)
    raise RuntimeError("unreachable")


def epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def repair(row: dict) -> dict:
    markets = [market for market in get_markets(row["ticker"]) if market.get("result") in ("yes", "no")]
    volumes = sorted(float(market.get("volume_fp") or 0.0) for market in markets)
    events = {market.get("event_ticker") for market in markets if market.get("event_ticker")}
    close_times = [epoch(market["close_time"]) for market in markets if market.get("close_time")]
    sample = markets[0] if markets else {}
    output = {key: value for key, value in row.items() if key != "error"}
    output.update(
        {
            "recent_settled_markets": len(markets),
            "recent_settled_events": len(events),
            "median_market_volume": statistics.median(volumes) if volumes else 0.0,
            "max_market_volume": max(volumes, default=0.0),
            "earliest_recent_close": min(close_times, default=None),
            "latest_recent_close": max(close_times, default=None),
            "sample_ticker": sample.get("ticker"),
            "sample_strike_type": sample.get("strike_type"),
        }
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    repaired = []
    failures = 0
    for index, row in enumerate(rows, start=1):
        if "error" not in row:
            repaired.append(row)
            continue
        try:
            repaired.append(repair(row))
        except Exception as exc:
            repaired.append({**row, "retry_error": repr(exc)})
            failures += 1
        time.sleep(0.35)
        print(f"retried {row['ticker']} ({index}/{len(rows)})", flush=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in repaired))
    print(f"wrote {len(repaired)} rows with {failures} retry failures")


if __name__ == "__main__":
    main()
