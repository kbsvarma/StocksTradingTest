#!/usr/bin/env python3
"""Queue-aware neutral maker capture on recorded BTC order books."""

from __future__ import annotations

import datetime
import json
import os
import statistics
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import execution_audit
import study


BASE = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"
CACHE = os.path.join(study.ROOT, "data", "maker_trade_windows.jsonl")


@dataclass(frozen=True)
class QuoteResult:
    ticker: str
    ts: int
    pnl: float
    maker_fills: int
    fill_type: str
    spread: float


def get_json(url: str, retries: int = 7) -> dict:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "btc15m-independent-research/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(min(1.5 * 2**attempt, 15))
    raise RuntimeError("unreachable")


def market_windows(markets: list[study.Market]) -> list[dict]:
    market_map = {market.ticker: market for market in markets}
    output = []
    for ticker, rows in study.load_books().items():
        market = market_map.get(ticker)
        if market is None:
            continue
        start = study.first_snapshot(rows, market.close_ts - 12 * 60)
        if start is None:
            continue
        end = study.first_snapshot(rows, start["ts"] + 30, 40)
        if end is None:
            continue
        spread = start["yes_ask"] - start["yes_bid"]
        if not 0.20 <= start["mid"] <= 0.80 or spread > 0.02 + study.EPSILON:
            continue
        output.append({"ticker": ticker, "start": start, "end": end})
    return sorted(output, key=lambda row: row["start"]["ts"])


def pull_window(ticker: str, start: int, end: int) -> list[dict]:
    output = []
    cursor = ""
    while True:
        query = {
            "ticker": ticker,
            "min_ts": start,
            "max_ts": end + 1,
            "limit": 1000,
            "is_block_trade": "false",
        }
        if cursor:
            query["cursor"] = cursor
        response = get_json(f"{BASE}?{urllib.parse.urlencode(query)}")
        for row in response.get("trades") or []:
            timestamp = execution_audit.trade_timestamp(row["created_time"])
            if start <= timestamp <= end:
                output.append(
                    {
                        "ts": timestamp,
                        "taker_side": row.get("taker_side"),
                        "yes": float(row["yes_price_dollars"]),
                        "count": float(row["count_fp"]),
                    }
                )
        cursor = response.get("cursor") or ""
        if not cursor:
            break
    return sorted(output, key=lambda row: row["ts"])


def pull(windows: list[dict]) -> dict[tuple[str, int], list[dict]]:
    cached = {}
    if os.path.exists(CACHE):
        for row in study.read_jsonl(CACHE):
            cached[(row["ticker"], row["start_ts"])] = row["trades"]
    for index, window in enumerate(windows, start=1):
        key = (window["ticker"], window["start"]["ts"])
        if key not in cached:
            cached[key] = pull_window(window["ticker"], key[1], key[1] + 30)
            time.sleep(0.04)
        if index == 1 or index % 50 == 0 or index == len(windows):
            print(f"trade windows: {index}/{len(windows)}", flush=True)

    descriptor, temporary = tempfile.mkstemp(
        prefix="maker_trade_windows", dir=os.path.dirname(CACHE)
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            for (ticker, start), trades in sorted(cached.items(), key=lambda item: item[0][1]):
                handle.write(
                    json.dumps(
                        {"ticker": ticker, "start_ts": start, "trades": trades},
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        os.replace(temporary, CACHE)
    except BaseException:
        os.unlink(temporary)
        raise
    return cached


def simulate(window: dict, trades: list[dict]) -> QuoteResult:
    start, end = window["start"], window["end"]
    bid, ask = start["yes_bid"], start["yes_ask"]
    bid_ahead, ask_ahead = start["bid_size"], start["ask_size"]
    bid_consumed = ask_consumed = 0.0
    bid_filled = ask_filled = False
    for trade in trades:
        if not bid_filled and trade["taker_side"] == "no":
            if trade["yes"] < bid - study.EPSILON:
                bid_filled = True
            elif abs(trade["yes"] - bid) <= study.EPSILON:
                bid_consumed += trade["count"]
                bid_filled = bid_consumed >= bid_ahead + 1
        if not ask_filled and trade["taker_side"] == "yes":
            if trade["yes"] > ask + study.EPSILON:
                ask_filled = True
            elif abs(trade["yes"] - ask) <= study.EPSILON:
                ask_consumed += trade["count"]
                ask_filled = ask_consumed >= ask_ahead + 1

    if bid_filled and ask_filled:
        pnl = ask - bid
        fill_type = "both"
        fills = 2
    elif bid_filled:
        exit_bid = end["yes_bid"]
        pnl = exit_bid - bid - study.fee(exit_bid)
        fill_type = "bid_only"
        fills = 1
    elif ask_filled:
        no_entry = 1 - ask
        no_exit_bid = 1 - end["yes_ask"]
        pnl = no_exit_bid - no_entry - study.fee(no_exit_bid)
        fill_type = "ask_only"
        fills = 1
    else:
        pnl = 0.0
        fill_type = "none"
        fills = 0
    return QuoteResult(
        window["ticker"], start["ts"], pnl, fills, fill_type, ask - bid
    )


def summary(label: str, rows: list[QuoteResult], maker_stress_cents: float) -> None:
    adjusted = [row.pnl - row.maker_fills * maker_stress_cents / 100 for row in rows]
    filled = [row for row in rows if row.maker_fills]
    both = [row for row in rows if row.fill_type == "both"]
    one = [row for row in rows if row.maker_fills == 1]
    days = max((max(row.ts for row in rows) - min(row.ts for row in rows)) / 86400, 1)
    print(
        f"{label:20} quotes={len(rows):3d} filled={len(filled):3d} "
        f"both={len(both):3d} one={len(one):3d} "
        f"EV/quote={100*statistics.mean(adjusted):+7.3f}c "
        f"EV/filled={100*statistics.mean(row.pnl for row in filled):+7.3f}c "
        f"$/day@1={sum(adjusted)/days:+6.3f}"
    )


def main() -> None:
    markets = study.load_markets()
    windows = market_windows(markets)
    trade_windows = pull(windows)
    rows = [
        simulate(window, trade_windows[(window["ticker"], window["start"]["ts"])])
        for window in windows
    ]
    split = sorted(row.ts for row in rows)[len(rows) // 2]
    halves = (
        ("first half", [row for row in rows if row.ts < split]),
        ("final half", [row for row in rows if row.ts >= split]),
        ("all", rows),
    )
    print(
        f"eligible windows={len(rows)} split="
        f"{datetime.datetime.fromtimestamp(split, datetime.timezone.utc).isoformat()}"
    )
    for stress in (0.0, 0.10, 0.25):
        print(f"\nmaker-fill stress={stress:.2f}c")
        for label, part in halves:
            summary(label, part, stress)
    print("\nfill types:")
    for fill_type in ("none", "both", "bid_only", "ask_only"):
        part = [row for row in rows if row.fill_type == fill_type]
        if part:
            print(
                f"{fill_type:10} n={len(part):3d} "
                f"mean={100*statistics.mean(row.pnl for row in part):+7.3f}c"
            )


if __name__ == "__main__":
    main()
