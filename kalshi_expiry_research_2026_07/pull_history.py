#!/usr/bin/env python3
"""Pull settled Kalshi markets and causal candle histories for research."""

from __future__ import annotations

import argparse
import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def get_json(path: str, params: dict[str, object], retries: int = 6) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{BASE_URL}{path}?{query}",
        headers={"User-Agent": "kalshi-expiry-research/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == retries - 1:
                raise
            time.sleep(min(20, 1.5 * (2**attempt)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(20, 1.5 * (2**attempt)))
    raise RuntimeError("unreachable")


def to_timestamp(value: str) -> int:
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def compact_market(market: dict) -> dict:
    fields = (
        "ticker",
        "event_ticker",
        "title",
        "yes_sub_title",
        "no_sub_title",
        "open_time",
        "close_time",
        "expected_expiration_time",
        "expiration_time",
        "settlement_ts",
        "occurrence_datetime",
        "result",
        "floor_strike",
        "cap_strike",
        "strike_type",
        "volume_fp",
        "expiration_value",
        "settlement_value_dollars",
    )
    return {field: market.get(field) for field in fields}


def pull_markets(
    series: str,
    start_ts: int,
    output: Path,
    end_ts: int | None = None,
) -> list[dict]:
    markets: list[dict] = []
    cursor = None
    while True:
        params: dict[str, object] = {
            "series_ticker": series,
            "status": "settled",
            "min_close_ts": start_ts,
            "limit": 1000,
        }
        if end_ts is not None:
            params["max_close_ts"] = end_ts
        if cursor:
            params["cursor"] = cursor
        response = get_json("/markets", params)
        page = response.get("markets", [])
        markets.extend(compact_market(market) for market in page)
        cursor = response.get("cursor")
        print(f"{series}: {len(markets)} settled markets", flush=True)
        if not cursor or not page:
            break
        time.sleep(0.15)

    markets = sorted(
        {market["ticker"]: market for market in markets}.values(),
        key=lambda market: (market.get("close_time") or "", market["ticker"]),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as handle:
        for market in markets:
            handle.write(json.dumps(market, sort_keys=True) + "\n")
    return markets


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def candle_chunks(markets: list[dict], period_minutes: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for market in sorted(markets, key=lambda item: item["open_time"]):
        trial = current + [market]
        start = min(to_timestamp(item["open_time"]) for item in trial) - 60
        end = max(to_timestamp(item["close_time"]) for item in trial) + 60
        periods = max(1, (end - start) // (period_minutes * 60) + 1)
        url_size = sum(len(item["ticker"]) + 1 for item in trial)
        if current and (
            len(trial) * periods > 8000
            or len(trial) > 50
            or url_size > 3500
        ):
            chunks.append(current)
            current = [market]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def compact_candle(candle: dict) -> dict:
    return {
        "t": candle["end_period_ts"],
        "bo": (candle.get("yes_bid") or {}).get("open_dollars"),
        "bc": (candle.get("yes_bid") or {}).get("close_dollars"),
        "ao": (candle.get("yes_ask") or {}).get("open_dollars"),
        "ac": (candle.get("yes_ask") or {}).get("close_dollars"),
        "v": candle.get("volume_fp"),
    }


def pull_candles(
    markets: list[dict],
    output: Path,
    period_minutes: int,
) -> None:
    done = {
        record["ticker"]
        for record in load_jsonl(output)
        if record.get("ticker")
    }
    error_path = output.with_suffix(".errors.jsonl")
    failed = {
        record["ticker"]
        for record in load_jsonl(error_path)
        if record.get("ticker")
    }
    eligible = [
        market
        for market in markets
        if market.get("result") in ("yes", "no")
        and market.get("open_time")
        and market.get("close_time")
        and market["ticker"] not in done
        and market["ticker"] not in failed
    ]
    chunks = candle_chunks(eligible, period_minutes)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"{len(eligible)} markets need {period_minutes}-minute candles "
        f"in {len(chunks)} chunks",
        flush=True,
    )
    completed_chunks = 0
    with output.open("a") as handle, error_path.open("a") as error_handle:
        pending = list(chunks)
        while pending:
            chunk = pending.pop(0)
            start_ts = min(to_timestamp(item["open_time"]) for item in chunk) - 60
            end_ts = max(to_timestamp(item["close_time"]) for item in chunk) + 60
            try:
                response = get_json(
                    "/markets/candlesticks",
                    {
                        "market_tickers": ",".join(item["ticker"] for item in chunk),
                        "start_ts": start_ts,
                        "end_ts": end_ts,
                        "period_interval": period_minutes,
                    },
                )
            except Exception as exc:
                if len(chunk) > 1:
                    midpoint = len(chunk) // 2
                    pending = [chunk[:midpoint], chunk[midpoint:]] + pending
                    continue
                error_handle.write(
                    json.dumps(
                        {
                            "ticker": chunk[0]["ticker"],
                            "error": repr(exc),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                error_handle.flush()
                print(f"skipped candle error for {chunk[0]['ticker']}: {exc}")
                continue
            received = {
                item["market_ticker"]: item.get("candlesticks", [])
                for item in response.get("markets", [])
            }
            for market in chunk:
                handle.write(
                    json.dumps(
                        {
                            "ticker": market["ticker"],
                            "candles": [
                                compact_candle(candle)
                                for candle in received.get(market["ticker"], [])
                            ],
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            handle.flush()
            completed_chunks += 1
            if completed_chunks == 1 or completed_chunks % 20 == 0 or not pending:
                print(
                    f"candle chunks completed={completed_chunks} pending={len(pending)}",
                    flush=True,
                )
            time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("series", nargs="+")
    parser.add_argument("--start", default="2026-05-18T00:00:00Z")
    parser.add_argument("--end")
    parser.add_argument("--period", type=int, choices=(1, 60), default=1)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--refresh-markets", action="store_true")
    args = parser.parse_args()
    start_ts = to_timestamp(args.start)
    end_ts = to_timestamp(args.end) if args.end else None

    for series in args.series:
        market_path = args.data_dir / f"markets_{series}.jsonl"
        if args.refresh_markets or not market_path.exists():
            markets = pull_markets(series, start_ts, market_path, end_ts)
        else:
            markets = load_jsonl(market_path)
            print(f"{series}: {len(markets)} cached markets", flush=True)
        candle_path = args.data_dir / f"candles_{series}_{args.period}m.jsonl"
        pull_candles(markets, candle_path, args.period)


if __name__ == "__main__":
    main()
