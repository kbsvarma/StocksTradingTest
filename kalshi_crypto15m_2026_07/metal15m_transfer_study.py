#!/usr/bin/env python3
"""Forward-only transfer test of the crypto R1 rule on 15-minute metals.

The strategy parameters are inherited unchanged from the crypto study. This
script deliberately does not optimize a metal-specific band or time window.
It pulls settled markets, enters at the worse of the signal close ask and the
next minute open ask, charges the exact quadratic taker fee, and reports a
one-cent adverse-fill stress.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXGOLD15M", "KXWTI15M", "KXSILVER15M")


def request_json(url: str, retries: int = 6) -> dict:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "independent-research/1.0"}
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except Exception:  # noqa: BLE001 - retry network and API failures alike
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def pull_markets(series: str, start_ts: int) -> list[dict]:
    markets: list[dict] = []
    cursor = ""
    while True:
        url = (
            f"{BASE}/markets?series_ticker={series}&status=settled"
            f"&min_close_ts={start_ts}&limit=1000"
        )
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        response = request_json(url)
        page = response.get("markets", [])
        markets.extend(
            market for market in page if market.get("result") in ("yes", "no")
        )
        cursor = response.get("cursor") or ""
        if not cursor or not page:
            break
        time.sleep(0.15)
    return markets


def candle_chunks(markets: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for market in sorted(markets, key=lambda item: item["open_time"]):
        trial = current + [market]
        start = min(timestamp(item["open_time"]) for item in trial) - 60
        end = max(timestamp(item["close_time"]) for item in trial) + 60
        span_minutes = (end - start) // 60
        if current and len(trial) * span_minutes > 8_500:
            chunks.append(current)
            current = [market]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks


def pull_candles(markets: list[dict]) -> dict[str, list[dict]]:
    candles: dict[str, list[dict]] = {}
    for chunk in candle_chunks(markets):
        tickers = ",".join(market["ticker"] for market in chunk)
        start = min(timestamp(market["open_time"]) for market in chunk) - 60
        end = max(timestamp(market["close_time"]) for market in chunk) + 60
        response = request_json(
            f"{BASE}/markets/candlesticks?market_tickers={tickers}"
            f"&start_ts={start}&end_ts={end}&period_interval=1"
        )
        for row in response.get("markets", []):
            candles[row["market_ticker"]] = row.get("candlesticks", [])
        time.sleep(0.15)
    return candles


def quote_value(candle: dict, side: str, field: str) -> float | None:
    value = (candle.get(side) or {}).get(field)
    return None if value is None else float(value)


def favorite_close(candle: dict) -> tuple[str, float, float, float] | None:
    yes_bid = quote_value(candle, "yes_bid", "close_dollars")
    yes_ask = quote_value(candle, "yes_ask", "close_dollars")
    if yes_bid is None or yes_ask is None or yes_bid > yes_ask:
        return None
    midpoint = (yes_bid + yes_ask) / 2
    if midpoint > 0.5:
        return "yes", yes_bid, yes_ask, midpoint
    return "no", 1 - yes_ask, 1 - yes_bid, 1 - midpoint


def favorite_open_ask(candle: dict, favorite: str) -> float | None:
    yes_bid = quote_value(candle, "yes_bid", "open_dollars")
    yes_ask = quote_value(candle, "yes_ask", "open_dollars")
    if favorite == "yes":
        return yes_ask
    return None if yes_bid is None else 1 - yes_bid


def fee(price: float) -> float:
    return 0.07 * price * (1 - price)


def pnl(price: float, won: bool) -> float:
    return 1 - price - fee(price) if won else -price - fee(price)


def signal(market: dict, candles: list[dict]) -> dict | None:
    close_ts = timestamp(market["close_time"])
    by_remaining = {
        (close_ts - int(candle["end_period_ts"])) // 60: candle
        for candle in candles
        if candle.get("end_period_ts") is not None
    }
    path = {
        remaining: favorite_close(by_remaining[remaining])
        for remaining in range(14, 0, -1)
        if remaining in by_remaining and favorite_close(by_remaining[remaining])
    }

    # R1: first qualifying minute, 4-8 minutes left, 93-99c unbroken favorite.
    for remaining in range(8, 3, -1):
        current = path.get(remaining)
        if current is None:
            continue
        side, bid, ask, midpoint = current
        if ask - bid > 0.010001 or not 0.93 <= midpoint < 0.99:
            continue
        observed = [
            path[minute][0]
            for minute in range(14, remaining - 1, -1)
            if minute in path
        ]
        if len(observed) < 5 or len(set(observed)) != 1:
            continue
        entry = ask
        next_candle = by_remaining.get(remaining - 1)
        if next_candle is not None:
            next_ask = favorite_open_ask(next_candle, side)
            if next_ask is not None:
                entry = max(entry, next_ask)
        if entry > 0.99:
            continue
        won = market["result"] == side
        return {
            "ticker": market["ticker"],
            "series": market["ticker"].split("-")[0],
            "close_ts": close_ts,
            "remaining": remaining,
            "side": side,
            "entry": entry,
            "won": won,
            "pnl": pnl(entry, won),
            "pnl_one_cent_stress": pnl(min(0.999, entry + 0.01), won),
        }
    return None


def summarize(trades: list[dict]) -> dict:
    trades = sorted(trades, key=lambda trade: trade["close_ts"])
    if not trades:
        return {"trades": 0}
    count = len(trades)
    split = count // 2
    day_pnls: dict[str, float] = defaultdict(float)
    for trade in trades:
        day = datetime.fromtimestamp(trade["close_ts"], timezone.utc).date().isoformat()
        day_pnls[day] += trade["pnl"]
    values = list(day_pnls.values())
    day_stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    day_t = (
        statistics.mean(values) / (day_stdev / math.sqrt(len(values)))
        if day_stdev
        else None
    )

    def mean_cents(rows: list[dict], field: str = "pnl") -> float | None:
        return 100 * statistics.mean(row[field] for row in rows) if rows else None

    return {
        "trades": count,
        "wins": sum(trade["won"] for trade in trades),
        "losses": sum(not trade["won"] for trade in trades),
        "mean_cents": mean_cents(trades),
        "one_cent_stress_mean_cents": mean_cents(trades, "pnl_one_cent_stress"),
        "first_half_mean_cents": mean_cents(trades[:split]),
        "second_half_mean_cents": mean_cents(trades[split:]),
        "independent_days": len(day_pnls),
        "day_cluster_t": day_t,
        "trades_per_day": count / len(day_pnls),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("/tmp/kalshi_metal15m_transfer")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())

    all_trades: list[dict] = []
    results: dict[str, dict] = {}
    for series in SERIES:
        market_path = args.cache_dir / f"markets_{series}.json"
        candle_path = args.cache_dir / f"candles_{series}.json"
        if args.refresh or not market_path.exists() or not candle_path.exists():
            markets = pull_markets(series, start_ts)
            candles = pull_candles(markets)
            market_path.write_text(json.dumps(markets))
            candle_path.write_text(json.dumps(candles))
        else:
            markets = json.loads(market_path.read_text())
            candles = json.loads(candle_path.read_text())
        trades = [
            trade
            for market in markets
            if (
                trade := signal(market, candles.get(market["ticker"], []))
            )
        ]
        all_trades.extend(trades)
        results[series] = summarize(trades)
    results["POOLED"] = summarize(all_trades)
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rule": "unchanged_crypto_R1",
        "series": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")


if __name__ == "__main__":
    main()
