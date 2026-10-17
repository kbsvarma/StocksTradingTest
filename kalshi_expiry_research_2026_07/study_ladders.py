#!/usr/bin/env python3
"""Event-level tests for multi-strike commodity and hourly-crypto ladders."""

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


def event_ticker(market: dict) -> str:
    if market.get("event_ticker"):
        return market["event_ticker"]
    ticker = market["ticker"]
    return ticker.rsplit("-T", 1)[0] if "-T" in ticker else ticker


def normalize_hourly_candle(candle: dict) -> dict:
    if "bc" in candle:
        return candle
    return {
        "t": candle["t"],
        "bc": candle.get("b"),
        "ac": candle.get("a"),
        "bo": candle.get("b"),
        "ao": candle.get("a"),
        "v": candle.get("v"),
    }


def load_candles(path: Path) -> dict[str, dict[int, dict]]:
    output: dict[str, dict[int, dict]] = {}
    for record in load_jsonl(path):
        output[record["ticker"]] = {
            int(candle["t"]): normalize_hourly_candle(candle)
            for candle in record.get("candles", [])
        }
    return output


def split_event_ids(close_by_event: dict[str, int]) -> tuple[set[str], set[str]]:
    days = sorted({utc_day(value) for value in close_by_event.values()})
    train_days = set(days[: int(len(days) * 0.60)])
    return (
        {
            event
            for event, close_ts in close_by_event.items()
            if utc_day(close_ts) in train_days
        },
        {
            event
            for event, close_ts in close_by_event.items()
            if utc_day(close_ts) not in train_days
        },
    )


def candidate_trade(
    market: dict,
    candles: dict[int, dict],
    config: dict,
    period_seconds: int,
    strategy: str,
) -> dict | None:
    if market.get("result") not in ("yes", "no") or not candles:
        return None
    close_ts = timestamp(market["close_time"])
    signal_ts = close_ts - config["periods_remaining"] * period_seconds
    signal_candle = candles.get(signal_ts)
    quote = favorite_quote(signal_candle) if signal_candle else None
    if quote is None:
        return None
    side, bid, ask = quote
    if not config["band_low"] <= ask <= config["band_high"]:
        return None
    if ask - bid > config["maximum_spread"] + 1e-9:
        return None
    for period in range(config["persistence_periods"]):
        prior = candles.get(signal_ts - period * period_seconds)
        prior_quote = favorite_quote(prior) if prior else None
        if prior_quote is None or prior_quote[0] != side:
            return None

    prior_volume = sum(
        number(candle.get("v")) or 0.0
        for candle_ts, candle in candles.items()
        if candle_ts <= signal_ts
    )
    if prior_volume < config["minimum_prior_volume"]:
        return None

    next_candle = candles.get(signal_ts + period_seconds)
    next_quote = side_quote(next_candle, side, phase="open") if next_candle else None
    if next_quote is None:
        return None
    entry = max(ask, next_quote[1])
    if entry > 0.99:
        return None
    won = market["result"] == side
    pnl = (1.0 - entry if won else -entry) - fee(entry)
    return {
        "strategy": strategy,
        "event_ticker": event_ticker(market),
        "ticker": market["ticker"],
        "ts": signal_ts + period_seconds,
        "cluster": utc_day(close_ts),
        "side": side,
        "entry": entry,
        "signal_ask": ask,
        "spread": ask - bid,
        "prior_volume": prior_volume,
        "win": won,
        "pnl": pnl,
    }


def select_one(candidates: list[dict], selector: str) -> dict | None:
    if not candidates:
        return None
    if selector == "lowest_entry":
        return min(candidates, key=lambda item: (item["entry"], item["spread"], item["ticker"]))
    if selector == "highest_entry":
        return max(candidates, key=lambda item: (item["entry"], -item["spread"], item["ticker"]))
    return min(
        candidates,
        key=lambda item: (item["spread"], -item["prior_volume"], item["ticker"]),
    )


def run_config(
    events: dict[str, list[dict]],
    candles: dict[str, dict[int, dict]],
    allowed_events: set[str],
    config: dict,
    period_seconds: int,
    strategy: str,
) -> list[dict]:
    trades = []
    for event, markets in events.items():
        if event not in allowed_events:
            continue
        candidates = [
            trade
            for market in markets
            for trade in [
                candidate_trade(
                    market,
                    candles.get(market["ticker"], {}),
                    config,
                    period_seconds,
                    strategy,
                )
            ]
            if trade
        ]
        selected = select_one(candidates, config["selector"])
        if selected:
            trades.append(selected)
    return trades


