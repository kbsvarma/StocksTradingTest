#!/usr/bin/env python3
"""Test best-route CS2 favorite and recovering-underdog strategies."""

from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_common import (
    benjamini_hochberg,
    fee,
    load_jsonl,
    number,
    summarize,
)


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def utc_day(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")


def load_candles(path: Path) -> dict[str, dict[int, dict]]:
    return {
        record["ticker"]: {
            int(candle["t"]): candle for candle in record.get("candles", [])
        }
        for record in load_jsonl(path)
    }


def yes_quote(candle: dict | None, phase: str = "close") -> tuple[float, float] | None:
    if not candle:
        return None
    suffix = "c" if phase == "close" else "o"
    bid = number(candle.get(f"b{suffix}"))
    ask = number(candle.get(f"a{suffix}"))
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return bid, ask


def outcome_snapshot(
    event_markets: list[dict],
    candles: dict[str, dict[int, dict]],
    candle_ts: int,
    phase: str = "close",
) -> list[dict] | None:
    if len(event_markets) != 2:
        return None
    quotes = [
        yes_quote(candles.get(market["ticker"], {}).get(candle_ts), phase)
        for market in event_markets
    ]
    if any(quote is None for quote in quotes):
        return None
    output = []
    for index, market in enumerate(event_markets):
        other_index = 1 - index
        direct_bid, direct_ask = quotes[index]
        other_bid, other_ask = quotes[other_index]
        synthetic_bid = 1.0 - other_ask
        synthetic_ask = 1.0 - other_bid
        routes = [
            {
                "ticker": market["ticker"],
                "side": "yes",
                "bid": direct_bid,
                "ask": direct_ask,
            },
            {
                "ticker": event_markets[other_index]["ticker"],
                "side": "no",
                "bid": synthetic_bid,
                "ask": synthetic_ask,
            },
        ]
        route = min(routes, key=lambda item: (item["ask"], item["ask"] - item["bid"]))
        direct_mid = (direct_bid + direct_ask) / 2.0
        synthetic_mid = (synthetic_bid + synthetic_ask) / 2.0
        output.append(
            {
                "outcome_ticker": market["ticker"],
                "probability_mid": (direct_mid + synthetic_mid) / 2.0,
                "route": route,
            }
        )
    return output


def route_trade(
    event: str,
    event_markets: list[dict],
    candles: dict[str, dict[int, dict]],
    config: dict,
    lane: str,
) -> dict | None:
    if (
        len(event_markets) != 2
        or sum(market.get("result") == "yes" for market in event_markets) != 1
    ):
        return None
    market_by_ticker = {market["ticker"]: market for market in event_markets}
    common_times = sorted(
        set(candles.get(event_markets[0]["ticker"], {})).intersection(
            candles.get(event_markets[1]["ticker"], {})
        )
    )
    if not common_times:
        return None
    open_ts = max(timestamp(market["open_time"]) for market in event_markets)
    snapshots = {
        candle_ts: outcome_snapshot(event_markets, candles, candle_ts)
        for candle_ts in common_times
    }
    snapshots = {key: value for key, value in snapshots.items() if value}
    first_snapshot = next(iter(snapshots.values()), None)
    if not first_snapshot:
        return None
    baseline = {
        item["outcome_ticker"]: item["probability_mid"] for item in first_snapshot
    }

    for signal_ts in sorted(snapshots):
        if signal_ts < open_ts + 20 * 60:
            continue
        ordered = sorted(
            snapshots[signal_ts],
            key=lambda item: item["probability_mid"],
        )
        selected = ordered[-1] if lane == "favorite" else ordered[0]
        route = selected["route"]
        ask = route["ask"]
        spread = route["ask"] - route["bid"]
        if not config["band_low"] <= ask <= config["band_high"]:
            continue
        if spread > config["maximum_spread"] + 1e-9:
            continue
        baseline_change = (
            selected["probability_mid"] - baseline[selected["outcome_ticker"]]
        )
        if lane == "favorite" and baseline_change < config["minimum_baseline_move"]:
            continue

        persistent = True
        for minute in range(config["persistence_minutes"]):
            prior = snapshots.get(signal_ts - minute * 60)
            if prior is None:
                persistent = False
                break
            prior_ordered = sorted(prior, key=lambda item: item["probability_mid"])
            prior_selected = prior_ordered[-1] if lane == "favorite" else prior_ordered[0]
            if prior_selected["outcome_ticker"] != selected["outcome_ticker"]:
                persistent = False
                break
        if not persistent:
            continue

        comparison = snapshots.get(signal_ts - 5 * 60)
        if comparison is None:
            continue
        comparison_item = next(
            (
                item
                for item in comparison
                if item["outcome_ticker"] == selected["outcome_ticker"]
            ),
            None,
        )
        if comparison_item is None:
            continue
        recent_move = (
            selected["probability_mid"] - comparison_item["probability_mid"]
        )
        if lane == "underdog" and recent_move < config["minimum_recent_move"]:
            continue

        next_snapshot = outcome_snapshot(
            event_markets,
            candles,
            signal_ts + 60,
            phase="open",
        )
        if next_snapshot is None:
            continue
        next_item = next(
            (
                item
                for item in next_snapshot
                if item["outcome_ticker"] == selected["outcome_ticker"]
            ),
            None,
        )
        if next_item is None:
            continue
        next_route = next_item["route"]
        entry = max(ask, next_route["ask"])
        if entry > 0.99:
            continue
        won = market_by_ticker[selected["outcome_ticker"]]["result"] == "yes"
        pnl = (1.0 - entry if won else -entry) - fee(entry)
        return {
            "strategy": f"cs2_best_route_{lane}",
            "event_ticker": event,
            "outcome_ticker": selected["outcome_ticker"],
            "ticker": next_route["ticker"],
            "side": next_route["side"],
            "ts": signal_ts + 60,
            "cluster": utc_day(signal_ts),
            "entry": entry,
            "signal_ask": ask,
            "spread": spread,
            "win": won,
            "pnl": pnl,
        }
    return None


def split_events(close_by_event: dict[str, int]) -> tuple[set[str], set[str]]:
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


def study_lane(
    lane: str,
    events: dict[str, list[dict]],
    candles: dict[str, dict[int, dict]],
    train_events: set[str],
    holdout_events: set[str],
    output_dir: Path,
) -> dict:
    bands = (
        ((0.80, 0.8999), (0.90, 0.9499), (0.95, 0.99))
        if lane == "favorite"
        else ((0.02, 0.0499), (0.05, 0.0999), (0.10, 0.1999), (0.20, 0.30))
    )
    configurations = [
        {
            "band_low": low,
            "band_high": high,
            "persistence_minutes": persistence,
            "maximum_spread": spread,
            "minimum_baseline_move": baseline_move,
            "minimum_recent_move": recent_move,
        }
        for (low, high), persistence, spread, baseline_move, recent_move in itertools.product(
            bands,
            (2, 4),
            (0.02, 0.04),
            ((0.03, 0.08) if lane == "favorite" else (0.0,)),
            ((0.0,) if lane == "favorite" else (0.0, 0.02)),
        )
    ]
    rows = []
    for config in configurations:
        trades = [
            trade
            for event, event_markets in events.items()
            if event in train_events
            for trade in [route_trade(event, event_markets, candles, config, lane)]
            if trade
        ]
        rows.append({"config": config, "metrics": summarize(trades)})
    benjamini_hochberg(rows, "exact_break_even_p")
    eligible = [
        row
        for row in rows
        if row["metrics"]["n"] >= 75
        and row["metrics"]["mean_pnl_cents"] > 0
    ]
    if not eligible:
        return {
            "lane": lane,
            "selected": None,
            "top_train": sorted(
                rows,
                key=lambda row: row["metrics"]["exact_break_even_p_bh"],
            )[:5],
        }
    selected = min(
        eligible,
        key=lambda row: (
            row["metrics"]["exact_break_even_p_bh"],
            -row["metrics"]["mean_pnl_cents"],
        ),
    )
    holdout = [
        trade
        for event, event_markets in events.items()
        if event in holdout_events
        for trade in [
            route_trade(event, event_markets, candles, selected["config"], lane)
        ]
        if trade
    ]
    holdout_metrics = summarize(holdout)
    checks = {
        "train_bh_p_le_0_05": selected["metrics"]["exact_break_even_p_bh"] <= 0.05,
        "holdout_n_ge_50": holdout_metrics["n"] >= 50,
        "holdout_mean_positive": holdout_metrics["mean_pnl_cents"] > 0,
        "holdout_exact_p_le_0_05": holdout_metrics["exact_break_even_p"] <= 0.05,
        "holdout_bootstrap_low_positive": (
            holdout_metrics["bootstrap_mean_low_cents"] > 0
        ),
    }
    trade_path = output_dir / f"trades_cs2_best_route_{lane}_holdout.jsonl"
    with trade_path.open("w") as handle:
        for trade in holdout:
            handle.write(json.dumps(trade, sort_keys=True) + "\n")
    return {
        "lane": lane,
        "tested_configurations": len(rows),
        "selected": {
            "config": selected["config"],
            "train_metrics": selected["metrics"],
            "holdout_metrics": holdout_metrics,
        },
        "validation": {
            "passed": all(checks.values()),
            "checks": checks,
            "failure_reasons": [key for key, value in checks.items() if not value],
        },
        "top_train": sorted(
            rows,
            key=lambda row: row["metrics"]["exact_break_even_p_bh"],
        )[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    markets = load_jsonl(args.data_dir / "markets_KXCS2MAP.jsonl")
    candles = load_candles(args.data_dir / "candles_KXCS2MAP_1m.jsonl")
    events: dict[str, list[dict]] = defaultdict(list)
    close_by_event: dict[str, int] = {}
    for market in markets:
        event = market.get("event_ticker")
        if not event:
            continue
        events[event].append(market)
        close_by_event[event] = max(
            close_by_event.get(event, 0),
            timestamp(market["close_time"]),
        )
    train_events, holdout_events = split_events(close_by_event)
    results = [
        study_lane(
            lane,
            events,
            candles,
            train_events,
            holdout_events,
            args.output_dir,
        )
        for lane in ("favorite", "underdog")
    ]
    output = args.output_dir / "cs2_route_studies.json"
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            [
                {
                    "lane": result["lane"],
                    "selected": result.get("selected"),
                    "validation": result.get("validation"),
                }
                for result in results
            ],
            indent=2,
            sort_keys=True,
        )
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
