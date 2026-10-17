#!/usr/bin/env python3
"""Causal replay of irreversible daily-low observation locks.

Unlike a market-price favorite, this lane acts only after an official station
observation makes one side mathematically irreversible. Quotes are taken from
the first archived minute after the observation, never from the signal minute.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "kalshi_three_lane_research_2026_08" / "data"
OUT = ROOT / "results"

CITIES = (
    ("KXLOWTNYC", "NYC"), ("KXLOWTPHIL", "PHL"),
    ("KXLOWTAUS", "AUS"), ("KXLOWTDEN", "DEN"),
    ("KXLOWTBOS", "BOS"), ("KXLOWTMIA", "MIA"),
    ("KXLOWTCHI", "MDW"), ("KXLOWTLAX", "LAX"),
)
MAX_SPREAD = 0.02
CAPS = (0.90, 0.95, 0.99)


@dataclass(frozen=True)
class LockTrade:
    series: str
    event: str
    date: str
    ticker: str
    side: str
    mode: str
    cap: float
    signal_ts: int
    entry_ts: int
    observed_low_f: float
    bid: float
    ask: float
    spread: float
    won: bool
    pnl: float
    pnl_stressed: float


def num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ts(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def event_date(event: str) -> str:
    return dt.datetime.strptime(event.split("-")[1], "%y%b%d").date().isoformat()


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def pnl(won: bool, ask: float, stress: float = 0.0) -> float:
    paid = min(0.99, ask + stress)
    return (1.0 if won else 0.0) - paid - fee(paid)


def locked_side(market: dict[str, Any], low: float, buffer_f: int) -> str | None:
    kind = market.get("strike_type")
    floor = num(market.get("floor_strike"))
    cap = num(market.get("cap_strike"))
    if kind == "less" and cap is not None and low <= cap - 1.0 - buffer_f:
        return "yes"
    if kind == "greater" and floor is not None and low <= floor - buffer_f:
        return "no"
    if kind == "between" and floor is not None and low <= floor - 1.0 - buffer_f:
        return "no"
    return None


def candle_quote(candle: dict[str, Any], side: str) -> tuple[float, float] | None:
    yes_bid = num((candle.get("yes_bid") or {}).get("close_dollars"))
    yes_ask = num((candle.get("yes_ask") or {}).get("close_dollars"))
    if yes_bid is None or yes_ask is None or not (0 <= yes_bid <= yes_ask <= 1):
        return None
    if side == "yes":
        return yes_bid, yes_ask
    return 1.0 - yes_ask, 1.0 - yes_bid


def quote_after(
    candles: list[dict[str, Any]], signal_ts: int, side: str,
) -> tuple[int, float, float] | None:
    for candle in candles:
        ts = int(candle["end_period_ts"])
        if ts < signal_ts + 60:
            continue
        if ts > signal_ts + 900:
            return None
        quote = candle_quote(candle, side)
        if quote is not None:
            return ts, quote[0], quote[1]
    return None


def observation_file(station: str) -> Path:
    candidates = list(SOURCE.glob(f"asos_{station}_*.json"))
    if not candidates:
        raise FileNotFoundError(station)
    return max(candidates, key=lambda path: path.stat().st_size)


def first_trade(
    *,
    series: str,
    event: str,
    markets: list[dict[str, Any]],
    candles: dict[str, list[dict[str, Any]]],
    observations: list[dict[str, Any]],
    mode: str,
    cap: float,
) -> LockTrade | None:
    buffer_f = 1 if mode == "buffer_1f" else 0
    low = float("inf")
    lock_started: dict[tuple[str, str], int] = {}
    for observation in sorted(observations, key=lambda row: int(row["timestamp"])):
        signal_ts = int(observation["timestamp"])
        low = min(low, float(observation["temperature_f"]))
        candidates: list[tuple[float, float, str, str, int, float, float, bool]] = []
        for market in markets:
            side = locked_side(market, low, buffer_f)
            if side is None:
                continue
            key = (market["ticker"], side)
            lock_started.setdefault(key, signal_ts)
            if mode == "confirm_5m" and signal_ts - lock_started[key] < 300:
                continue
            quote = quote_after(candles.get(market["ticker"], []), signal_ts, side)
            if quote is None:
                continue
            entry_ts, bid, ask = quote
            spread = ask - bid
            if ask > cap + 1e-9 or spread > MAX_SPREAD + 1e-9:
                continue
            candidates.append((ask, spread, market["ticker"], side, entry_ts, bid, low, market["result"] == side))
        if candidates:
            ask, spread, ticker, side, entry_ts, bid, observed_low, won = min(candidates)
            return LockTrade(
                series=series, event=event, date=event_date(event),
                ticker=ticker, side=side, mode=mode, cap=cap,
                signal_ts=signal_ts, entry_ts=entry_ts,
                observed_low_f=observed_low, bid=bid, ask=ask,
                spread=spread, won=won, pnl=pnl(won, ask),
                pnl_stressed=pnl(won, ask, 0.01),
            )
    return None


def load_trades() -> tuple[list[LockTrade], dict[str, Any]]:
    output: list[LockTrade] = []
    inventory: dict[str, Any] = {}
    for series, station in CITIES:
        metadata = json.loads((SOURCE / f"low_markets_{series}.json").read_text())
        by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for market in metadata:
            if market.get("result") in ("yes", "no"):
                by_event[market["event_ticker"]].append(market)
        observations = json.loads(observation_file(station).read_text())
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in observations:
            by_date[observation["standard_date"]].append(observation)
        files = sorted((SOURCE / "low_event_candles_windowed" / series).glob("*.json"))
        inventory[series] = {"events": len(files), "observations": len(observations)}
        for path in files:
            event = path.stem
            markets = by_event.get(event, [])
            if not markets:
                continue
            candle_map = json.loads(path.read_text())
            event_observations = by_date.get(event_date(event), [])
            for mode in ("immediate", "confirm_5m", "buffer_1f"):
                for cap in CAPS:
                    trade = first_trade(
                        series=series, event=event, markets=markets,
                        candles=candle_map, observations=event_observations,
                        mode=mode, cap=cap,
                    )
                    if trade:
                        output.append(trade)
    return output, inventory


def split_map(rows: list[LockTrade]) -> dict[str, str]:
    dates = sorted({row.date for row in rows})
    a, b = len(dates) // 2, len(dates) * 3 // 4
    return {
        date: "train" if i < a else "validation" if i < b else "test"
        for i, date in enumerate(dates)
    }


def bootstrap(rows: list[LockTrade], iterations: int = 5000) -> dict[str, Any]:
    by_date: dict[str, list[LockTrade]] = defaultdict(list)
    for row in rows:
        by_date[row.date].append(row)
    dates = sorted(by_date)
    if not dates:
        return {"days": 0}
    rng = random.Random(20260811)
    means: list[float] = []
    for _ in range(iterations):
        values = [trade.pnl_stressed for _ in dates for trade in by_date[rng.choice(dates)]]
        means.append(statistics.fmean(values))
    means.sort()
    return {
        "days": len(dates),
        "p05_cents": means[int(0.05 * (iterations - 1))] * 100,
        "probability_positive": sum(value > 0 for value in means) / iterations,
    }


def summary(rows: list[LockTrade]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    return {
        "n": len(rows),
        "wins": sum(row.won for row in rows),
        "losses": sum(not row.won for row in rows),
        "avg_ask": statistics.fmean(row.ask for row in rows),
        "net_cents": statistics.fmean(row.pnl for row in rows) * 100,
        "stressed_net_cents": statistics.fmean(row.pnl_stressed for row in rows) * 100,
        "bootstrap": bootstrap(rows),
    }


def main() -> None:
    trades, inventory = load_trades()
    splits = split_map(trades)
    cells: list[dict[str, Any]] = []
    for mode in ("immediate", "confirm_5m", "buffer_1f"):
        for cap in CAPS:
            rule = [row for row in trades if row.mode == mode and row.cap == cap]
            for split in ("train", "validation", "test", "all"):
                rows = rule if split == "all" else [row for row in rule if splits[row.date] == split]
                cells.append({"mode": mode, "cap": cap, "split": split, "summary": summary(rows)})

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inventory": inventory,
        "assumptions": {"max_spread": MAX_SPREAD, "caps": CAPS, "stress": 0.01},
        "cells": cells,
        "trades": [asdict(row) for row in trades],
    }
    (OUT / "low_lock_results.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Daily-Low Observation-Lock Replay", "",
        "A trade is eligible only after a station print makes the selected side irreversible. "
        "Entry uses the first archived quote at least one minute after that print, spread <=2c, "
        "plus a separate 1c adverse-fill stress.", "",
        "| Mode | Price cap | Split | n | W-L | Avg ask | Raw net | 1c-stress net | Day boot p05 | P(edge>0) |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in cells:
        row = cell["summary"]
        if not row.get("n"):
            continue
        boot = row["bootstrap"]
        lines.append(
            f"| {cell['mode']} | <={cell['cap']*100:.0f}c | {cell['split']} | {row['n']} | "
            f"{row['wins']}-{row['losses']} | {row['avg_ask']*100:.2f}c | {row['net_cents']:+.2f}c | "
            f"{row['stressed_net_cents']:+.2f}c | {boot['p05_cents']:+.2f}c | "
            f"{boot['probability_positive']*100:.1f}% |"
        )
    lines.extend([
        "", "Modes:", "",
        "- `immediate`: act on the first mathematically locking station print.",
        "- `confirm_5m`: require the lock to remain present for at least five minutes, protecting against a bad single print.",
        "- `buffer_1f`: require one additional degree beyond the exact mathematical boundary.",
        "", "The archive has BBO but not quoted depth. Even stressed results require a live order-book/depth check before promotion.",
    ])
    (ROOT / "LOW_LOCK_REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"trades={len(trades):,}")
    print(ROOT / "LOW_LOCK_REPORT.md")


if __name__ == "__main__":
    main()