def choose_configuration(rows: list[dict], minimum_trades: int) -> dict | None:
    eligible = [
        row
        for row in rows
        if row["metrics"]["n"] >= minimum_trades
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


def study(
    sources: list[tuple[Path, str, str]],
    output_dir: Path,
    strategy: str,
    period_seconds: int,
    remaining: tuple[int, ...],
    bands: tuple[tuple[float, float], ...],
    persistences: tuple[int, ...],
    spreads: tuple[float, ...],
    minimum_train_trades: int,
) -> dict:
    markets: list[dict] = []
    candles: dict[str, dict[int, dict]] = {}
    for source_dir, series, candle_name in sources:
        markets.extend(load_jsonl(source_dir / f"markets_{series}.jsonl"))
        candles.update(load_candles(source_dir / candle_name))

    events: dict[str, list[dict]] = defaultdict(list)
    close_by_event: dict[str, int] = {}
    for market in markets:
        event = event_ticker(market)
        events[event].append(market)
        if market.get("close_time"):
            close_by_event[event] = timestamp(market["close_time"])
    train_events, holdout_events = split_event_ids(close_by_event)

    configurations = [
        {
            "periods_remaining": periods,
            "band_low": low,
            "band_high": high,
            "persistence_periods": persistence,
            "maximum_spread": spread,
            "minimum_prior_volume": 10.0,
            "selector": selector,
        }
        for periods, (low, high), persistence, spread, selector in itertools.product(
            remaining,
            bands,
            persistences,
            spreads,
            ("lowest_entry", "highest_entry", "tightest_spread"),
        )
    ]
    rows = []
    for config in configurations:
        trades = run_config(
            events,
            candles,
            train_events,
            config,
            period_seconds,
            strategy,
        )
        rows.append({"config": config, "metrics": summarize(trades)})
    benjamini_hochberg(rows, "exact_break_even_p")
    selected = choose_configuration(rows, minimum_train_trades)

    result = {
        "study": strategy,
        "event_count": len(events),
        "train_events": len(train_events),
        "holdout_events": len(holdout_events),
        "tested_configurations": len(rows),
        "selected": None,
        "top_train": sorted(
            rows,
            key=lambda row: (
                row["metrics"]["exact_break_even_p_bh"],
                -row["metrics"]["mean_pnl_cents"],
            ),
        )[:10],
    }
    if selected is None:
        return result

    holdout_trades = run_config(
        events,
        candles,
        holdout_events,
        selected["config"],
        period_seconds,
        strategy,
    )
    trade_path = output_dir / f"trades_{strategy}_holdout.jsonl"
    with trade_path.open("w") as handle:
        for trade in holdout_trades:
            handle.write(json.dumps(trade, sort_keys=True) + "\n")
    result["selected"] = {
        "config": selected["config"],
        "train_metrics": selected["metrics"],
        "holdout_metrics": summarize(holdout_trades),
    }
    holdout_metrics = result["selected"]["holdout_metrics"]
    checks = {
        "train_multiple_testing_p_le_0_05": (
            selected["metrics"]["exact_break_even_p_bh"] <= 0.05
        ),
        "holdout_sample_ge_20": holdout_metrics["n"] >= 20,
        "holdout_mean_positive": holdout_metrics["mean_pnl_cents"] > 0,
        "holdout_exact_p_le_0_05": holdout_metrics["exact_break_even_p"] <= 0.05,
        "holdout_cluster_bootstrap_low_positive": (
            holdout_metrics["bootstrap_mean_low_cents"] > 0
        ),
    }
    result["validation"] = {
        "passed": all(checks.values()),
        "checks": checks,
        "failure_reasons": [name for name, passed in checks.items() if not passed],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timely-dir",
        type=Path,
        default=Path("../kalshi_timely_2026_07/data"),
    )
    parser.add_argument(
        "--crypto-dir",
        type=Path,
        default=Path("../kalshi_crypto15m_2026_07/data"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument(
        "--study",
        choices=("all", "commodities", "hourly_crypto"),
        default="all",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    if args.study in ("all", "commodities"):
        results.append(
            study(
                [
                    (
                        args.timely_dir,
                        ticker,
                        f"candles_{ticker}.jsonl",
                    )
                    for ticker in ("KXWTI", "KXBRENTD", "KXGOLDD")
                ],
                args.output_dir,
                "commodity_ladder_closer",
                period_seconds=3600,
                remaining=(8, 6, 5, 4, 3, 2),
                bands=((0.80, 0.8999), (0.90, 0.9499), (0.95, 0.99)),
                persistences=(1, 2),
                spreads=(0.02, 0.04),
                minimum_train_trades=30,
            )
        )
    if args.study in ("all", "hourly_crypto"):
        results.append(
            study(
                [
                    (
                        args.crypto_dir,
                        "KXBTCD",
                        "candles_KXBTCD.jsonl",
                    )
                ],
                args.output_dir,
                "hourly_crypto_ladder_closer",
                period_seconds=60,
                remaining=(10, 7, 5, 3),
                bands=((0.90, 0.9499), (0.95, 0.9799), (0.98, 0.99)),
                persistences=(3, 6),
                spreads=(0.01, 0.02),
                minimum_train_trades=100,
            )
        )

    output = args.output_dir / "ladder_studies.json"
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
