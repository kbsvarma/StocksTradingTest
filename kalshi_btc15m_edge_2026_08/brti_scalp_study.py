#!/usr/bin/env python3
"""Frozen BRTI-driven 60-90 second entry with fixed scalp exits."""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass

import settlement_average_study as settlement
import study


MIN_PROBABILITY = 0.80
MIN_EXPECTED_EV = 0.03
MAX_SPREAD = 0.02
MIN_DEPTH = 10.0
STRESS = 0.0025


@dataclass(frozen=True)
class Entry:
    ticker: str
    close_ts: int
    result: str
    side: str
    ts: int
    entry: float
    p_win: float
    expected_ev: float
    rows: list[dict]


@dataclass(frozen=True)
class Result:
    ticker: str
    close_ts: int
    variant: str
    pnl: float
    won: bool
    exit_reason: str
    holding_seconds: int


def raw_books() -> dict[str, list[dict]]:
    output = defaultdict(list)
    with open(study.BOOK_PATH) as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("ticker", "").startswith("KXBTC15M"):
                output[row["ticker"]].append(row)
    for rows in output.values():
        rows.sort(key=lambda row: int(row["ts"]))
    return output


def levels(row: dict, side: str) -> tuple[float | None, float | None, float, float]:
    yes = [(float(price), float(size)) for price, size in row.get("yes") or []]
    no = [(float(price), float(size)) for price, size in row.get("no") or []]
    best_yes = max(yes, default=(None, 0.0), key=lambda item: item[0] or -1)
    best_no = max(no, default=(None, 0.0), key=lambda item: item[0] or -1)
    if side == "yes":
        bid = best_yes[0]
        ask = None if best_no[0] is None else 1 - best_no[0]
        return bid, ask, best_yes[1], best_no[1]
    bid = best_no[0]
    ask = None if best_yes[0] is None else 1 - best_yes[0]
    return bid, ask, best_no[1], best_yes[1]


def future_average_probability(strike: float, current: float, sigma: float) -> float:
    variance_weight = sum(index * index for index in range(1, 61))
    denominator = max(
        sigma * settlement.SIGMA_INFLATION * math.sqrt(variance_weight), 1e-12
    )
    return settlement.normal_cdf((60 * current - 60 * strike) / denominator)


def find_entries(markets: list[study.Market], books: dict, tape: settlement.BrtiTape) -> list[Entry]:
    entries = []
    for market in markets:
        if market.strike is None or market.expiration is None:
            continue
        opening = tape.boundary_average(market.close_ts - 15 * 60)
        closing = tape.boundary_average(market.close_ts)
        if (
            opening is None
            or closing is None
            or abs(opening - market.strike) > settlement.RECONSTRUCTION_TOLERANCE
            or abs(closing - market.expiration) > settlement.RECONSTRUCTION_TOLERANCE
        ):
            continue
        rows = books.get(market.ticker) or []
        for index, row in enumerate(rows):
            timestamp = int(row["ts"])
            remainder = market.close_ts - timestamp
            if not 60 <= remainder <= 90:
                continue
            current = tape.by_second.get(timestamp - 1)
            sigma = tape.innovation_sigma(timestamp)
            if current is None or sigma is None:
                continue
            p_yes = future_average_probability(market.strike, current, sigma)
            side = "yes" if p_yes >= 0.5 else "no"
            p_win = max(p_yes, 1 - p_yes)
            bid, ask, _, ask_depth = levels(row, side)
            if bid is None or ask is None or not 0 < bid <= ask < 1:
                continue
            expected_ev = p_win - ask - study.fee(ask)
            if (
                p_win < MIN_PROBABILITY
                or expected_ev < MIN_EXPECTED_EV
                or ask - bid > MAX_SPREAD + study.EPSILON
                or ask_depth < MIN_DEPTH
            ):
                continue
            entries.append(
                Entry(
                    market.ticker,
                    market.close_ts,
                    market.result,
                    side,
                    timestamp,
                    ask,
                    p_win,
                    expected_ev,
                    rows[index + 1 :],
                )
            )
            break
    return sorted(entries, key=lambda entry: entry.close_ts)


