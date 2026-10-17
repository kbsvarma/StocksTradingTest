#!/usr/bin/env python3
"""Independent, event-deduplicated CS2 map-market strategy search."""

from __future__ import annotations

import argparse
import json
import re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from common import (
    bh_adjust,
    chronological_splits,
    cluster_bootstrap,
    contract_pnl,
    summarize,
    utc_day,
)


EASTERN = ZoneInfo("America/New_York")
SCHEDULE_RE = re.compile(r"KXCS2MAP-(\d{2}[A-Z]{3}\d{2})(\d{4})")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def scheduled_start(event: str) -> int | None:
    match = SCHEDULE_RE.match(event)
    if match is None:
        return None
    local = datetime.strptime("".join(match.groups()), "%y%b%d%H%M").replace(
        tzinfo=EASTERN
    )
    return int(local.timestamp())


def yes_quote(candle: dict | None, phase: str) -> tuple[float, float] | None:
    if candle is None:
        return None
    suffix = "c" if phase == "close" else "o"
    bid = candle.get(f"b{suffix}")
    ask = candle.get(f"a{suffix}")
    if bid is None or ask is None:
        return None
    bid_value, ask_value = float(bid), float(ask)
    if not 0.0 <= bid_value <= ask_value <= 1.0:
        return None
    return bid_value, ask_value


def outcome_snapshot(
    event_markets: list[dict],
    candles: dict[str, dict[int, dict]],
    timestamp: int,
    phase: str,
) -> list[dict] | None:
    direct = [yes_quote(candles[market["ticker"]].get(timestamp), phase) for market in event_markets]
    if any(item is None for item in direct):
        return None
    assert direct[0] is not None and direct[1] is not None
    raw_probabilities = []
    for index in (0, 1):
        other = 1 - index
        direct_bid, direct_ask = direct[index]
        other_bid, other_ask = direct[other]
        direct_mid = (direct_bid + direct_ask) / 2.0
        synthetic_mid = 1.0 - (other_bid + other_ask) / 2.0
        raw_probabilities.append((direct_mid + synthetic_mid) / 2.0)
    denominator = sum(raw_probabilities)
    if denominator <= 0:
        return None

    output = []
    for index, market in enumerate(event_markets):
        other = 1 - index
        direct_bid, direct_ask = direct[index]
        other_bid, other_ask = direct[other]
        routes = [
            {
                "ticker": market["ticker"],
                "side": "yes",
                "bid": direct_bid,
                "ask": direct_ask,
            },
            {
                "ticker": event_markets[other]["ticker"],
                "side": "no",
                "bid": 1.0 - other_ask,
                "ask": 1.0 - other_bid,
            },
        ]
        route = min(routes, key=lambda item: (item["ask"], item["ask"] - item["bid"]))
        output.append(
            {
                "outcome_ticker": market["ticker"],
                "probability": raw_probabilities[index] / denominator,
                "route": route,
            }
        )
    return output


