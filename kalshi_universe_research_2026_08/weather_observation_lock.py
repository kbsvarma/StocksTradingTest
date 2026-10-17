#!/usr/bin/env python3
"""Test deterministic weather locks from observations already printed."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
import io
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import contract_pnl, summarize
from fixed_expiry_study import first_after, quote_for_side
from weather_structure_study import SERIES, load_events, weather_cluster


STATIONS = {
    "KXHIGHAUS": ("AUS", -6),
    "KXHIGHCHI": ("MDW", -6),
    "KXHIGHLAX": ("LAX", -8),
    "KXHIGHMIA": ("MIA", -5),
    "KXHIGHNY": ("NYC", -5),
    "KXHIGHTATL": ("ATL", -5),
    "KXHIGHTDAL": ("DFW", -6),
    "KXHIGHTPHX": ("PHX", -7),
    "KXHIGHTSEA": ("SEA", -8),
    "KXHIGHTSFO": ("SFO", -8),
}


def event_date(event: str) -> str:
    suffix = event.split("-", 1)[1]
    return datetime.strptime(suffix, "%y%b%d").date().isoformat()


def fetch_station(station: str, start: datetime, end: datetime) -> str:
    params = {
        "station": station,
        "data": "tmpf",
        "year1": start.year,
        "month1": start.month,
        "day1": start.day,
        "year2": end.year,
        "month2": end.month,
        "day2": end.day,
        "tz": "Etc/UTC",
        "format": "onlycomma",
        "latlon": "no",
        "elev": "no",
        "missing": "empty",
        "trace": "empty",
        "direct": "no",
        "report_type": [1, 2],
    }
    query = urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(
        f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{query}",
        headers={"User-Agent": "kalshi-universe-research/1.0"},
    )
    for attempt in range(7):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode()
        except Exception:
            if attempt == 6:
                raise
            time.sleep(2.0 + attempt * 2.0)
    raise RuntimeError("unreachable")


def load_observations(
    cache_dir: Path,
    fetch: bool,
) -> dict[str, dict[str, tuple[list[int], list[float]]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = {}
    start = datetime(2026, 5, 19, tzinfo=timezone.utc)
    end = datetime(2026, 8, 6, tzinfo=timezone.utc)
    for series, (station, _) in STATIONS.items():
        path = cache_dir / f"asos_{station}.csv"
        if fetch or not path.exists():
            path.write_text(fetch_station(station, start, end))
            time.sleep(0.5)
        rows = []
        for row in csv.DictReader(io.StringIO(path.read_text())):
            if not row.get("valid") or not row.get("tmpf"):
                continue
            valid = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            rows.append((int(valid.timestamp()), float(row["tmpf"])))
        by_date: dict[str, list[tuple[int, float]]] = defaultdict(list)
        offset_hours = STATIONS[series][1]
        for observed_ts, temperature in sorted(rows):
            by_date[standard_date(observed_ts, offset_hours)].append(
                (observed_ts, temperature)
            )
        indexed = {}
        for date, dated_rows in by_date.items():
            timestamps = []
            running_highs = []
            high = float("-inf")
            for observed_ts, temperature in dated_rows:
                high = max(high, temperature)
                timestamps.append(observed_ts)
                running_highs.append(high)
            indexed[date] = (timestamps, running_highs)
        output[series] = indexed
    return output


def standard_date(timestamp: int, offset_hours: int) -> str:
    shifted = datetime.fromtimestamp(timestamp, timezone.utc) + timedelta(hours=offset_hours)
    return shifted.date().isoformat()


def running_high(
    observations: dict[str, tuple[list[int], list[float]]],
    timestamp: int,
    date: str,
) -> float | None:
    indexed = observations.get(date)
    if indexed is None:
        return None
    timestamps, running_highs = indexed
    position = bisect_right(timestamps, timestamp) - 1
    return running_highs[position] if position >= 0 else None


def locked_side(market: dict, high: float) -> str | None:
    strike_type = market.get("strike_type")
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    # Kalshi's `greater` boundary is strict: T94 settles YES only above 94,
    # not when the reported high equals 94.
    if strike_type == "greater" and floor is not None and high > float(floor):
        return "yes"
    if strike_type in ("less", "between") and cap is not None and high > float(cap):
        return "no"
    return None


def event_lock_trade(
    event: str,
    items: list[dict],
    observations: dict[str, tuple[list[int], list[float]]],
    spread_cap: float,
    entry_cap: float,
) -> dict | None:
    date = event_date(event)
    close_ts = int(datetime.fromisoformat(items[0]["market"]["close_time"].replace("Z", "+00:00")).timestamp())
    signal_times = sorted(
        {
            int(candle["t"])
            for item in items
            for candle in item["candles"]
            if int(candle["t"]) < close_ts
        }
    )
    for signal_ts in signal_times:
        high = running_high(observations, signal_ts, date)
        if high is None:
            continue
        candidates = []
        for item in items:
            side = locked_side(item["market"], high)
            if side is None:
                continue
            signal = next((row for row in item["candles"] if int(row["t"]) == signal_ts), None)
            signal_quote = quote_for_side(signal, side) if signal else None
            execution = first_after(item["candles"], signal_ts, 5400)
            execution_quote = quote_for_side(execution, side, "open") if execution else None
            if signal_quote is None or execution_quote is None:
                continue
            if signal_quote[1] - signal_quote[0] > spread_cap:
                continue
            entry = max(signal_quote[1], execution_quote[1])
            if entry > entry_cap:
                continue
            candidates.append((1.0 - entry, -entry, item, execution, side, entry))
        if not candidates:
            continue
        _, _, item, execution, side, entry = max(
            candidates,
            key=lambda row: (row[0], row[1], row[2]["market"]["ticker"]),
        )
        won = item["market"]["result"] == side
        return {
            "event": event,
            "series": item["series"],
            "ticker": item["market"]["ticker"],
            "cluster": weather_cluster(event),
            "timestamp": int(execution["t"]),
            "observed_high_f": high,
            "side": side,
            "entry": entry,
            "won": won,
            "pnl": contract_pnl(entry, won),
            "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
        }
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", type=Path, default=Path("../kalshi_timely_2026_07/data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/weather_observations"))
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results/weather_observation_lock.json"))
    args = parser.parse_args()
    events = load_events(args.market_data)
    observations = load_observations(args.cache_dir, args.fetch)
    rows = []
    for spread_cap in (0.01, 0.02, 0.04):
        for entry_cap in (0.97, 0.98, 0.99):
            trades = []
            for event, items in events.items():
                series = items[0]["series"]
                trade = event_lock_trade(
                    event,
                    items,
                    observations[series],
                    spread_cap,
                    entry_cap,
                )
                if trade:
                    trades.append(trade)
            rows.append(
                {
                    "config": {"spread_cap": spread_cap, "entry_cap": entry_cap},
                    "metrics": summarize(trades, with_bootstrap=True),
                    "source_mismatch_trades": [trade for trade in trades if not trade["won"]],
                    "trades": trades,
                }
            )
    payload = {"events": len(events), "cells": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps([{**row, "trades": f"{len(row['trades'])} records"} for row in rows], indent=2))


if __name__ == "__main__":
    main()
