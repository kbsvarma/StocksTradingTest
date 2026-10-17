#!/usr/bin/env python3
"""Audit fresh breakout signals against actual Kalshi public trade prints."""

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

import breakout_study
import study


BASE = "https://api.elections.kalshi.com/trade-api/v2/markets/trades"
CACHE = os.path.join(study.ROOT, "data", "execution_trades.jsonl")


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


def trade_timestamp(value: str) -> float:
    normalized = value.removesuffix("Z")
    if "." in normalized:
        base, fraction = normalized.split(".", 1)
        normalized = f"{base}.{fraction[:6].ljust(6, '0')}"
    return datetime.datetime.fromisoformat(normalized).replace(
        tzinfo=datetime.timezone.utc
    ).timestamp()


def pull(signals: list[study.Trade]) -> list[dict]:
    cached = {row["ticker"]: row for row in study.read_jsonl(CACHE)} if os.path.exists(CACHE) else {}
    for index, signal in enumerate(signals, start=1):
        if signal.ticker in cached:
            continue
        decision_ts = signal.close_ts - signal.signal_rem * 60
        query = urllib.parse.urlencode(
            {
                "ticker": signal.ticker,
                "min_ts": decision_ts,
                "max_ts": decision_ts + 6,
                "limit": 1000,
                "is_block_trade": "false",
            }
        )
        response = get_json(f"{BASE}?{query}")
        prints = []
        for row in response.get("trades") or []:
            timestamp = trade_timestamp(row["created_time"])
            if not decision_ts <= timestamp <= decision_ts + 6:
                continue
            prints.append(
                {
                    "ts": timestamp,
                    "side": row.get("taker_side"),
                    "book_side": row.get("taker_book_side"),
                    "yes": float(row["yes_price_dollars"]),
                    "no": float(row["no_price_dollars"]),
                    "count": float(row["count_fp"]),
                }
            )
        cached[signal.ticker] = {
            "ticker": signal.ticker,
            "decision_ts": decision_ts,
            "signal_side": signal.side,
            "modeled_entry": signal.entry,
            "prints": sorted(prints, key=lambda row: row["ts"]),
            "truncated": bool(response.get("cursor")),
        }
        if index == 1 or index % 25 == 0 or index == len(signals):
            print(f"pulled {index}/{len(signals)}", flush=True)
        time.sleep(0.06)

    rows = [cached[signal.ticker] for signal in signals if signal.ticker in cached]
    descriptor, temporary = tempfile.mkstemp(
        prefix="execution_trades", dir=os.path.dirname(CACHE)
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(temporary, CACHE)
    except BaseException:
        os.unlink(temporary)
        raise
    return rows


def main() -> None:
    markets = study.load_markets()
    market_map = {market.ticker: market for market in markets}
    all_signals = breakout_study.breakout(
        markets, (8, 7), 0.85, 0.93, 0.03, 1
    )
    fresh = study.segment(all_signals, study.FRESH_START)
    records = pull(fresh)
    actual = []
    delays = []
    misses = 0
    truncated = 0
    for record in records:
        signal = next(row for row in fresh if row.ticker == record["ticker"])
        if record["truncated"]:
            truncated += 1
        same_side = [
            row for row in record["prints"]
            if row["side"] == signal.side and row["book_side"] == "ask"
        ]
        if not same_side:
            misses += 1
            continue
        first = same_side[0]
        entry = first[signal.side]
        delays.append(first["ts"] - record["decision_ts"])
        market = market_map[signal.ticker]
        actual.append(
            study.Trade(
                "actual_print_execution", signal.ticker, signal.close_ts,
                signal.side, entry,
                study.settlement_pnl(entry, market.result == signal.side),
                signal.signal_rem,
            )
        )

    model_by_ticker = {trade.ticker: trade for trade in fresh}
    slippage = [trade.entry - model_by_ticker[trade.ticker].entry for trade in actual]
    print(f"fresh signals={len(fresh)} records={len(records)} actual fills={len(actual)} misses={misses}")
    print(f"truncated six-second responses={truncated}")
    if delays:
        print(
            f"first same-side taker delay median={statistics.median(delays):.3f}s "
            f"p90={sorted(delays)[int(.90*(len(delays)-1))]:.3f}s"
        )
    if slippage:
        ordered = sorted(slippage)
        print(
            f"actual minus candle entry: mean={100*statistics.mean(slippage):+.3f}c "
            f"median={100*statistics.median(slippage):+.3f}c "
            f"p90={100*ordered[int(.90*(len(ordered)-1))]:+.3f}c"
        )
    print(study.summarize("candle modeled", [model_by_ticker[t.ticker] for t in actual]))
    print(study.summarize("actual trade print", actual))


if __name__ == "__main__":
    main()
