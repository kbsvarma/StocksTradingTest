#!/usr/bin/env python3
"""Causal train/holdout tests for sports and fixed-expiry closer designs."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_common import (
    benjamini_hochberg,
    favorite_quote,
    fee,
    load_jsonl,
    number,
    side_quote,
    summarize,
)


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def utc_day(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")


def candle_map(records: list[dict]) -> dict[str, dict[int, dict]]:
    return {
        record["ticker"]: {
            int(candle["t"]): candle for candle in record.get("candles", [])
        }
        for record in records
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def cs2_trade(
    event_ticker: str,
    markets: list[dict],
    candles: dict[str, dict[int, dict]],
    config: dict,
) -> dict | None:
    valid = [
        market
        for market in markets
        if market.get("result") in ("yes", "no")
        and market["ticker"] in candles
        and candles[market["ticker"]]
    ]
    if len(valid) < 2 or sum(market["result"] == "yes" for market in valid) != 1:
        return None

    market_by_ticker = {market["ticker"]: market for market in valid}
    all_times = sorted(
        set().union(*(set(candles[market["ticker"]]) for market in valid))
    )
    if not all_times:
        return None
    open_ts = min(timestamp(market["open_time"]) for market in valid)

    baseline_mid: dict[str, float] = {}
    favorite_at: dict[int, tuple[str, float, float, float]] = {}
    for candle_ts in all_times:
        choices: list[tuple[float, str, float, float]] = []
        for market in valid:
            candle = candles[market["ticker"]].get(candle_ts)
            quote = side_quote(candle, "yes") if candle else None
            if quote is None:
                continue
            bid, ask = quote
            if not 0.0 <= bid <= ask <= 1.0:
                continue
            mid = (bid + ask) / 2.0
            baseline_mid.setdefault(market["ticker"], mid)
            choices.append((mid, market["ticker"], bid, ask))
        if choices:
            mid, ticker, bid, ask = max(choices)
            favorite_at[candle_ts] = ticker, bid, ask, mid

    for signal_ts in sorted(favorite_at):
        if signal_ts < open_ts + config["minimum_age_minutes"] * 60:
            continue
        ticker, bid, ask, mid = favorite_at[signal_ts]
        if mid < 0.5 or not config["band_low"] <= ask <= config["band_high"]:
            continue
        if ask - bid > config["maximum_spread"] + 1e-9:
            continue
        if mid - baseline_mid.get(ticker, mid) < config["minimum_move"]:
            continue

        persistent = True
        for minute in range(config["persistence_minutes"]):
            prior = favorite_at.get(signal_ts - minute * 60)
            if prior is None or prior[0] != ticker:
                persistent = False
                break
        if not persistent:
            continue

        comparison = favorite_at.get(signal_ts - 5 * 60)
        if comparison is None or comparison[0] != ticker:
            continue
        if ask - comparison[2] < config["minimum_recent_move"]:
            continue

        next_candle = candles[ticker].get(signal_ts + 60)
        next_quote = side_quote(next_candle, "yes", "open") if next_candle else None
        if next_quote is None:
            continue
        entry = max(ask, next_quote[1])
        if entry > 0.99:
            continue
        won = market_by_ticker[ticker]["result"] == "yes"
        pnl = (1.0 - entry if won else -entry) - fee(entry)
        return {
            "strategy": "cs2_inplay_closer",
            "event_ticker": event_ticker,
            "ticker": ticker,
            "ts": signal_ts + 60,
            "cluster": utc_day(signal_ts),
            "side": "yes",
            "entry": entry,
            "signal_ask": ask,
            "spread": ask - bid,
            "win": won,
            "pnl": pnl,
        }
    return None


def fixed_expiry_trade(
    market: dict,
    candles: dict[int, dict],
    config: dict,
    strategy: str,
) -> dict | None:
    if market.get("result") not in ("yes", "no") or not candles:
        return None
    close_ts = timestamp(market["close_time"])
    signal_ts = close_ts - config["minutes_remaining"] * 60
    signal_candle = candles.get(signal_ts)
    quote = favorite_quote(signal_candle) if signal_candle else None
    if quote is None:
        return None
    side, bid, ask = quote
    if not config["band_low"] <= ask <= config["band_high"]:
        return None
    if ask - bid > config["maximum_spread"] + 1e-9:
        return None
    for minute in range(config["persistence_minutes"]):
        prior = candles.get(signal_ts - minute * 60)
        prior_quote = favorite_quote(prior) if prior else None
        if prior_quote is None or prior_quote[0] != side:
            return None
    next_candle = candles.get(signal_ts + 60)
    next_quote = favorite_quote(next_candle, phase="open") if next_candle else None
    if next_quote is None or next_quote[0] != side:
        return None
    entry = max(ask, next_quote[2])
    if entry > 0.99:
        return None
    won = market["result"] == side
    pnl = (1.0 - entry if won else -entry) - fee(entry)
    return {
        "strategy": strategy,
        "event_ticker": market.get("event_ticker") or market["ticker"],
        "ticker": market["ticker"],
        "ts": signal_ts + 60,
        "cluster": utc_day(close_ts),
        "side": side,
        "entry": entry,
        "signal_ask": ask,
        "spread": ask - bid,
        "win": won,
        "pnl": pnl,
    }


def split_ids(items: list[tuple[str, int]], fraction: float = 0.60) -> tuple[set[str], set[str]]:
    """Chronologically split whole UTC days to prevent same-day leakage."""
    days = sorted({utc_day(item[1]) for item in items})
    cut = int(len(days) * fraction)
    train_days = set(days[:cut])
    train = {item[0] for item in items if utc_day(item[1]) in train_days}
    holdout = {item[0] for item in items if utc_day(item[1]) not in train_days}
    return train, holdout


def choose_configuration(
    rows: list[dict],
    minimum_train_trades: int,
) -> dict | None:
    eligible = [
        row
        for row in rows
        if row["metrics"]["n"] >= minimum_train_trades
        and row["metrics"]["mean_pnl_cents"] > 0
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            row["metrics"]["exact_break_even_p_bh"],
            -row["metrics"]["mean_pnl_cents"],
            -row["metrics"]["n"],
        ),
    )


def run_cs2(data_dir: Path, output_dir: Path) -> dict:
    markets = load_jsonl(data_dir / "markets_KXCS2MAP.jsonl")
    candles = candle_map(load_jsonl(data_dir / "candles_KXCS2MAP_1m.jsonl"))
    by_event: dict[str, list[dict]] = defaultdict(list)
    close_by_event: dict[str, int] = {}
    for market in markets:
        event = market.get("event_ticker")
        if not event:
            continue
        by_event[event].append(market)
        close_by_event[event] = max(
            close_by_event.get(event, 0),
            timestamp(market["close_time"]),
        )
    train_ids, holdout_ids = split_ids(list(close_by_event.items()))
    configurations = [
        {
            "band_low": low,
            "band_high": high,
            "persistence_minutes": persistence,
            "maximum_spread": spread,
            "minimum_move": movement,
            "minimum_recent_move": recent,
            "minimum_age_minutes": 20,
        }
        for (low, high), persistence, spread, movement, recent in itertools.product(
            ((0.80, 0.8999), (0.90, 0.9499), (0.95, 0.99)),
            (2, 4),
            (0.02, 0.04),
            (0.03, 0.08),
            (0.00, 0.02),
        )
    ]
    rows: list[dict] = []
    trade_cache: list[list[dict]] = []
    for config in configurations:
        trades = [
            trade
            for event, event_markets in by_event.items()
            if event in train_ids
            for trade in [cs2_trade(event, event_markets, candles, config)]
            if trade
        ]
        rows.append({"config": config, "metrics": summarize(trades)})
        trade_cache.append(trades)
    benjamini_hochberg(rows, "exact_break_even_p")
    selected = choose_configuration(rows, minimum_train_trades=75)
    if selected is None:
        return {
            "study": "cs2_inplay_closer",
            "events": len(by_event),
            "train_events": len(train_ids),
            "holdout_events": len(holdout_ids),
            "selected": None,
            "top_train": sorted(
                rows,
                key=lambda row: -row["metrics"]["mean_pnl_cents"],
            )[:10],
        }

    selected_config = selected["config"]
    holdout_trades = [
        trade
        for event, event_markets in by_event.items()
        if event in holdout_ids
        for trade in [cs2_trade(event, event_markets, candles, selected_config)]
        if trade
    ]
    write_jsonl(output_dir / "trades_cs2_holdout.jsonl", holdout_trades)
    holdout_metrics = summarize(holdout_trades)
    checks = {
        "train_multiple_testing_p_le_0_05": (
            selected["metrics"]["exact_break_even_p_bh"] <= 0.05
        ),
        "holdout_sample_ge_50": holdout_metrics["n"] >= 50,
        "holdout_mean_positive": holdout_metrics["mean_pnl_cents"] > 0,
        "holdout_exact_p_le_0_05": holdout_metrics["exact_break_even_p"] <= 0.05,
        "holdout_cluster_bootstrap_low_positive": (
            holdout_metrics["bootstrap_mean_low_cents"] > 0
        ),
    }
    return {
        "study": "cs2_inplay_closer",
        "events": len(by_event),
        "train_events": len(train_ids),
        "holdout_events": len(holdout_ids),
        "tested_configurations": len(rows),
        "selected": {
            "config": selected_config,
            "train_metrics": selected["metrics"],
            "holdout_metrics": holdout_metrics,
        },
        "validation": {
            "passed": all(checks.values()),
            "checks": checks,
            "failure_reasons": [
                name for name, passed in checks.items() if not passed
            ],
        },
        "top_train": sorted(
            rows,
            key=lambda row: (
                row["metrics"]["exact_break_even_p_bh"],
                -row["metrics"]["mean_pnl_cents"],
            ),
        )[:10],
    }


def run_fixed(
    data_dir: Path,
    output_dir: Path,
    series: list[str],
    strategy: str,
    remaining_minutes: tuple[int, ...],
    minimum_train_trades: int,
) -> dict:
    markets: list[dict] = []
    candles: dict[str, dict[int, dict]] = {}
    for ticker in series:
        markets.extend(load_jsonl(data_dir / f"markets_{ticker}.jsonl"))
        candles.update(
            candle_map(load_jsonl(data_dir / f"candles_{ticker}_1m.jsonl"))
        )
    close_items = [
        (
            market.get("event_ticker") or market["ticker"],
            timestamp(market["close_time"]),
        )
        for market in markets
        if market.get("close_time")
    ]
    train_ids, holdout_ids = split_ids(close_items)
    configurations = [
        {
            "minutes_remaining": minutes,
            "band_low": low,
            "band_high": high,
            "persistence_minutes": persistence,
            "maximum_spread": spread,
        }
        for minutes, (low, high), persistence, spread in itertools.product(
            remaining_minutes,
            ((0.80, 0.8999), (0.90, 0.9499), (0.95, 0.99)),
            (3, 5),
            (0.02, 0.04),
        )
    ]
    rows: list[dict] = []
    for config in configurations:
        trades = []
        for market in markets:
            event = market.get("event_ticker") or market["ticker"]
            if event not in train_ids:
                continue
            trade = fixed_expiry_trade(
                market,
                candles.get(market["ticker"], {}),
                config,
                strategy,
            )
            if trade:
                trades.append(trade)
        rows.append({"config": config, "metrics": summarize(trades)})
    benjamini_hochberg(rows, "exact_break_even_p")
    selected = choose_configuration(rows, minimum_train_trades)
    if selected is None:
        return {
            "study": strategy,
            "series": series,
            "events": len(close_items),
            "selected": None,
            "top_train": sorted(
                rows,
                key=lambda row: -row["metrics"]["mean_pnl_cents"],
            )[:10],
        }

    selected_config = selected["config"]
    holdout_trades = []
    for market in markets:
        event = market.get("event_ticker") or market["ticker"]
        if event not in holdout_ids:
            continue
        trade = fixed_expiry_trade(
            market,
            candles.get(market["ticker"], {}),
            selected_config,
            strategy,
        )
        if trade:
            holdout_trades.append(trade)
    write_jsonl(output_dir / f"trades_{strategy}_holdout.jsonl", holdout_trades)
    holdout_metrics = summarize(holdout_trades)
    checks = {
        "train_multiple_testing_p_le_0_05": (
            selected["metrics"]["exact_break_even_p_bh"] <= 0.05
        ),
        "holdout_sample_ge_15": holdout_metrics["n"] >= 15,
        "holdout_mean_positive": holdout_metrics["mean_pnl_cents"] > 0,
        "holdout_exact_p_le_0_05": holdout_metrics["exact_break_even_p"] <= 0.05,
        "holdout_cluster_bootstrap_low_positive": (
            holdout_metrics["bootstrap_mean_low_cents"] > 0
        ),
    }
    return {
        "study": strategy,
        "series": series,
        "events": len(close_items),
        "train_events": len(train_ids),
        "holdout_events": len(holdout_ids),
        "tested_configurations": len(rows),
        "selected": {
            "config": selected_config,
            "train_metrics": selected["metrics"],
            "holdout_metrics": holdout_metrics,
        },
        "validation": {
            "passed": all(checks.values()),
            "checks": checks,
            "failure_reasons": [
                name for name, passed in checks.items() if not passed
            ],
        },
        "top_train": sorted(
            rows,
            key=lambda row: (
                row["metrics"]["exact_break_even_p_bh"],
                -row["metrics"]["mean_pnl_cents"],
            ),
        )[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--study",
        choices=("all", "cs2", "indices", "rain"),
        default="all",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    if args.study in ("all", "cs2"):
        results.append(run_cs2(args.data_dir, args.output_dir))
    if args.study in ("all", "indices"):
        results.append(
            run_fixed(
                args.data_dir,
                args.output_dir,
                ["KXINXDUD", "KXNASDAQDUD"],
                "daily_index_closer",
                (30, 15, 10, 5, 3, 2),
                minimum_train_trades=10,
            )
        )
    if args.study in ("all", "rain"):
        results.append(
            run_fixed(
                args.data_dir,
                args.output_dir,
                ["KXRAINNYC"],
                "nyc_rain_closer",
                (480, 240, 120, 60, 30, 15, 5),
                minimum_train_trades=10,
            )
        )
    output = args.output_dir / "closer_studies.json"
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
