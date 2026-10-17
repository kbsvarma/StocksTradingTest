#!/usr/bin/env python3
"""Independent market-structure study of daily high-temperature events."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from common import bh_adjust, cluster_bootstrap, contract_pnl, fee, parse_ts, summarize
from fixed_expiry_study import first_after, nearest_before, quote, quote_for_side


SERIES = (
    "KXHIGHAUS",
    "KXHIGHCHI",
    "KXHIGHLAX",
    "KXHIGHMIA",
    "KXHIGHNY",
    "KXHIGHTATL",
    "KXHIGHTDAL",
    "KXHIGHTPHX",
    "KXHIGHTSEA",
    "KXHIGHTSFO",
)
BANDS = ((0.50, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.99))


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def weather_cluster(event: str) -> str:
    return event.split("-", 1)[-1]


def load_events(data_dir: Path) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = defaultdict(list)
    for series in SERIES:
        market_rows = load_jsonl(data_dir / f"markets_{series}.jsonl")
        market_rows += load_jsonl(data_dir / f"extension_markets_{series}.jsonl")
        candle_rows = load_jsonl(data_dir / f"candles_{series}.jsonl")
        candle_rows += load_jsonl(data_dir / f"extension_candles_{series}.jsonl")
        candles = {
            row["ticker"]: sorted(row.get("candles", []), key=lambda item: int(item["t"]))
            for row in candle_rows
        }
        markets = {row["ticker"]: row for row in market_rows}
        for ticker, market in markets.items():
            if market.get("result") not in ("yes", "no") or ticker not in candles:
                continue
            events[market["event_ticker"]].append(
                {"market": market, "candles": candles[ticker], "series": series}
            )
    return events


def side_history(item: dict, signal_ts: int, side: str, hold: int) -> list[float] | None:
    history = []
    for offset in range(hold - 1, -1, -1):
        candle = nearest_before(item["candles"], signal_ts - offset * 3600, 1200)
        side_quote = quote_for_side(candle, side) if candle else None
        favorite = quote(candle) if candle else None
        if side_quote is None or favorite is None or favorite[0] != side:
            return None
        history.append(side_quote[2])
    return history


def executable_trade(item: dict, signal: dict, side: str, signal_ask: float) -> dict | None:
    execution = first_after(item["candles"], int(signal["t"]), 5400)
    execution_quote = quote_for_side(execution, side, "open") if execution else None
    if execution_quote is None:
        return None
    entry = max(signal_ask, execution_quote[1])
    if entry > 0.99:
        return None
    won = item["market"]["result"] == side
    return {
        "ticker": item["market"]["ticker"],
        "timestamp": int(execution["t"]),
        "side": side,
        "entry": entry,
        "won": won,
        "pnl": contract_pnl(entry, won),
        "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
    }


def event_trade(
    event: str,
    items: list[dict],
    *,
    hours: int,
    band: tuple[float, float],
    spread: float,
    hold: int,
    mechanism: str,
    margin: float = 0.0,
) -> dict | None:
    close_ts = parse_ts(items[0]["market"]["close_time"])
    target = close_ts - hours * 3600
    snapshots = []
    for item in items:
        signal = nearest_before(item["candles"], target, 5400)
        current = quote(signal) if signal else None
        yes = quote_for_side(signal, "yes") if signal else None
        if current is None or yes is None:
            continue
        snapshots.append((item, signal, current, yes))
    if not snapshots:
        return None

    if mechanism == "modal_yes":
        ranked = sorted(snapshots, key=lambda row: row[3][2], reverse=True)
        item, signal, _, yes = ranked[0]
        bid, ask, midpoint = yes
        if not band[0] <= midpoint < band[1] or ask - bid > spread:
            return None
        if hold > 1:
            for offset in range(hold - 1, 0, -1):
                prior_ranked = []
                for prior_item, _, _, _ in snapshots:
                    prior = nearest_before(prior_item["candles"], int(signal["t"]) - offset * 3600, 1200)
                    prior_yes = quote_for_side(prior, "yes") if prior else None
                    if prior_yes:
                        prior_ranked.append((prior_yes[2], prior_item["market"]["ticker"]))
                if not prior_ranked or max(prior_ranked)[1] != item["market"]["ticker"]:
                    return None
        trade = executable_trade(item, signal, "yes", ask)
    elif mechanism == "concentrated_modal":
        if len(snapshots) != len(items):
            return None
        ranked = sorted(snapshots, key=lambda row: row[3][2], reverse=True)
        top = ranked[0]
        total_mid = sum(row[3][2] for row in ranked)
        if total_mid <= 0:
            return None
        normalized = [row[3][2] / total_mid for row in ranked]
        normalized_margin = normalized[0] - normalized[1]
        item, signal, _, yes = top
        bid, ask, midpoint = yes
        if normalized_margin < margin or ask - bid > spread or ask > 0.99:
            return None
        trade = executable_trade(item, signal, "yes", ask)
        if trade:
            trade["normalized_margin"] = normalized_margin
            trade["market_probability_sum"] = total_mid
    else:
        candidates = []
        for item, signal, current, _ in snapshots:
            side, bid, ask, midpoint = current
            if not band[0] <= midpoint < band[1] or ask - bid > spread:
                continue
            history = side_history(item, int(signal["t"]), side, hold)
            if history is None:
                continue
            if mechanism == "strengthening" and midpoint - history[0] < 0.05:
                continue
            if mechanism == "drawdown" and max(history) - midpoint < 0.05:
                continue
            candidates.append((midpoint, -(ask - bid), item, signal, side, ask))
        if not candidates:
            return None
        _, _, item, signal, side, ask = max(candidates, key=lambda row: (row[0], row[1], row[2]["market"]["ticker"]))
        trade = executable_trade(item, signal, side, ask)

    if trade is None:
        return None
    trade["event"] = event
    trade["series"] = item["series"]
    trade["cluster"] = weather_cluster(event)
    return trade


def calibration_surface(events: dict[str, list[dict]]) -> list[dict]:
    results = []
    for hours in (18, 12, 10, 8, 6, 4, 3, 2):
        for band in BANDS:
            for spread in (0.02, 0.04):
                for hold in (1, 2, 3):
                    for mechanism in ("binary_stable", "strengthening", "drawdown", "modal_yes"):
                        trades = [
                            trade
                            for event, items in events.items()
                            if (
                                trade := event_trade(
                                    event,
                                    items,
                                    hours=hours,
                                    band=band,
                                    spread=spread,
                                    hold=hold,
                                    mechanism=mechanism,
                                )
                            )
                        ]
                        results.append(
                            {
                                "mechanism": mechanism,
                                "config": {"hours": hours, "band": band, "spread": spread, "hold": hold},
                                "metrics": summarize(trades),
                                "_trades": trades,
                            }
                        )
        for spread in (0.02, 0.04):
            for margin in (0.05, 0.10, 0.20, 0.30):
                trades = [
                    trade
                    for event, items in events.items()
                    if (
                        trade := event_trade(
                            event,
                            items,
                            hours=hours,
                            band=(0.0, 1.0),
                            spread=spread,
                            hold=1,
                            mechanism="concentrated_modal",
                            margin=margin,
                        )
                    )
                ]
                results.append(
                    {
                        "mechanism": "concentrated_modal",
                        "config": {"hours": hours, "spread": spread, "normalized_margin": margin},
                        "metrics": summarize(trades),
                        "_trades": trades,
                    }
                )
    eligible = [row for row in results if row["metrics"].get("n", 0) >= 30]
    q_values = bh_adjust(row["metrics"]["cluster_p_one_sided"] for row in eligible)
    for row, q_value in zip(eligible, q_values):
        metrics = row["metrics"]
        metrics["bh_q"] = q_value
        preliminary = bool(
            metrics["n"] >= 50
            and all(
                metrics.get(key) is not None and metrics[key] > 0.0
                for key in ("train_mean_cents", "validation_mean_cents", "test_mean_cents")
            )
            and metrics.get("stress_1c_mean_cents", -1.0) > 0.0
            and q_value <= 0.10
        )
        if preliminary:
            metrics.update(cluster_bootstrap(row["_trades"]))
        row["passes_historical_gate"] = bool(
            preliminary and metrics.get("p05_cents", -1.0) > 0.0
        )
    for row in results:
        row.pop("_trades", None)
    return results


def basket_scan(events: dict[str, list[dict]]) -> dict:
    records = []
    for event, items in events.items():
        if len(items) < 2:
            continue
        close_ts = parse_ts(items[0]["market"]["close_time"])
        for hours in (18, 12, 10, 8, 6, 4, 3, 2, 1):
            yes_entries = []
            no_entries = []
            valid = True
            timestamp = None
            for item in items:
                signal = nearest_before(item["candles"], close_ts - hours * 3600, 5400)
                execution = first_after(item["candles"], int(signal["t"]), 5400) if signal else None
                signal_yes = quote_for_side(signal, "yes") if signal else None
                signal_no = quote_for_side(signal, "no") if signal else None
                next_yes = quote_for_side(execution, "yes", "open") if execution else None
                next_no = quote_for_side(execution, "no", "open") if execution else None
                if not all((signal_yes, signal_no, next_yes, next_no)):
                    valid = False
                    break
                yes_entries.append(max(signal_yes[1], next_yes[1]))
                no_entries.append(max(signal_no[1], next_no[1]))
                timestamp = int(execution["t"])
            if not valid or timestamp is None:
                continue
            yes_profit = 1.0 - sum(yes_entries) - sum(fee(price) for price in yes_entries)
            no_profit = (len(items) - 1.0) - sum(no_entries) - sum(fee(price) for price in no_entries)
            yes_stress = 1.0 - sum(min(0.999, price + 0.01) for price in yes_entries) - sum(
                fee(min(0.999, price + 0.01)) for price in yes_entries
            )
            no_stress = (len(items) - 1.0) - sum(min(0.999, price + 0.01) for price in no_entries) - sum(
                fee(min(0.999, price + 0.01)) for price in no_entries
            )
            records.append(
                {
                    "event": event,
                    "cluster": weather_cluster(event),
                    "hours": hours,
                    "legs": len(items),
                    "yes_basket_profit_cents": 100.0 * yes_profit,
                    "no_basket_profit_cents": 100.0 * no_profit,
                    "yes_basket_stress_cents": 100.0 * yes_stress,
                    "no_basket_stress_cents": 100.0 * no_stress,
                }
            )
    return {
        "snapshots": len(records),
        "positive_yes_baskets": sum(row["yes_basket_profit_cents"] > 0 for row in records),
        "positive_no_baskets": sum(row["no_basket_profit_cents"] > 0 for row in records),
        "stress_positive_yes_baskets": sum(row["yes_basket_stress_cents"] > 0 for row in records),
        "stress_positive_no_baskets": sum(row["no_basket_stress_cents"] > 0 for row in records),
        "best_yes_basket_cents": max((row["yes_basket_profit_cents"] for row in records), default=None),
        "best_no_basket_cents": max((row["no_basket_profit_cents"] for row in records), default=None),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../kalshi_timely_2026_07/data"))
    parser.add_argument("--output", type=Path, default=Path("results/weather_structure.json"))
    args = parser.parse_args()
    events = load_events(args.data_dir)
    surface = calibration_surface(events)
    baskets = basket_scan(events)
    payload = {
        "independent_city_day_events": len(events),
        "tested_calibration_cells": len(surface),
        "historical_survivors": [row for row in surface if row.get("passes_historical_gate")],
        "surface": surface,
        "basket_scan": baskets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    printable = dict(payload)
    printable["surface"] = f"{len(surface)} cells written to output"
    printable["basket_scan"] = {key: value for key, value in baskets.items() if key != "records"}
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
