#!/usr/bin/env python3
"""Independent replay of earlier entry bands for Kalshi temperature ladders.

This deliberately reproduces the deployed lane's signal definition: select the
exact-temperature YES contract with the highest YES midpoint, buy YES once per
city-day, and hold to settlement.  Earlier binary-favorite studies are not the
same strategy because they may buy NO on any strike.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
HIGH_ROOT = ROOT.parent / "kalshi_timely_2026_07"
LOW_ROOT = ROOT.parent / "kalshi_three_lane_research_2026_08"
OUT = ROOT / "results"

HIGH_SERIES = (
    "KXHIGHAUS", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHNY",
    "KXHIGHTATL", "KXHIGHTDAL", "KXHIGHTPHX", "KXHIGHTSEA", "KXHIGHTSFO",
)
LOW_SERIES = (
    "KXLOWTNYC", "KXLOWTPHIL", "KXLOWTAUS", "KXLOWTDEN",
    "KXLOWTBOS", "KXLOWTMIA", "KXLOWTCHI", "KXLOWTLAX",
)

# Midpoint bands are disjoint. Caps allow half the 2c spread above the band.
BANDS = {
    "80-85": (0.80, 0.85, 0.86),
    "85-90": (0.85, 0.90, 0.91),
    "80-90": (0.80, 0.90, 0.91),
    "90-95": (0.90, 0.95, 0.96),
    "95-99": (0.95, 0.99, 0.99),
}
MAX_SPREAD = 0.02
FILL_STRESS = 0.01


@dataclass(frozen=True)
class Trade:
    family: str
    series: str
    event: str
    date: str
    ticker: str
    band: str
    hold: int
    entry_ts: int
    hours_remaining: float
    bid: float
    ask: float
    mid: float
    won: bool
    pnl: float
    pnl_stressed: float


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def trade_pnl(won: bool, ask: float, stress: float = 0.0) -> float:
    paid = min(0.99, ask + stress)
    return (1.0 if won else 0.0) - paid - fee(paid)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def candle_quote(candle: dict[str, Any]) -> tuple[float, float] | None:
    if "b" in candle:
        bid = number(candle.get("b"))
        ask = number(candle.get("a"))
    else:
        bid = number((candle.get("yes_bid") or {}).get("close_dollars"))
        ask = number((candle.get("yes_ask") or {}).get("close_dollars"))
    if bid is None or ask is None or not (0 <= bid <= ask <= 1):
        return None
    return bid, ask


def date_from_event(event: str) -> str:
    token = event.split("-")[1]
    return dt.datetime.strptime(token, "%y%b%d").date().isoformat()


def select_first(
    *,
    family: str,
    series: str,
    event: str,
    date: str,
    close_ts: int,
    states: list[tuple[int, str, float, float, float, bool]],
    band: str,
    hold: int,
    max_gap_seconds: int,
) -> Trade | None:
    low, high, ask_cap = BANDS[band]
    recent: deque[tuple[int, str, float, float, float, bool]] = deque(maxlen=hold)
    for state in states:
        recent.append(state)
        if len(recent) != hold:
            continue
        if any(right[0] - left[0] > max_gap_seconds for left, right in zip(recent, list(recent)[1:])):
            continue
        ticker = state[1]
        if any(row[1] != ticker or not (low <= row[4] < high) for row in recent):
            continue
        ts, _, bid, ask, mid, won = state
        if ask - bid > MAX_SPREAD + 1e-9 or ask > ask_cap + 1e-9:
            continue
        return Trade(
            family=family,
            series=series,
            event=event,
            date=date,
            ticker=ticker,
            band=band,
            hold=hold,
            entry_ts=ts,
            hours_remaining=(close_ts - ts) / 3600.0,
            bid=bid,
            ask=ask,
            mid=mid,
            won=won,
            pnl=trade_pnl(won, ask),
            pnl_stressed=trade_pnl(won, ask, FILL_STRESS),
        )
    return None


def load_high_trades() -> tuple[list[Trade], dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    candles: dict[str, list[dict[str, Any]]] = {}
    for series in HIGH_SERIES:
        for prefix in ("", "extension_"):
            for row in read_jsonl(HIGH_ROOT / "data" / f"{prefix}markets_{series}.jsonl"):
                metadata[row["ticker"]] = row
            for row in read_jsonl(HIGH_ROOT / "data" / f"{prefix}candles_{series}.jsonl"):
                candles[row["ticker"]] = sorted(row.get("candles") or [], key=lambda x: int(x["t"]))

    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metadata.values():
        if row.get("result") in ("yes", "no") and row["ticker"] in candles:
            events[row["event_ticker"]].append(row)

    output: list[Trade] = []
    usable = 0
    for event, markets in sorted(events.items()):
        close_ts = min(parse_ts(row["close_time"]) for row in markets)
        states: list[tuple[int, str, float, float, float, bool]] = []
        for hours in range(12, 0, -1):
            target = close_ts - hours * 3600
            leaders: list[tuple[float, float, str, float, float, bool, int]] = []
            for market in markets:
                prior = [row for row in candles[market["ticker"]] if int(row["t"]) <= target]
                if not prior:
                    continue
                candle = prior[-1]
                candle_ts = int(candle["t"])
                if target - candle_ts > 5400:
                    continue
                quote = candle_quote(candle)
                if quote is None:
                    continue
                bid, ask = quote
                mid = (bid + ask) / 2.0
                leaders.append((mid, -(ask - bid), market["ticker"], bid, ask, market["result"] == "yes", candle_ts))
            if leaders:
                mid, _, ticker, bid, ask, won, candle_ts = max(leaders)
                states.append((candle_ts, ticker, bid, ask, mid, won))
        # De-duplicate repeated hourly archive timestamps.
        states = list(dict.fromkeys(states))
        if not states:
            continue
        usable += 1
        series = markets[0]["ticker"].split("-")[0]
        for band in ("80-90", "90-95", "95-99"):
            for hold in (1, 2, 3):
                trade = select_first(
                    family="high", series=series, event=event,
                    date=date_from_event(event), close_ts=close_ts,
                    states=states, band=band, hold=hold,
                    max_gap_seconds=5400,
                )
                if trade:
                    output.append(trade)
    return output, {
        "contracts": len(metadata),
        "events": len(events),
        "usable_events": usable,
        "resolution": "hourly",
    }


def load_low_metadata() -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for series in LOW_SERIES:
        path = LOW_ROOT / "data" / f"low_markets_{series}.json"
        rows = json.loads(path.read_text())
        output[series] = {
            row["ticker"]: row
            for row in rows
            if row.get("result") in ("yes", "no")
        }
    return output


def low_states(
    candle_map: dict[str, list[dict[str, Any]]],
    metadata: dict[str, dict[str, Any]],
    close_ts: int,
) -> list[tuple[int, str, float, float, float, bool]]:
    start = close_ts - 12 * 3600
    end = close_ts - 3600
    updates: dict[int, list[tuple[str, float, float]]] = defaultdict(list)
    for ticker, rows in candle_map.items():
        if ticker not in metadata:
            continue
        for candle in rows:
            ts = int(candle["end_period_ts"])
            if ts > end:
                break
            quote = candle_quote(candle)
            if quote is not None:
                updates[ts].append((ticker, quote[0], quote[1]))

    current: dict[str, tuple[float, float]] = {}
    # Establish the last known quote at the start of the live window.
    for ts in sorted(key for key in updates if key <= start):
        for ticker, bid, ask in updates[ts]:
            current[ticker] = (bid, ask)

    states: list[tuple[int, str, float, float, float, bool]] = []
    for ts in range(start, end + 1, 60):
        for ticker, bid, ask in updates.get(ts, ()):
            current[ticker] = (bid, ask)
        leaders: list[tuple[float, float, str, float, float, bool]] = []
        for ticker, (bid, ask) in current.items():
            mid = (bid + ask) / 2.0
            leaders.append((mid, -(ask - bid), ticker, bid, ask, metadata[ticker]["result"] == "yes"))
        if leaders:
            mid, _, ticker, bid, ask, won = max(leaders)
            states.append((ts, ticker, bid, ask, mid, won))
    return states


def load_low_trades() -> tuple[list[Trade], dict[str, Any]]:
    all_metadata = load_low_metadata()
    output: list[Trade] = []
    events_seen = 0
    usable = 0
    for series in LOW_SERIES:
        directory = LOW_ROOT / "data" / "low_event_candles_windowed" / series
        metadata = all_metadata[series]
        for path in sorted(directory.glob("*.json")):
            event = path.stem
            event_markets = {ticker: row for ticker, row in metadata.items() if row["event_ticker"] == event}
            if not event_markets:
                continue
            events_seen += 1
            close_ts = min(parse_ts(row["close_time"]) for row in event_markets.values())
            states = low_states(json.loads(path.read_text()), event_markets, close_ts)
            if not states:
                continue
            usable += 1
            for band in BANDS:
                for hold in (1, 5, 15, 20, 30, 45, 60, 90):
                    trade = select_first(
                        family="low", series=series, event=event,
                        date=date_from_event(event), close_ts=close_ts,
                        states=states, band=band, hold=hold,
                        max_gap_seconds=90,
                    )
                    if trade:
                        output.append(trade)
    return output, {
        "events": events_seen,
        "usable_events": usable,
        "resolution": "one-minute forward-filled BBO candles",
    }


def split_dates(trades: Iterable[Trade]) -> dict[str, str]:
    dates = sorted({trade.date for trade in trades})
    train_end = max(1, len(dates) // 2)
    validation_end = max(train_end + 1, (len(dates) * 3) // 4)
    return {
        date: "train" if index < train_end else "validation" if index < validation_end else "test"
        for index, date in enumerate(dates)
    }


def block_bootstrap(rows: list[Trade], iterations: int = 5000) -> dict[str, float | int]:
    by_date: dict[str, list[Trade]] = defaultdict(list)
    for row in rows:
        by_date[row.date].append(row)
    dates = sorted(by_date)
    if not dates:
        return {"days": 0}
    rng = random.Random(20260811)
    means: list[float] = []
    for _ in range(iterations):
        sample = [rng.choice(dates) for _ in dates]
        values = [trade.pnl_stressed for date in sample for trade in by_date[date]]
        means.append(statistics.fmean(values))
    means.sort()
    return {
        "days": len(dates),
        "probability_positive": sum(value > 0 for value in means) / iterations,
        "p05_cents": means[int(0.05 * (iterations - 1))] * 100,
        "median_cents": means[iterations // 2] * 100,
    }


def summarize(rows: list[Trade]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    by_city: dict[str, list[Trade]] = defaultdict(list)
    for row in rows:
        by_city[row.series].append(row)
    return {
        "n": len(rows),
        "wins": sum(row.won for row in rows),
        "losses": sum(not row.won for row in rows),
        "win_rate": sum(row.won for row in rows) / len(rows),
        "avg_ask": statistics.fmean(row.ask for row in rows),
        "avg_hours_remaining": statistics.fmean(row.hours_remaining for row in rows),
        "net_cents": statistics.fmean(row.pnl for row in rows) * 100,
        "stressed_net_cents": statistics.fmean(row.pnl_stressed for row in rows) * 100,
        "bootstrap": block_bootstrap(rows),
        "city_net_cents": {
            city: statistics.fmean(row.pnl_stressed for row in values) * 100
            for city, values in sorted(by_city.items())
        },
    }


def make_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Temperature Earlier-Entry Replay",
        "",
        "Signal: buy YES on the exact-temperature bracket with the highest YES midpoint. "
        "One entry per city-day per rule; final 1-12 hours; spread <=2c; hold to settlement.",
        "Execution: archived ask plus a separately reported 1c adverse-fill stress. "
        "Dates are split chronologically 50% train / 25% validation / 25% untouched test.",
        "",
    ]
    for family in ("high", "low"):
        lines.extend([
            f"## {family.title()} temperature",
            "",
            f"Inventory: `{json.dumps(payload['inventory'][family], sort_keys=True)}`",
            "",
            "| Band | Hold | Split | n | W-L | Avg ask | Avg hours left | Raw net | 1c-stress net | Day boot p05 | P(edge>0) |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        cells = [cell for cell in payload["cells"] if cell["family"] == family]
        for cell in cells:
            summary = cell["summary"]
            if not summary.get("n"):
                continue
            boot = summary["bootstrap"]
            lines.append(
                f"| {cell['band']}c | {cell['hold']} | {cell['split']} | {summary['n']} | "
                f"{summary['wins']}-{summary['losses']} | {summary['avg_ask']*100:.2f}c | "
                f"{summary['avg_hours_remaining']:.1f}h | {summary['net_cents']:+.2f}c | "
                f"{summary['stressed_net_cents']:+.2f}c | {boot['p05_cents']:+.2f}c | "
                f"{boot['probability_positive']*100:.1f}% |"
            )
        lines.append("")
    lines.extend([
        "## Interpretation guardrails",
        "",
        "- High-temperature candles are hourly, so their entry timing and fill estimates are coarse.",
        "- Low-temperature candles are one-minute BBO closes, forward-filled when the quote did not change; depth is unavailable.",
        "- A lower band is not promotable unless validation and test are positive after 1c stress and the day-bootstrap 5th percentile is positive.",
        "- Results do not transfer from binary-favorite strategies that can buy NO; those are a separate lane.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("high", "low", "both"), default="both")
    args = parser.parse_args()

    inventory: dict[str, Any] = {"high": {}, "low": {}}
    trades: list[Trade] = []
    if args.family in ("high", "both"):
        rows, inventory["high"] = load_high_trades()
        trades.extend(rows)
    if args.family in ("low", "both"):
        rows, inventory["low"] = load_low_trades()
        trades.extend(rows)

    cells: list[dict[str, Any]] = []
    for family in sorted({trade.family for trade in trades}):
        family_rows = [trade for trade in trades if trade.family == family]
        date_splits = split_dates(family_rows)
        bands = ("80-90", "90-95", "95-99") if family == "high" else tuple(BANDS)
        for band in bands:
            holds = (1, 2, 3) if family == "high" else (1, 5, 15, 20, 30, 45, 60, 90)
            for hold in holds:
                rule_rows = [trade for trade in family_rows if trade.band == band and trade.hold == hold]
                for split in ("train", "validation", "test", "all"):
                    subset = rule_rows if split == "all" else [
                        trade for trade in rule_rows if date_splits[trade.date] == split
                    ]
                    cells.append({
                        "family": family,
                        "band": band,
                        "hold": hold,
                        "split": split,
                        "summary": summarize(subset),
                    })

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inventory": inventory,
        "assumptions": {
            "bands": BANDS,
            "max_spread": MAX_SPREAD,
            "fill_stress": FILL_STRESS,
            "fee": "0.07*p*(1-p)",
        },
        "cells": cells,
        "trades": [asdict(trade) for trade in trades],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "early_entry_results.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (ROOT / "REPORT.md").write_text(make_report(payload))
    print(f"trades={len(trades):,} cells={len(cells):,}")
    print(ROOT / "REPORT.md")


if __name__ == "__main__":
    main()
