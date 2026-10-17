#!/usr/bin/env python3
"""Observed-high calibration surface for daily temperature contracts."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from common import bh_adjust, chronological_splits, cluster_bootstrap, contract_pnl, parse_ts, summarize
from fixed_expiry_study import first_after, nearest_before, quote_for_side
from weather_observation_lock import STATIONS, event_date, load_observations, running_high
from weather_structure_study import load_events, weather_cluster


PRICE_BANDS = ((0.05, 0.20), (0.20, 0.50), (0.50, 0.80), (0.80, 0.95), (0.95, 0.99))


def distance_bin(distance: float) -> str:
    if distance < 0:
        return "crossed"
    if distance <= 1:
        return "0_to_1"
    if distance <= 2:
        return "1_to_2"
    if distance <= 4:
        return "2_to_4"
    return "gt_4"


def path_state(market: dict, high: float) -> tuple[str, str] | None:
    strike_type = market.get("strike_type")
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    if strike_type == "greater" and floor is not None:
        gap = float(floor) - high
        return ("greater_crossed" if gap < 0 else "greater_pending", distance_bin(gap))
    if strike_type == "less" and cap is not None:
        gap = float(cap) - high
        return ("cap_crossed" if gap < 0 else "less_alive", distance_bin(gap))
    if strike_type == "between" and floor is not None and cap is not None:
        if high > float(cap):
            return "cap_crossed", "crossed"
        if high >= float(floor):
            return "between_inside", distance_bin(float(cap) - high)
        return "between_below", distance_bin(float(floor) - high)
    return None


def candidate_rows(
    events: dict[str, list[dict]],
    observations: dict[str, dict[str, tuple[list[int], list[float]]]],
) -> list[dict]:
    rows = []
    for event, items in events.items():
        series = items[0]["series"]
        date = event_date(event)
        close_ts = parse_ts(items[0]["market"]["close_time"])
        for hours in (18, 12, 10, 8, 6, 4):
            target = close_ts - hours * 3600
            high = running_high(observations[series], target, date)
            if high is None:
                continue
            for item in items:
                state = path_state(item["market"], high)
                signal = nearest_before(item["candles"], target, 5400)
                execution = first_after(item["candles"], int(signal["t"]), 5400) if signal else None
                if state is None or signal is None or execution is None:
                    continue
                for side in ("yes", "no"):
                    signal_quote = quote_for_side(signal, side)
                    execution_quote = quote_for_side(execution, side, "open")
                    if signal_quote is None or execution_quote is None:
                        continue
                    entry = max(signal_quote[1], execution_quote[1])
                    if entry > 0.99:
                        continue
                    midpoint = signal_quote[2]
                    price_band = next(
                        (band for band in PRICE_BANDS if band[0] <= midpoint < band[1]),
                        None,
                    )
                    if price_band is None:
                        continue
                    won = item["market"]["result"] == side
                    rows.append(
                        {
                            "event": event,
                            "ticker": item["market"]["ticker"],
                            "series": series,
                            "cluster": weather_cluster(event),
                            "timestamp": int(execution["t"]),
                            "hours": hours,
                            "state": state[0],
                            "distance_bin": state[1],
                            "side": side,
                            "price_band": price_band,
                            "spread": signal_quote[1] - signal_quote[0],
                            "observed_high_f": high,
                            "entry": entry,
                            "won": won,
                            "pnl": contract_pnl(entry, won),
                            "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
                        }
                    )
    return rows


def build_cells(rows: list[dict]) -> list[dict]:
    cells = []
    dimensions = sorted(
        {
            (row["hours"], row["state"], row["distance_bin"], row["side"], tuple(row["price_band"]))
            for row in rows
        }
    )
    for hours, state, distance, side, price_band in dimensions:
        for spread_cap in (0.02, 0.04):
            matching = [
                row
                for row in rows
                if row["hours"] == hours
                and row["state"] == state
                and row["distance_bin"] == distance
                and row["side"] == side
                and tuple(row["price_band"]) == price_band
                and row["spread"] <= spread_cap + 1e-9
            ]
            by_event: dict[str, list[dict]] = defaultdict(list)
            for row in matching:
                by_event[row["event"]].append(row)
            trades = [
                min(event_rows, key=lambda row: (row["spread"], row["entry"], row["ticker"]))
                for event_rows in by_event.values()
            ]
            train, validation, test = chronological_splits(trades)
            cells.append(
                {
                    "config": {
                        "hours": hours,
                        "state": state,
                        "distance_bin": distance,
                        "side": side,
                        "price_band": price_band,
                        "spread_cap": spread_cap,
                    },
                    "metrics": summarize(trades),
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
    return cells


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", type=Path, default=Path("../kalshi_timely_2026_07/data"))
    parser.add_argument("--observation-cache", type=Path, default=Path("data/weather_observations"))
    parser.add_argument("--output", type=Path, default=Path("results/weather_path_state.json"))
    args = parser.parse_args()
    events = load_events(args.market_data)
    observations = load_observations(args.observation_cache, fetch=False)
    rows = candidate_rows(events, observations)
    cells = build_cells(rows)
    payload = {
        "events": len(events),
        "candidate_quotes": len(rows),
        "tested_cells": len(cells),
        "survivors": sum(cell["passes_historical_gate"] for cell in cells),
        "cells": cells,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    survivors = [cell for cell in cells if cell["passes_historical_gate"]]
    print(json.dumps(survivors, indent=2))
    print(f"events {len(events)} candidates {len(rows)} cells {len(cells)} survivors {len(survivors)}")


if __name__ == "__main__":
    main()
