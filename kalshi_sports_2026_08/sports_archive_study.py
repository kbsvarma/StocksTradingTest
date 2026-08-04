#!/usr/bin/env python3
"""Causal archived-market test of simple late sports favorite strategies."""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_SERIES = (
    "KXWNBAGAME",
    "KXNBAGAME",
    "KXNHLGAME",
    "KXMLBGAME",
    "KXWTAMATCH",
)


def get_json(path: str, params: dict[str, Any] | None = None, retries: int = 6) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{BASE_URL}{path}{query}",
        headers={"User-Agent": "kalshi-sports-archive-study/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise
            code = getattr(exc, "code", None)
            delay = 1.5 * (2**attempt) if code in (429, 500, 502, 503) else 1.0
            time.sleep(delay)
    raise RuntimeError("unreachable")


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def side_quotes(candle: dict, side: str, phase: str = "close") -> tuple[float, float] | None:
    bid = (candle.get("yes_bid") or {}).get(phase)
    ask = (candle.get("yes_ask") or {}).get(phase)
    if bid is None or ask is None:
        return None
    yes_bid = float(bid)
    yes_ask = float(ask)
    if side == "yes":
        return yes_bid, yes_ask
    return 1.0 - yes_ask, 1.0 - yes_bid


def favorite(candle: dict, phase: str = "close") -> tuple[str, float, float] | None:
    quotes = side_quotes(candle, "yes", phase)
    if quotes is None:
        return None
    yes_bid, yes_ask = quotes
    side = "yes" if (yes_bid + yes_ask) / 2.0 >= 0.5 else "no"
    side_quote = side_quotes(candle, side, phase)
    if side_quote is None:
        return None
    return side, side_quote[0], side_quote[1]


def pull_series_markets(series: str, max_events: int) -> list[dict]:
    markets: list[dict] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"series_ticker": series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/historical/markets", params)
        markets.extend(
            market
            for market in payload.get("markets", [])
            if market.get("result") in ("yes", "no") and market.get("event_ticker")
        )
        cursor = payload.get("cursor") or ""
        if not cursor or len({item["event_ticker"] for item in markets}) >= max_events:
            break

    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        by_event[market["event_ticker"]].append(market)

    # A single binary contract exposes both YES and NO routes. Selecting a
    # deterministic ticker avoids using future volume to choose the route.
    events = []
    for event_ticker, event_markets in by_event.items():
        market = min(event_markets, key=lambda item: item["ticker"])
        events.append(market)
    events.sort(key=lambda item: (item.get("close_time") or "", item["ticker"]), reverse=True)
    return events[:max_events]


def candle_cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / f"{ticker}.json"


def pull_market_candles(market: dict, cache_dir: Path) -> tuple[str, list[dict]]:
    ticker = market["ticker"]
    path = candle_cache_path(cache_dir, ticker)
    if path.exists():
        return ticker, json.loads(path.read_text())
    close_ts = parse_ts(market["close_time"])
    payload = get_json(
        f"/historical/markets/{ticker}/candlesticks",
        {
            "start_ts": close_ts - (10 * 60 * 60),
            "end_ts": close_ts + 120,
            "period_interval": 1,
        },
    )
    candles = payload.get("candlesticks", [])
    path.write_text(json.dumps(candles, separators=(",", ":")))
    return ticker, candles


@dataclass(frozen=True)
class Config:
    band_low: float
    band_high: float
    persistence: int
    max_spread: float
    min_expected_minutes: int
    max_expected_minutes: int


def candidate_trade(market: dict, candles: list[dict], config: Config) -> dict | None:
    expected_raw = market.get("expected_expiration_time") or market.get("close_time")
    if not expected_raw:
        return None
    expected_ts = parse_ts(expected_raw)
    close_ts = parse_ts(market["close_time"])
    ordered = sorted(
        (candle for candle in candles if int(candle.get("end_period_ts") or 0) < close_ts),
        key=lambda item: int(item["end_period_ts"]),
    )
    for index, candle in enumerate(ordered[:-1]):
        now_ts = int(candle["end_period_ts"])
        expected_minutes = (expected_ts - now_ts) / 60.0
        if not (config.min_expected_minutes <= expected_minutes <= config.max_expected_minutes):
            continue
        quote = favorite(candle)
        if quote is None:
            continue
        side, bid, ask = quote
        if not (config.band_low <= ask <= config.band_high):
            continue
        if ask - bid > config.max_spread + 1e-12:
            continue
        if index + 1 < config.persistence:
            continue
        confirmed = True
        for prior in ordered[index - config.persistence + 1 : index + 1]:
            prior_quote = favorite(prior)
            if prior_quote is None or prior_quote[0] != side or prior_quote[2] < config.band_low:
                confirmed = False
                break
        if not confirmed:
            continue
        next_candle = ordered[index + 1]
        if int(next_candle["end_period_ts"]) - now_ts > 120:
            continue
        entry_quote = side_quotes(next_candle, side, "open")
        if entry_quote is None:
            continue
        entry = max(ask, entry_quote[1])
        if entry >= 1.0:
            continue
        won = market["result"] == side
        pnl = (1.0 - entry if won else -entry) - fee(entry)
        return {
            "series": market["series"],
            "event_ticker": market["event_ticker"],
            "ticker": market["ticker"],
            "signal_ts": now_ts,
            "close_ts": close_ts,
            "side": side,
            "entry": entry,
            "won": won,
            "pnl": pnl,
        }
    return None


def normal_pvalue(trades: list[dict]) -> float:
    if len(trades) < 2:
        return 1.0
    values = [trade["pnl"] for trade in trades]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance <= 0:
        return 0.0 if mean > 0 else 1.0
    t_value = mean / math.sqrt(variance / len(values))
    return 0.5 * math.erfc(t_value / math.sqrt(2.0))


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    values = [trade["pnl"] for trade in trades]
    days = max(1.0, (max(t["close_ts"] for t in trades) - min(t["close_ts"] for t in trades)) / 86400)
    return {
        "n": len(trades),
        "win_rate": sum(trade["won"] for trade in trades) / len(trades),
        "mean_entry": sum(trade["entry"] for trade in trades) / len(trades),
        "mean_pnl_cents": 100.0 * sum(values) / len(values),
        "total_pnl_one_contract": sum(values),
        "trades_per_day": len(trades) / days,
        "one_sided_p": normal_pvalue(trades),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-per-series", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/archive_candles"))
    parser.add_argument("--output", type=Path, default=Path("data/sports_archive_study.json"))
    parser.add_argument("--series", default=",".join(DEFAULT_SERIES))
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    all_markets: list[dict] = []
    for series in (item.strip() for item in args.series.split(",") if item.strip()):
        markets = pull_series_markets(series, args.events_per_series)
        for market in markets:
            market["series"] = series
        all_markets.extend(markets)
        print(f"{series}: {len(markets)} archived events", flush=True)

    candles_by_ticker: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(pull_market_candles, market, args.cache_dir): market["ticker"]
            for market in all_markets
        }
        for index, future in enumerate(as_completed(futures), start=1):
            ticker = futures[future]
            try:
                fetched_ticker, candles = future.result()
                candles_by_ticker[fetched_ticker] = candles
            except Exception as exc:
                failures[ticker] = repr(exc)
            if index % 100 == 0:
                print(f"candles: {index}/{len(futures)}", flush=True)

    configs = [
        Config(low, high, persistence, spread, 10, max_minutes)
        for low, high in ((0.80, 0.92), (0.90, 0.95), (0.93, 0.99), (0.95, 0.99))
        for persistence in (1, 3, 5)
        for spread in (0.01, 0.02, 0.03)
        for max_minutes in (90, 180)
    ]
    market_days = sorted({datetime.utcfromtimestamp(parse_ts(m["close_time"])).date() for m in all_markets})
    split_day = market_days[int(len(market_days) * 0.60)] if market_days else None
    rows = []
    trades_by_config: list[list[dict]] = []
    for config in configs:
        trades = []
        for market in all_markets:
            trade = candidate_trade(market, candles_by_ticker.get(market["ticker"], []), config)
            if trade:
                trades.append(trade)
        trades.sort(key=lambda item: item["close_ts"])
        train = [
            trade
            for trade in trades
            if split_day and datetime.utcfromtimestamp(trade["close_ts"]).date() < split_day
        ]
        holdout = [trade for trade in trades if trade not in train]
        rows.append(
            {
                "config": config.__dict__,
                "train": metrics(train),
                "holdout": metrics(holdout),
                "all": metrics(trades),
            }
        )
        trades_by_config.append(trades)

    eligible = [
        (index, row)
        for index, row in enumerate(rows)
        if row["train"].get("n", 0) >= 75 and row["train"].get("mean_pnl_cents", 0) > 0
    ]
    selected = max(
        eligible,
        key=lambda item: (item[1]["train"]["mean_pnl_cents"], item[1]["train"]["n"]),
        default=None,
    )
    selected_payload = None
    if selected:
        selected_index, selected_row = selected
        selected_payload = {
            **selected_row,
            "trades": trades_by_config[selected_index],
        }

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "series": sorted({market["series"] for market in all_markets}),
        "events": len(all_markets),
        "events_by_series": {
            series: sum(market["series"] == series for market in all_markets)
            for series in sorted({market["series"] for market in all_markets})
        },
        "candle_failures": failures,
        "split_day": str(split_day) if split_day else None,
        "configurations_tested": len(rows),
        "selected": selected_payload,
        "top_train": sorted(
            rows,
            key=lambda row: row["train"].get("mean_pnl_cents", -999),
            reverse=True,
        )[:20],
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in output.items() if key != "top_train"}, indent=2))


if __name__ == "__main__":
    main()