def build_features(
    markets_path: Path,
    candles_path: Path,
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    markets_by_event: dict[str, list[dict]] = defaultdict(list)
    for market in load_jsonl(markets_path):
        if market.get("result") in ("yes", "no"):
            markets_by_event[market["event_ticker"]].append(market)
    candles = {
        record["ticker"]: {int(candle["t"]): candle for candle in record.get("candles", [])}
        for record in load_jsonl(candles_path)
    }
    features: dict[str, list[dict]] = {}
    diagnostics = defaultdict(int)
    for event, event_markets in markets_by_event.items():
        if len(event_markets) != 2 or sum(market["result"] == "yes" for market in event_markets) != 1:
            diagnostics["invalid_event_shape"] += 1
            continue
        if any(market["ticker"] not in candles for market in event_markets):
            diagnostics["missing_candles"] += 1
            continue
        start_ts = scheduled_start(event)
        if start_ts is None:
            diagnostics["unparsed_schedule"] += 1
            continue
        common_times = sorted(
            set(candles[event_markets[0]["ticker"]])
            & set(candles[event_markets[1]["ticker"]])
        )
        close_snapshots = {
            timestamp: outcome_snapshot(event_markets, candles, timestamp, "close")
            for timestamp in common_times
        }
        close_snapshots = {
            timestamp: snapshot
            for timestamp, snapshot in close_snapshots.items()
            if snapshot is not None
        }
        rows = []
        previous_favorite = None
        previous_ts = None
        held = 0
        history: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for timestamp in sorted(close_snapshots):
            if timestamp < start_ts or timestamp > start_ts + 5 * 3600:
                continue
            snapshot = close_snapshots[timestamp]
            assert snapshot is not None
            favorite = max(snapshot, key=lambda item: item["probability"])
            if previous_ts == timestamp - 60 and previous_favorite == favorite["outcome_ticker"]:
                held += 1
            else:
                held = 1
            previous_ts = timestamp
            previous_favorite = favorite["outcome_ticker"]
            for item in snapshot:
                history[item["outcome_ticker"]].append((timestamp, item["probability"]))

            times_position = bisect_left(common_times, timestamp - 9 * 60)
            recent_times = [value for value in common_times[times_position:] if value <= timestamp]
            activity_count = len(recent_times)
            recent_volume = 0.0
            for market in event_markets:
                for candle_ts in recent_times:
                    recent_volume += float(candles[market["ticker"]].get(candle_ts, {}).get("v") or 0.0)

            outcome_history = history[favorite["outcome_ticker"]]
            probability_by_ts = {item_ts: probability for item_ts, probability in outcome_history}
            five_minute_probability = probability_by_ts.get(timestamp - 5 * 60)
            prior_ten = [
                probability
                for item_ts, probability in outcome_history
                if timestamp - 10 * 60 <= item_ts < timestamp
            ]
            route = favorite["route"]
            next_snapshot = outcome_snapshot(event_markets, candles, timestamp + 60, "open")
            next_item = next(
                (
                    item
                    for item in (next_snapshot or [])
                    if item["outcome_ticker"] == favorite["outcome_ticker"]
                ),
                None,
            )
            if next_item is None:
                continue
            result_market = next(
                market for market in event_markets if market["ticker"] == favorite["outcome_ticker"]
            )
            rows.append(
                {
                    "event": event,
                    "timestamp": timestamp,
                    "cluster": utc_day(timestamp),
                    "outcome_ticker": favorite["outcome_ticker"],
                    "probability": favorite["probability"],
                    "bid": route["bid"],
                    "ask": route["ask"],
                    "spread": route["ask"] - route["bid"],
                    "held": held,
                    "activity_count": activity_count,
                    "recent_volume": recent_volume,
                    "move_5m": (
                        favorite["probability"] - five_minute_probability
                        if five_minute_probability is not None
                        else None
                    ),
                    "drawdown_10m": (
                        max(prior_ten) - favorite["probability"] if prior_ten else None
                    ),
                    "execution_ask": next_item["route"]["ask"],
                    "won": result_market["result"] == "yes",
                }
            )
        if rows:
            features[event] = rows
            diagnostics["usable_events"] += 1
    diagnostics["market_events"] = len(markets_by_event)
    return features, dict(diagnostics)


def candidate(event_rows: list[dict], config: dict) -> dict | None:
    for row in event_rows:
        if not config["band"][0] <= row["probability"] < config["band"][1]:
            continue
        if row["spread"] > config["spread"] + 1e-9:
            continue
        if row["held"] < config["hold"]:
            continue
        if row["activity_count"] < config["activity"]:
            continue
        if row["recent_volume"] < config["minimum_volume"]:
            continue
        if config["mechanism"] == "strengthening":
            if row["move_5m"] is None or row["move_5m"] < config["movement"]:
                continue
        if config["mechanism"] == "drawdown":
            if row["drawdown_10m"] is None or row["drawdown_10m"] < config["movement"]:
                continue
        entry = max(row["ask"], row["execution_ask"])
        if entry > 0.99:
            continue
        return {
            **row,
            "entry": entry,
            "pnl": contract_pnl(entry, row["won"]),
            "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), row["won"]),
        }
    return None


def configurations() -> list[dict]:
    output = []
    for mechanism in ("stable", "strengthening", "drawdown"):
        for band in ((0.80, 0.90), (0.90, 0.95), (0.95, 0.99), (0.97, 0.99)):
            for spread in (0.01, 0.02):
                for hold in (3, 5, 10):
                    for activity in (5, 8):
                        movements = (0.0,) if mechanism == "stable" else (0.03, 0.05, 0.10)
                        for movement in movements:
                            output.append(
                                {
                                    "mechanism": mechanism,
                                    "band": band,
                                    "spread": spread,
                                    "hold": hold,
                                    "activity": activity,
                                    "minimum_volume": 10.0,
                                    "movement": movement,
                                }
                            )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("../kalshi_expiry_research_2026_07/data"),
    )
    parser.add_argument("--output", type=Path, default=Path("results/cs2_independent.json"))
    args = parser.parse_args()
    features, diagnostics = build_features(
        args.data_dir / "markets_KXCS2MAP.jsonl",
        args.data_dir / "candles_KXCS2MAP_1m.jsonl",
    )
    cells = []
    for config in configurations():
        trades = [trade for rows in features.values() if (trade := candidate(rows, config))]
        metrics = summarize(trades)
        train, validation, test = chronological_splits(trades)
        cells.append(
            {
                "config": config,
                "metrics": metrics,
                "selection_train": summarize(train),
                "validation": summarize(validation),
                "test": summarize(test),
                "_oos": validation + test,
            }
        )
    eligible = [cell for cell in cells if cell["selection_train"].get("n", 0) >= 20]
    adjusted = bh_adjust(
        cell["selection_train"].get("cluster_p_one_sided", 1.0) for cell in eligible
    )
    for cell, q_value in zip(eligible, adjusted):
        cell["selection_train"]["bh_q"] = q_value
        oos = summarize(cell["_oos"])
        bootstrap = cluster_bootstrap(cell["_oos"])
        cell["out_of_sample"] = {**oos, "bootstrap": bootstrap}
        cell["passes_historical_gate"] = bool(
            cell["metrics"].get("n", 0) >= 50
            and cell["selection_train"].get("n", 0) >= 25
            and q_value <= 0.10
            and cell["validation"].get("mean_cents", -1.0) > 0.0
            and cell["test"].get("mean_cents", -1.0) > 0.0
            and oos.get("stress_1c_mean_cents", -1.0) > 0.0
            and bootstrap.get("p05_cents", -1.0) > 0.0
        )
    for cell in cells:
        cell.setdefault("passes_historical_gate", False)
        cell.pop("_oos", None)
    payload = {
        "diagnostics": diagnostics,
        "tested_cells": len(cells),
        "survivors": sum(cell["passes_historical_gate"] for cell in cells),
        "cells": cells,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    survivors = [cell for cell in cells if cell["passes_historical_gate"]]
    print(json.dumps(sorted(survivors, key=lambda row: -row["out_of_sample"]["mean_cents"]), indent=2))
    print(json.dumps(diagnostics, sort_keys=True))
    print(f"tested {len(cells)} cells; survivors {len(survivors)}")


if __name__ == "__main__":
    main()
