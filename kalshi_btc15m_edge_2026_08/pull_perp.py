#!/usr/bin/env python3
"""Pull BTC perpetual one-minute quotes for an independent BRTI proxy."""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request


BASE = "https://external-api.kalshi.com/trade-api/v2/margin/markets"
TICKER = "KXBTCPERP"
START_TS = 1785110400  # 2026-07-27 00:00:00 UTC
ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(ROOT, "data", "perp_fresh.jsonl")


def get_json(url: str, retries: int = 6) -> dict:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "btc15m-independent-research/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(min(1.5 * 2**attempt, 20))
    raise RuntimeError("unreachable")


def main() -> None:
    end_ts = int(time.time()) // 3600 * 3600
    rows = []
    hours = list(range(START_TS, end_ts, 3600))
    for index, hour in enumerate(hours, start=1):
        response = get_json(
            f"{BASE}/{TICKER}/candlesticks?start_ts={hour}&end_ts={hour + 3600}"
            "&period_interval=1"
        )
        rows.append(
            {
                "hour": hour,
                "candles": [
                    {
                        "t": candle["end_period_ts"],
                        "bid": (candle.get("bid") or {}).get("close"),
                        "ask": (candle.get("ask") or {}).get("close"),
                        "bid_low": (candle.get("bid") or {}).get("low"),
                        "ask_high": (candle.get("ask") or {}).get("high"),
                    }
                    for candle in response.get("candlesticks") or []
                ],
            }
        )
        if index == 1 or index % 50 == 0 or index == len(hours):
            print(f"hours: {index}/{len(hours)}", flush=True)
        time.sleep(0.1)

    descriptor, temporary = tempfile.mkstemp(
        prefix="perp_fresh", dir=os.path.dirname(OUTPUT)
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(temporary, OUTPUT)
    except BaseException:
        os.unlink(temporary)
        raise
    print(f"complete: {len(rows)} hourly blocks")


if __name__ == "__main__":
    main()
