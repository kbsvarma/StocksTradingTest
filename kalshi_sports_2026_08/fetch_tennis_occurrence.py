#!/usr/bin/env python3
"""Fetch scheduled occurrence metadata omitted from the candle archive."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://api.elections.kalshi.com/trade-api/v2"
FAMILIES = ("KXWTAMATCH", "KXITFMATCH", "KXITFWMATCH")


def get(params: dict, retries: int = 6) -> dict:
    url = f"{BASE}/markets?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "tennis-rule-audit/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def main() -> None:
    output_path = os.path.join(DATA, "audit_tennis_occurrence.jsonl")
    rows = []
    for family in FAMILIES:
        local_path = os.path.join(DATA, f"games_{family}.jsonl")
        wanted = {}
        with open(local_path, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                wanted[row["ticker"]] = row
        min_close = min(row["close_ts"] for row in wanted.values()) - 86400
        cursor = None
        found = {}
        while True:
            params = {
                "series_ticker": family,
                "status": "settled",
                "min_close_ts": min_close,
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor
            response = get(params)
            markets = response.get("markets", [])
            for market in markets:
                ticker = market.get("ticker")
                if ticker in wanted:
                    found[ticker] = {
                        "ticker": ticker,
                        "event_ticker": market.get("event_ticker"),
                        "occurrence_datetime": market.get("occurrence_datetime"),
                        "expected_expiration_time": market.get("expected_expiration_time"),
                        "open_time": market.get("open_time"),
                        "close_time": market.get("close_time"),
                        "open_interest_fp": market.get("open_interest_fp"),
                        "volume_fp": market.get("volume_fp"),
                    }
            cursor = response.get("cursor")
            if not cursor or not markets:
                break
            time.sleep(0.15)
        rows.extend(found.values())
        print(f"{family}: metadata {len(found)}/{len(wanted)}")
    rows.sort(key=lambda row: row["ticker"])
    with open(output_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(output_path)


if __name__ == "__main__":
    main()
