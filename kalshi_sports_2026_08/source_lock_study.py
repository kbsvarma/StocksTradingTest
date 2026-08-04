#!/usr/bin/env python3
"""Chronological holdout study of scheduled-source lock-in markets."""

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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_SERIES = (
    "KXAAAGASD",
    "KXAPPRANKFREE",
    "KXAPPRANKFREE2",
    "KXSPOTIFYD",
    "KXSPOTIFYGLOBALD",
    "KXSPOTIFYARTISTD",
    "KXTSAW",
    "KXFLIGHTJFK",
    "KXFLIGHTLAX",
    "KXFLIGHTORD",
    "KXRAINNYC",
)


def get_json(path: str, params: dict[str, Any] | None = None, retries: int = 6) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{BASE_URL}{path}{query}",
        headers={"User-Agent": "kalshi-source-lock-study/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise
            code = getattr(exc, "code", None)
            time.sleep(1.5 * (2**attempt) if code in (429, 500, 502, 503) else 1.0)
    raise RuntimeError("unreachable")


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def side_quotes(candle: dict, side: str, phase: str = "close") -> tuple[float, float] | None:
    bid_payload = candle.get("yes_bid") or {}
    ask_payload = candle.get("yes_ask") or {}
    bid = bid_payload.get(phase)
    ask = ask_payload.get(phase)
    if bid is None:
        bid = bid_payload.get(f"{phase}_dollars")
    if ask is None:
        ask = ask_payload.get(f"{phase}_dollars")
    if bid is None or ask is None:
        return None
    yes_bid, yes_ask = float(bid), float(ask)
    return (yes_bid, yes_ask) if side == "yes" else (1.0 - yes_ask, 1.0 - yes_bid)


def pull_events(series: str, maximum: int) -> list[dict]:
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
        if not cursor or len({market["event_ticker"] for market in markets}) >= maximum:
            break
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        by_event[market["event_ticker"]].append(market)
    events = [
        {
            "series": series,
            "event_ticker": ticker,
            "markets": event_markets,
            "close_ts": min(parse_ts(market["close_time"]) for market in event_markets),
        }
        for ticker, event_markets in by_event.items()
        if any(market.get("close_time") for market in event_markets)
    ]
    events.sort(key=lambda event: (event["close_ts"], event["event_ticker"]), reverse=True)
    return events[:maximum]


def cache_path(cache_dir: Path, event: dict) -> Path:
    return cache_dir / f"{event['series']}__{event['event_ticker']}.json"


def pull_event_candles(event: dict, cache_dir: Path) -> tuple[str, dict[str, list[dict]]]:
    path = cache_path(cache_dir, event)
    if path.exists():
        return event["event_ticker"], json.loads(path.read_text())
    payload = get_json(
        f"/series/{event['series']}/events/{event['event_ticker']}/candlesticks",
        {
            "start_ts": event["close_ts"] - (4 * 60 * 60) - 300,
            "end_ts": event["close_ts"] + 60,
            "period_interval": 1,
        },
    )
    tickers = payload.get("market_tickers", [])
    arrays = payload.get("market_candlesticks", [])
    output = {ticker: candles for ticker, candles in zip(tickers, arrays)}
    path.write_text(json.dumps(output, separators=(",", ":")))
    return event["event_ticker"], output


@dataclass(frozen=True)
class Config:
    hours_before_close: int
    band_low: float
    band_high: float
    persistence: int
    max_spread: float
    yes_only: bool


def candidate(event: dict, candle_map: dict[str, list[dict]], config: Config) -> dict | None:
    result_by_ticker = {market["ticker"]: market["result"] for market in event["markets"]}
    prepared: dict[str, tuple[list[int], dict[int, dict]]] = {}
    for ticker, rows in candle_map.items():
        if ticker not in result_by_ticker:
            continue
        candles = {int(row["end_period_ts"]): row for row in rows}
        prepared[ticker] = sorted(candles), candles

    window_start = event["close_ts"] - config.hours_before_close * 3600
    window_end = event["close_ts"] - 10 * 60
    signal_times = sorted(
        {
            timestamp
            for timestamps, _ in prepared.values()
            for timestamp in timestamps
            if window_start <= timestamp <= window_end
        }
    )
    for signal_ts in signal_times:
        choices = []
        for ticker, (timestamps, candles) in prepared.items():
            current = candles.get(signal_ts)
            if current is None:
                continue
            current_index = timestamps.index(signal_ts)
            prior_times = timestamps[max(0, current_index - config.persistence + 1) : current_index + 1]
            if len(prior_times) < config.persistence or signal_ts - prior_times[0] > 10 * 60:
                continue
            next_times = [timestamp for timestamp in timestamps[current_index + 1 :] if timestamp <= signal_ts + 5 * 60]
            if not next_times:
                continue
            next_candle = candles[next_times[0]]
            sides = ("yes",) if config.yes_only else ("yes", "no")
            for side in sides:
                quotes = side_quotes(current, side)
                if quotes is None:
                    continue
                bid, ask = quotes
                if not (config.band_low <= ask <= config.band_high):
                    continue
                if ask - bid > config.max_spread + 1e-12:
                    continue
                if any(
                    (prior := side_quotes(candles[timestamp], side)) is None
                    or prior[1] < config.band_low
                    for timestamp in prior_times
                ):
                    continue
                next_quote = side_quotes(next_candle, side, "open")
                if next_quote is None:
                    continue
                entry = max(ask, next_quote[1])
                if entry < 1.0:
                    choices.append((entry, ask - bid, ticker, side))
        if choices:
            entry, spread, ticker, side = min(choices)
            won = result_by_ticker[ticker] == side
            pnl = (1.0 - entry if won else -entry) - fee(entry)
            return {
                "series": event["series"],
                "event_ticker": event["event_ticker"],
                "ticker": ticker,
                "signal_ts": signal_ts,
                "close_ts": event["close_ts"],
                "side": side,
                "entry": entry,
                "spread": spread,
                "won": won,
                "pnl": pnl,
            }
    return None


def pvalue(trades: list[dict]) -> float:
    if len(trades) < 2:
        return 1.0
    values = [trade["pnl"] for trade in trades]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance <= 0:
        return 0.0 if mean > 0 else 1.0
    z_value = mean / math.sqrt(variance / len(values))
    return 0.5 * math.erfc(z_value / math.sqrt(2.0))


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    days = max(1.0, (max(t["close_ts"] for t in trades) - min(t["close_ts"] for t in trades)) / 86400)
    return {
        "n": len(trades),
        "win_rate": sum(t["won"] for t in trades) / len(trades),
        "mean_entry": sum(t["entry"] for t in trades) / len(trades),
        "mean_pnl_cents": 100.0 * sum(t["pnl"] for t in trades) / len(trades),
        "total_pnl_one_contract": sum(t["pnl"] for t in trades),
        "trades_per_day": len(trades) / days,
        "one_sided_p": pvalue(trades),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-per-series", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--series", default=",".join(DEFAULT_SERIES))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/source_lock_candles"))
    parser.add_argument("--output", type=Path, default=Path("data/source_lock_study.json"))
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    events = []
    for series in (item.strip() for item in args.series.split(",") if item.strip()):
        series_events = pull_events(series, args.events_per_series)
        events.extend(series_events)
        print(f"{series}: {len(series_events)} events", flush=True)

    candles: dict[str, dict[str, list[dict]]] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(pull_event_candles, event, args.cache_dir): event for event in events}
        for index, future in enumerate(as_completed(futures), start=1):
            event = futures[future]
            try:
                ticker, payload = future.result()
                candles[ticker] = payload
            except Exception as exc:
                failures[event["event_ticker"]] = repr(exc)
            if index % 100 == 0:
                print(f"candles: {index}/{len(futures)}", flush=True)

    configs = [
        Config(hour, low, high, persistence, spread, yes_only)
        for hour in (1, 2, 3)
        for low, high in ((0.80, 0.90), (0.90, 0.95), (0.95, 0.99))
        for persistence in (1, 3, 5)
        for spread in (0.01, 0.02)
        for yes_only in (True, False)
    ]
    studies = {}
    for series in sorted({event["series"] for event in events}):
        series_events = sorted(
            (event for event in events if event["series"] == series),
            key=lambda event: event["close_ts"],
        )
        split = int(len(series_events) * 0.60)
        train_ids = {event["event_ticker"] for event in series_events[:split]}
        rows = []
        trades_by_config = []
        for config in configs:
            trades = [
                trade
                for event in series_events
                if (trade := candidate(event, candles.get(event["event_ticker"], {}), config))
            ]
            train = [trade for trade in trades if trade["event_ticker"] in train_ids]
            holdout = [trade for trade in trades if trade["event_ticker"] not in train_ids]
            rows.append(
                {
                    "config": asdict(config),
                    "train": metrics(train),
                    "holdout": metrics(holdout),
                    "all": metrics(trades),
                }
            )
            trades_by_config.append(trades)
        eligible = [
            (index, row)
            for index, row in enumerate(rows)
            if row["train"].get("n", 0) >= 20 and row["train"].get("mean_pnl_cents", 0) > 0
        ]
        selected = max(
            eligible,
            key=lambda item: (item[1]["train"]["mean_pnl_cents"], item[1]["train"]["n"]),
            default=None,
        )
        selected_payload = None
        if selected:
            index, row = selected
            selected_payload = {**row, "trades": trades_by_config[index]}
        studies[series] = {
            "events": len(series_events),
            "configurations_tested": len(rows),
            "selected": selected_payload,
            "top_train": sorted(
                rows,
                key=lambda row: row["train"].get("mean_pnl_cents", -999),
                reverse=True,
            )[:10],
        }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": len(events),
        "candle_failures": failures,
        "studies": studies,
    }
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    compact = {
        series: {
            "events": study["events"],
            "selected": study["selected"],
        }
        for series, study in studies.items()
    }
    print(json.dumps(compact, indent=2))


if __name__ == "__main__":
    main()