def settle(entry: Entry, variant: str, take_profit: float | None, stop: float | None) -> Result:
    if take_profit is None:
        won = entry.result == entry.side
        pnl = study.settlement_pnl(entry.entry, won)
        return Result(entry.ticker, entry.close_ts, variant, pnl, won, "settlement", entry.close_ts - entry.ts)

    last_bid = None
    last_ts = entry.ts
    reason = "last_bid"
    for row in entry.rows:
        timestamp = int(row["ts"])
        if timestamp > entry.close_ts:
            break
        bid, _, _, _ = levels(row, entry.side)
        if bid is None:
            continue
        last_bid, last_ts = bid, timestamp
        change = bid - entry.entry
        if change >= take_profit - study.EPSILON:
            reason = "take_profit"
            break
        if stop is not None and change <= -stop + study.EPSILON:
            reason = "stop"
            break
    if last_bid is None:
        won = entry.result == entry.side
        pnl = study.settlement_pnl(entry.entry, won)
        return Result(entry.ticker, entry.close_ts, variant, pnl, won, "no_exit_book", entry.close_ts - entry.ts)
    pnl = last_bid - entry.entry - study.fee(entry.entry) - study.fee(last_bid)
    return Result(entry.ticker, entry.close_ts, variant, pnl, pnl > 0, reason, last_ts - entry.ts)


def day_bootstrap(results: list[Result], iterations: int = 20_000) -> tuple[float, float, int]:
    grouped = defaultdict(list)
    for result in results:
        grouped[result.close_ts // 86400].append(result.pnl - STRESS)
    days = sorted(grouped)
    if not days:
        return 0.0, float("nan"), 0
    generator = random.Random(2026080702)
    means = []
    for _ in range(iterations):
        values = [
            value
            for _ in days
            for value in grouped[generator.choice(days)]
        ]
        means.append(100 * statistics.mean(values))
    means.sort()
    return (
        sum(value > 0 for value in means) / len(means),
        means[int(0.05 * (len(means) - 1))],
        len(days),
    )


def summarize(label: str, results: list[Result]) -> None:
    if not results:
        print(f"{label:24} n=0")
        return
    probability, p05, days = day_bootstrap(results)
    reasons = defaultdict(int)
    for result in results:
        reasons[result.exit_reason] += 1
    print(
        f"{label:24} n={len(results):3d} positive={sum(r.pnl > 0 for r in results)/len(results):6.1%} "
        f"raw={100*statistics.mean(r.pnl for r in results):+7.3f}c "
        f"stress25={100*(statistics.mean(r.pnl for r in results)-STRESS):+7.3f}c "
        f"hold={statistics.mean(r.holding_seconds for r in results):5.1f}s "
        f"days={days} bootP={probability:5.1%} p05={p05:+7.3f}c exits={dict(reasons)}"
    )


def main() -> None:
    tape = settlement.BrtiTape.load()
    entries = find_entries(study.load_markets(), raw_books(), tape)
    print(f"entries={len(entries)}")
    if not entries:
        return
    split = entries[len(entries) // 2].close_ts
    variants = {
        "hold": (None, None),
        "tp5_sl8": (0.05, 0.08),
        "tp10_sl8": (0.10, 0.08),
    }
    for variant, (take_profit, stop) in variants.items():
        results = [settle(entry, variant, take_profit, stop) for entry in entries]
        print(f"\n=== {variant} ===")
        summarize("diagnostic first half", [r for r in results if r.close_ts < split])
        summarize("locked final half", [r for r in results if r.close_ts >= split])
        summarize("all", results)
    print(
        f"mean_entry={statistics.mean(entry.entry for entry in entries):.4f} "
        f"mean_model_p={statistics.mean(entry.p_win for entry in entries):.4f} "
        f"mean_model_ev={100*statistics.mean(entry.expected_ev for entry in entries):+.3f}c "
        f"YES/NO={sum(entry.side == 'yes' for entry in entries)}/"
        f"{sum(entry.side == 'no' for entry in entries)}"
    )


if __name__ == "__main__":
    main()
