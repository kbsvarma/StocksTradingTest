#!/usr/bin/env python3
"""Download settled hourly ladders and their final 25 minutes of candles."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = (
    "KXBTCD",
    "KXETHD",
    "KXSOLD",
    "KXXRPD",
    "KXBNBD",
    "KXDOGED",
    "KXGOLDH",
    "KXSILVERH",
    "KXWTIH",
)


def get_json(path: str, params: dict[str, object], retries: int = 7) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "hourly-research/2026-08"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(min(30.0, 1.5 * (attempt + 1)))
    raise RuntimeError("unreachable")


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def compact_market(market: dict, series: str) -> dict:
    return {
        "series_ticker": series,
        "event_ticker": market.get("event_ticker"),
        "ticker": market["ticker"],
        "open_time": market.get("open_time"),
        "close_time": market["close_time"],
        "result": market.get("result"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "strike_type": market.get("strike_type"),
        "volume_fp": market.get("volume_fp"),
        "open_interest_fp": market.get("open_interest_fp"),
    }


def pull_markets(series: str, start_ts: int, data_dir: Path) -> list[dict]:
    rows: dict[str, dict] = {}
    cursor = ""
    page = 0
    reached_start = False
    while not reached_start:
        params: dict[str, object] = {
            "series_ticker": series,
            "status": "settled",
            "limit": 200,
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/events", params)
        events = payload.get("events", [])
        if not events:
            break
        page += 1
        oldest = None
        oldest_event = None
        for event in events:
            event_ticker = event.get("event_ticker")
            event_time = event.get("strike_date") or event.get("settlement_ts")
            if event_time:
                event_ts = parse_ts(event_time)
                oldest_event = event_ts if oldest_event is None else min(oldest_event, event_ts)
            for raw_market in event.get("markets", []):
                close_ts = parse_ts(raw_market["close_time"])
                oldest = close_ts if oldest is None else min(oldest, close_ts)
                if close_ts < start_ts:
                    continue
                market = compact_market(raw_market, series)
                market["event_ticker"] = market["event_ticker"] or event_ticker
                rows[market["ticker"]] = market
        # Older event shells can be returned without nested markets. Relying
        # only on market close times would then paginate through all history.
        reached_start = (
            (oldest is not None and oldest < start_ts)
            or (oldest_event is not None and oldest_event < start_ts)
        )
        cursor = payload.get("cursor") or ""
        print(
            f"{series}: event page {page}, markets in window {len(rows)}",
            flush=True,
        )
        if not cursor:
            break
        time.sleep(0.15)

    output = sorted(rows.values(), key=lambda row: (row["close_time"], row["ticker"]))
    path = data_dir / f"markets_{series}.jsonl"
    with path.open("w") as handle:
        for row in output:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return output


def compact_candle(candle: dict) -> dict:
    bid = candle.get("yes_bid") or {}
    ask = candle.get("yes_ask") or {}
    return {
        "t": candle["end_period_ts"],
        "bo": bid.get("open_dollars"),
        "bc": bid.get("close_dollars"),
        "ao": ask.get("open_dollars"),
        "ac": ask.get("close_dollars"),
        "v": candle.get("volume_fp"),
        "oi": candle.get("open_interest_fp"),
    }


def candle_chunks(markets: list[dict]) -> list[list[dict]]:
    eligible = [
        market
        for market in markets
        if market.get("result") in ("yes", "no")
        and float(market.get("volume_fp") or 0.0) > 0.0
    ]
    eligible.sort(key=lambda row: (parse_ts(row["close_time"]), row["ticker"]))
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for market in eligible:
        trial = current + [market]
        closes = [parse_ts(row["close_time"]) for row in trial]
        span_minutes = (max(closes) - min(closes)) // 60 + 27
        if current and (len(trial) > 100 or len(trial) * span_minutes > 8_000):
            chunks.append(current)
            current = [market]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def pull_candles(series: str, markets: list[dict], data_dir: Path) -> None:
    chunks = candle_chunks(markets)
    path = data_dir / f"candles_{series}.jsonl"
    with path.open("w") as handle:
        for index, chunk in enumerate(chunks, start=1):
            closes = [parse_ts(row["close_time"]) for row in chunk]
            params = {
                "market_tickers": ",".join(row["ticker"] for row in chunk),
                "start_ts": min(closes) - 26 * 60,
                "end_ts": max(closes) + 60,
                "period_interval": 1,
            }
            payload = get_json("/markets/candlesticks", params)
            by_ticker = {
                item["market_ticker"]: item.get("candlesticks", [])
                for item in payload.get("markets", [])
            }
            for market in chunk:
                candles = [compact_candle(candle) for candle in by_ticker.get(market["ticker"], [])]
                handle.write(
                    json.dumps(
                        {"ticker": market["ticker"], "candles": candles},
                        sort_keys=True,
                    )
                    + "\n"
                )
            handle.flush()
            if index == 1 or index % 25 == 0 or index == len(chunks):
                print(f"{series}: candle chunk {index}/{len(chunks)}", flush=True)
            time.sleep(0.15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-18T00:00:00Z")
    parser.add_argument("--series", nargs="*", default=list(SERIES))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    start_ts = parse_ts(args.start)

    manifest_path = args.data_dir / "manifest.json"
    existing_manifest = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )
    manifest = {
        "last_run_started_utc": datetime.now(timezone.utc).isoformat(),
        "start_utc": args.start,
        "series": sorted(set(existing_manifest.get("series", [])) | set(args.series)),
        "counts": dict(existing_manifest.get("counts", {})),
    }
    for series in args.series:
        markets = pull_markets(series, start_ts, args.data_dir)
        pull_candles(series, markets, args.data_dir)
        manifest.setdefault("counts", {})[series] = {
            "markets": len(markets),
            "events": len({row["event_ticker"] for row in markets}),
            "markets_with_volume": sum(
                float(row.get("volume_fp") or 0.0) > 0.0 for row in markets
            ),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
    manifest["last_run_completed_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
