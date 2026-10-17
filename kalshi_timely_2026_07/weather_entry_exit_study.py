#!/usr/bin/env python3
"""Independent weather entry/exit study using archived Kalshi BBO candles.

The unit of observation is one city-day event, not one strike.  This prevents
the six mutually exclusive temperature contracts from masquerading as six
independent outcomes.  Candidate rules are ranked on the early sample and
reported separately on the final chronological holdout.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import math
import os
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
FRESH_TS = calendar.timegm(time.strptime("2026-07-28T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
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
HOURS = (18, 12, 10, 8, 6, 4, 3, 2, 1)
SPREAD_CAPS = (0.02, 0.04)
FAVORITE_BANDS = ((0.70, 0.80), (0.80, 0.90), (0.90, 0.95), (0.95, 0.99))
MODAL_BANDS = ((0.20, 0.35), (0.35, 0.50), (0.50, 0.65), (0.65, 0.80), (0.80, 0.95))
STOP_LEVELS = (0.05, 0.10, 0.15, 0.20)
TARGET_LEVELS = (0.03, 0.05, 0.10)


def parse_ts(value: str) -> int:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp())


def taker_fee_per_contract(price: float, quantity: int = 10) -> float:
    """General Kalshi fee, rounded once at order level, then allocated per lot."""
    raw_total = 0.07 * quantity * price * (1.0 - price)
    rounded_total = math.ceil((raw_total * 100.0) - 1e-12) / 100.0
    return rounded_total / quantity


@dataclass(frozen=True)
class Market:
    ticker: str
    series: str
    event: str
    close_ts: int
    result: str
    candles: tuple[dict, ...]


@dataclass(frozen=True)
class Entry:
    family: str
    series: str
    event: str
    ticker: str
    close_ts: int
    entry_ts: int
    hours: int
    band: str
    spread_cap: float
    side: str
    bid: float
    ask: float
    mid: float
    result: str
    path: tuple[tuple[int, float, float], ...]

    @property
    def won(self) -> bool:
        return self.result == self.side

    @property
    def date(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.close_ts - 12 * 3600))

    @property
    def oos(self) -> bool:
        return self.close_ts >= OOS_TS

    @property
    def validation(self) -> bool:
        return OOS_TS <= self.close_ts < FRESH_TS

    @property
    def fresh(self) -> bool:
        return self.close_ts >= FRESH_TS


def quote(candle: dict, side: str) -> tuple[float, float, float] | None:
    try:
        yes_bid = float(candle["b"])
        yes_ask = float(candle["a"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= yes_bid <= yes_ask <= 1.0):
        return None
    if side == "yes":
        bid, ask = yes_bid, yes_ask
    else:
        bid, ask = 1.0 - yes_ask, 1.0 - yes_bid
    return bid, ask, (bid + ask) / 2.0


def quote_at(market: Market, target_ts: int) -> tuple[int, dict] | None:
    eligible = [c for c in market.candles if int(c["t"]) <= target_ts]
    if not eligible:
        return None
    candle = max(eligible, key=lambda c: int(c["t"]))
    # Hourly archive: the most recent close should be less than 90 minutes old.
    if target_ts - int(candle["t"]) > 5400:
        return None
    return int(candle["t"]), candle


def band_label(value: float, bands: Iterable[tuple[float, float]]) -> str | None:
    for low, high in bands:
        if low <= value < high:
            return f"{int(low * 100)}-{int(high * 100)}"
    return None


def side_path(market: Market, side: str, after_ts: int) -> tuple[tuple[int, float, float], ...]:
    path = []
    for candle in market.candles:
        candle_ts = int(candle["t"])
        if candle_ts <= after_ts:
            continue
        quoted = quote(candle, side)
        if quoted is None:
            continue
        bid, ask, _ = quoted
        # Settlement placeholders often appear as a synthetic 0/100 spread.
        if ask - bid > 0.25:
            continue
        path.append((candle_ts, bid, ask))
    return tuple(sorted(path))


def stable_in_band(market: Market, side: str, entry_ts: int, band: str, periods: int) -> bool:
    recent = sorted(
        (c for c in market.candles if int(c["t"]) <= entry_ts),
        key=lambda c: int(c["t"]),
    )[-periods:]
    if len(recent) != periods or int(recent[-1]["t"]) != entry_ts:
        return False
    for previous, current in zip(recent, recent[1:]):
        if int(current["t"]) - int(previous["t"]) > 5400:
            return False
    for candle in recent:
        quoted = quote(candle, side)
        if quoted is None:
            return False
        _, _, side_mid = quoted
        if side_mid < 0.5 or band_label(side_mid, FAVORITE_BANDS) != band:
            return False
    return True


def prior_opposite_peak(market: Market, side: str, entry_ts: int, stable_periods: int) -> float:
    prior = sorted(
        (c for c in market.candles if int(c["t"]) <= entry_ts),
        key=lambda c: int(c["t"]),
    )
    if len(prior) <= stable_periods:
        return 0.0
    opposite = "no" if side == "yes" else "yes"
    peaks = []
    for candle in prior[:-stable_periods]:
        quoted = quote(candle, opposite)
        if quoted is not None:
            peaks.append(quoted[2])
    return max(peaks, default=0.0)


def load_markets() -> tuple[list[Market], dict[str, list[Market]]]:
    all_markets: list[Market] = []
    events: dict[str, list[Market]] = defaultdict(list)
    for series in SERIES:
        market_path = os.path.join(DATA, f"markets_{series}.jsonl")
        candle_path = os.path.join(DATA, f"candles_{series}.jsonl")
        metadata = {}
        metadata_paths = [market_path, os.path.join(DATA, f"extension_markets_{series}.jsonl")]
        for candidate_path in metadata_paths:
            if not os.path.exists(candidate_path):
                continue
            with open(candidate_path, encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    metadata[row["ticker"]] = row
        candle_rows = {}
        candle_paths = [candle_path, os.path.join(DATA, f"extension_candles_{series}.jsonl")]
        for candidate_path in candle_paths:
            if not os.path.exists(candidate_path):
                continue
            with open(candidate_path, encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    candle_rows[row["ticker"]] = tuple(row["candles"])
        for ticker, row in metadata.items():
            if row.get("result") not in ("yes", "no"):
                continue
            market = Market(
                ticker=ticker,
                series=series,
                event=row["event_ticker"],
                close_ts=parse_ts(row["close_time"]),
                result=row["result"],
                candles=candle_rows.get(ticker, ()),
            )
            all_markets.append(market)
            events[market.event].append(market)
    return all_markets, events


def build_entries(events: dict[str, list[Market]]) -> list[Entry]:
    entries: list[Entry] = []
    for event, markets in events.items():
        close_ts = markets[0].close_ts
        series = markets[0].series
        for hours in HOURS:
            target_ts = close_ts - hours * 3600
            snapshots = []
            for market in markets:
                found = quote_at(market, target_ts)
                if found is None:
                    continue
                candle_ts, candle = found
                yes_quote = quote(candle, "yes")
                if yes_quote is None:
                    continue
                yes_bid, yes_ask, yes_mid = yes_quote
                cumulative_volume = sum(
                    float(c.get("v") or 0.0)
                    for c in market.candles
                    if int(c["t"]) <= candle_ts
                )
                snapshots.append((market, candle_ts, yes_bid, yes_ask, yes_mid, cumulative_volume))
            if not snapshots:
                continue

            # Family 1: the event's most likely exact temperature bucket.
            modal = max(snapshots, key=lambda row: (row[4], -row[3] + row[2], row[5]))
            market, candle_ts, bid, ask, mid, _ = modal
            modal_band = band_label(mid, MODAL_BANDS)
            if modal_band:
                for spread_cap in SPREAD_CAPS:
                    if ask <= 0.99 and ask - bid <= spread_cap:
                        entries.append(
                            Entry(
                                family="modal_yes",
                                series=series,
                                event=event,
                                ticker=market.ticker,
                                close_ts=close_ts,
                                entry_ts=candle_ts,
                                hours=hours,
                                band=modal_band,
                                spread_cap=spread_cap,
                                side="yes",
                                bid=bid,
                                ask=ask,
                                mid=mid,
                                result=market.result,
                                path=side_path(market, "yes", candle_ts),
                            )
                        )

            # Family 2: one liquid already-dominant binary side per event/band.
            # Selection uses only volume observed by the entry timestamp.
            candidates_by_band: dict[str, list[tuple]] = defaultdict(list)
            for market, candle_ts, yes_bid, yes_ask, yes_mid, cumulative_volume in snapshots:
                side = "yes" if yes_mid >= 0.5 else "no"
                side_quote = quote({"b": yes_bid, "a": yes_ask}, side)
                if side_quote is None:
                    continue
                side_bid, side_ask, side_mid = side_quote
                favorite_band = band_label(side_mid, FAVORITE_BANDS)
                if favorite_band and side_ask <= 0.99:
                    candidates_by_band[favorite_band].append(
                        (
                            cumulative_volume,
                            -(side_ask - side_bid),
                            side_mid,
                            market,
                            candle_ts,
                            side,
                            side_bid,
                            side_ask,
                        )
                    )
            for favorite_band, candidates in candidates_by_band.items():
                for spread_cap in SPREAD_CAPS:
                    eligible = [row for row in candidates if row[7] - row[6] <= spread_cap]
                    if not eligible:
                        continue
                    chosen = max(eligible)
                    _, _, side_mid, market, candle_ts, side, side_bid, side_ask = chosen
                    entries.append(
                        Entry(
                            family="binary_favorite",
                            series=series,
                            event=event,
                            ticker=market.ticker,
                            close_ts=close_ts,
                            entry_ts=candle_ts,
                            hours=hours,
                            band=favorite_band,
                            spread_cap=spread_cap,
                            side=side,
                            bid=side_bid,
                            ask=side_ask,
                            mid=side_mid,
                            result=market.result,
                            path=side_path(market, side, candle_ts),
                        )
                    )
                    for stable_periods in (2, 3):
                        stable_candidates = [
                            row
                            for row in eligible
                            if stable_in_band(row[3], row[5], row[4], favorite_band, stable_periods)
                        ]
                        if not stable_candidates:
                            continue
                        stable = max(stable_candidates)
                        _, _, stable_mid, stable_market, stable_ts, stable_side, stable_bid, stable_ask = stable
                        entries.append(
                            Entry(
                                family=f"binary_stable_{stable_periods}h",
                                series=series,
                                event=event,
                                ticker=stable_market.ticker,
                                close_ts=close_ts,
                                entry_ts=stable_ts,
                                hours=hours,
                                band=favorite_band,
                                spread_cap=spread_cap,
                                side=stable_side,
                                bid=stable_bid,
                                ask=stable_ask,
                                mid=stable_mid,
                                result=stable_market.result,
                                path=side_path(stable_market, stable_side, stable_ts),
                            )
                        )
                        if stable_periods == 2:
                            for threshold in (0.30, 0.40, 0.50):
                                displaced_candidates = [
                                    row
                                    for row in stable_candidates
                                    if prior_opposite_peak(row[3], row[5], row[4], stable_periods) >= threshold
                                ]
                                if not displaced_candidates:
                                    continue
                                displaced = max(displaced_candidates)
                                (
                                    _,
                                    _,
                                    displaced_mid,
                                    displaced_market,
                                    displaced_ts,
                                    displaced_side,
                                    displaced_bid,
                                    displaced_ask,
                                ) = displaced
                                entries.append(
                                    Entry(
                                        family=f"binary_displaced_{int(threshold * 100)}",
                                        series=series,
                                        event=event,
                                        ticker=displaced_market.ticker,
                                        close_ts=close_ts,
                                        entry_ts=displaced_ts,
                                        hours=hours,
                                        band=favorite_band,
                                        spread_cap=spread_cap,
                                        side=displaced_side,
                                        bid=displaced_bid,
                                        ask=displaced_ask,
                                        mid=displaced_mid,
                                        result=displaced_market.result,
                                        path=side_path(displaced_market, displaced_side, displaced_ts),
                                    )
                                )
    return entries


def hold_pnl(entry: Entry, quantity: int) -> float:
    entry_fee = taker_fee_per_contract(entry.ask, quantity)
    return (1.0 - entry.ask - entry_fee) if entry.won else (-entry.ask - entry_fee)


def stop_pnl(entry: Entry, quantity: int, stop: float) -> tuple[float, bool]:
    trigger = entry.ask - stop
    for _, bid, _ in entry.path:
        if bid <= trigger:
            return bid - entry.ask - taker_fee_per_contract(entry.ask, quantity) - taker_fee_per_contract(bid, quantity), True
    return hold_pnl(entry, quantity), False


def target_pnl(entry: Entry, quantity: int, target: float) -> tuple[float, bool]:
    trigger = entry.ask + target
    for _, bid, _ in entry.path:
        if bid >= trigger:
            return bid - entry.ask - taker_fee_per_contract(entry.ask, quantity) - taker_fee_per_contract(bid, quantity), True
    return hold_pnl(entry, quantity), False


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def daily_t(entries: list[Entry], pnls: list[float]) -> tuple[int, float]:
    by_day: dict[str, float] = defaultdict(float)
    for entry, pnl in zip(entries, pnls):
        by_day[entry.date] += pnl
    values = list(by_day.values())
    if len(values) < 2:
        return len(values), float("nan")
    sd = statistics.stdev(values)
    return len(values), (mean(values) / (sd / math.sqrt(len(values)))) if sd else float("inf")


def summarize(entries: list[Entry], quantity: int, mode: str = "hold", level: float = 0.0) -> dict:
    if mode == "hold":
        pnls = [hold_pnl(entry, quantity) for entry in entries]
        exits = 0
    elif mode == "stop":
        outcomes = [stop_pnl(entry, quantity, level) for entry in entries]
        pnls = [row[0] for row in outcomes]
        exits = sum(row[1] for row in outcomes)
    else:
        outcomes = [target_pnl(entry, quantity, level) for entry in entries]
        pnls = [row[0] for row in outcomes]
        exits = sum(row[1] for row in outcomes)
    days, t_value = daily_t(entries, pnls)
    expected_wins = sum(entry.ask + taker_fee_per_contract(entry.ask, quantity) for entry in entries)
    calibration_variance = sum(
        (entry.ask + taker_fee_per_contract(entry.ask, quantity))
        * (1.0 - entry.ask - taker_fee_per_contract(entry.ask, quantity))
        for entry in entries
    )
    edge_z = (
        (sum(entry.won for entry in entries) - expected_wins) / math.sqrt(calibration_variance)
        if calibration_variance > 0
        else float("nan")
    )
    return {
        "n": len(entries),
        "days": days,
        "wins": sum(entry.won for entry in entries),
        "win_rate": mean([float(entry.won) for entry in entries]),
        "avg_ask": mean([entry.ask for entry in entries]),
        "net_cents": mean(pnls) * 100.0,
        "total_dollars_10ct": sum(pnls) * 10.0,
        "daily_t": t_value,
        "edge_z": edge_z,
        "exits": exits,
        "exit_rate": exits / len(entries) if entries else 0.0,
    }


def key(entry: Entry) -> tuple:
    return entry.family, entry.hours, entry.band, entry.spread_cap


def fmt(value: float, places: int = 2) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:.{places}f}"


def run(quantity: int) -> str:
    markets, events = load_markets()
    entries = build_entries(events)
    grouped: dict[tuple, list[Entry]] = defaultdict(list)
    for entry in entries:
        grouped[key(entry)].append(entry)

    eligible = []
    for cell, rows in grouped.items():
        training = [row for row in rows if not row.oos]
        validation = [row for row in rows if row.validation]
        fresh = [row for row in rows if row.fresh]
        if len(training) < 50 or len(validation) < 25:
            continue
        eligible.append(
            (
                cell,
                rows,
                summarize(training, quantity),
                summarize(validation, quantity),
                summarize(fresh, quantity),
            )
        )
    eligible.sort(key=lambda row: (row[2]["net_cents"], row[2]["daily_t"]), reverse=True)

    first_date = min(entry.date for entry in entries)
    last_date = max(entry.date for entry in entries)

    lines = [
        "# Independent Weather Entry/Exit Study",
        "",
        f"Data: {len(markets):,} settled contracts, {len(events):,} independent city-day events, "
        f"{len(SERIES)} cities, {first_date} through {last_date}.",
        f"Execution: buy at archived ask; sell at archived bid; general taker fee rounded at {quantity} contracts; "
        "settlement is fee-free. One selected contract per city-day and rule cell.",
        "Selection: cells require at least 50 training trades and 25 original-validation trades. "
        "July 7-26 is validation; July 27-August 4 is a separately downloaded fresh extension.",
        "",
        "## Hold-to-settlement surface",
        "",
        "| Rule | Stage | Band | Spread | Train n | Train net | Validation n | Validation net | Fresh n | Fresh net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cell, _, training, validation, fresh in eligible[:20]:
        family, hours, band, spread_cap = cell
        lines.append(
            f"| {family} | {hours}h | {band}c | <= {spread_cap * 100:.0f}c | "
            f"{training['n']} | {training['net_cents']:+.2f}c | {validation['n']} | "
            f"{validation['net_cents']:+.2f}c | {fresh['n']} | {fresh['net_cents']:+.2f}c |"
        )

    robust = [
        row
        for row in eligible
        if row[2]["net_cents"] > 0 and row[3]["net_cents"] > 0
    ]
    robust.sort(key=lambda row: min(row[2]["net_cents"], row[3]["net_cents"]), reverse=True)
    lines.extend(
        [
            "",
            "## Rules surviving training and original validation",
            "",
            "Fresh results are displayed for every survivor, including failures; the extension was not used to choose the rule.",
            "",
            "| Rule | Stage | Band | Spread | Train net | Validation net | Fresh n | Fresh WR | Fresh net | All edge-z |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for cell, rows, training, validation, fresh in robust:
        family, hours, band, spread_cap = cell
        overall = summarize(rows, quantity)
        lines.append(
            f"| {family} | {hours}h | {band}c | <= {spread_cap * 100:.0f}c | "
            f"{training['net_cents']:+.2f}c | {validation['net_cents']:+.2f}c | {fresh['n']} | "
            f"{fresh['win_rate'] * 100:.1f}% | {fresh['net_cents']:+.2f}c | {overall['edge_z']:+.2f} |"
        )

    lines.extend(["", "## Exit-rule tests for surviving rules", ""])
    chosen = []
    for family in (
        "modal_yes",
        "binary_favorite",
        "binary_stable_2h",
        "binary_stable_3h",
        "binary_displaced_30",
        "binary_displaced_40",
        "binary_displaced_50",
    ):
        family_rows = [row for row in robust if row[0][0] == family]
        if family_rows:
            chosen.append(family_rows[0])
    for cell, _, training, validation, fresh in chosen:
        family, hours, band, spread_cap = cell
        validation_entries = [entry for entry in grouped[cell] if entry.validation]
        fresh_entries = [entry for entry in grouped[cell] if entry.fresh]
        lines.extend(
            [
                f"### {family}: {hours}h, {band}c, spread <= {spread_cap * 100:.0f}c",
                "",
                f"Training: {training['n']} trades at {training['net_cents']:+.2f}c/contract. "
                f"Original validation: {validation['n']} at {validation['net_cents']:+.2f}c.",
                "",
                "| Exit rule | Validation triggers | Validation net | Fresh n | Fresh triggers | Fresh net |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        validation_hold = summarize(validation_entries, quantity)
        fresh_hold = summarize(fresh_entries, quantity)
        lines.append(
            f"| Hold to settlement | 0 | {validation_hold['net_cents']:+.2f}c | "
            f"{fresh_hold['n']} | 0 | {fresh_hold['net_cents']:+.2f}c |"
        )
        for stop in STOP_LEVELS:
            validation_result = summarize(validation_entries, quantity, "stop", stop)
            fresh_result = summarize(fresh_entries, quantity, "stop", stop)
            lines.append(
                f"| Stop at -{stop * 100:.0f}c | {validation_result['exits']} | "
                f"{validation_result['net_cents']:+.2f}c | {fresh_result['n']} | "
                f"{fresh_result['exits']} | {fresh_result['net_cents']:+.2f}c |"
            )
        for target in TARGET_LEVELS:
            validation_result = summarize(validation_entries, quantity, "target", target)
            fresh_result = summarize(fresh_entries, quantity, "target", target)
            lines.append(
                f"| Take profit at +{target * 100:.0f}c | {validation_result['exits']} | "
                f"{validation_result['net_cents']:+.2f}c | {fresh_result['n']} | "
                f"{fresh_result['exits']} | {fresh_result['net_cents']:+.2f}c |"
            )
        lines.append("")

    lines.extend(["## City decomposition of selected hold rules", ""])
    for cell, _, _, _, _ in chosen:
        family, hours, band, spread_cap = cell
        lines.extend(
            [
                f"### {family}: {hours}h, {band}c, <= {spread_cap * 100:.0f}c",
                "",
                "| City series | IS n | IS net/ct | OOS n | OOS WR | OOS net/ct |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        rows = grouped[cell]
        for series in SERIES:
            ins = [entry for entry in rows if entry.series == series and not entry.oos]
            outs = [entry for entry in rows if entry.series == series and entry.oos]
            if not ins and not outs:
                continue
            is_result = summarize(ins, quantity) if ins else None
            oos_result = summarize(outs, quantity) if outs else None
            lines.append(
                f"| {series} | {len(ins)} | "
                f"{is_result['net_cents'] if is_result else float('nan'):+.2f}c | {len(outs)} | "
                f"{(oos_result['win_rate'] * 100) if oos_result else float('nan'):.1f}% | "
                f"{oos_result['net_cents'] if oos_result else float('nan'):+.2f}c |"
            )
        lines.append("")

    candidate_cell = ("binary_stable_2h", 12, "80-90", 0.04)
    if candidate_cell in grouped:
        candidate_rows = grouped[candidate_cell]
        selected_cities = set()
        for series in SERIES:
            city_training = [
                entry for entry in candidate_rows if entry.series == series and not entry.oos
            ]
            if len(city_training) >= 5 and summarize(city_training, quantity)["net_cents"] > 0:
                selected_cities.add(series)
        screened = [entry for entry in candidate_rows if entry.series in selected_cities]
        screened_training = [entry for entry in screened if not entry.oos]
        screened_validation = [entry for entry in screened if entry.validation]
        screened_fresh = [entry for entry in screened if entry.fresh]
        lines.extend(
            [
                "## Exploratory city-screened candidate",
                "",
                "This is an exploratory specialization, not another independent confirmation. Cities were selected "
                "only when they had at least five training entries and positive training P&L.",
                "",
                f"Selected series: {', '.join(sorted(selected_cities))}.",
                "",
                "| Period | Trades | Wins | Average ask | Net/contract | Day-t | Edge-z |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for label, period_entries in (
            ("Training", screened_training),
            ("Original validation", screened_validation),
            ("Fresh extension", screened_fresh),
            ("All", screened),
        ):
            result = summarize(period_entries, quantity)
            lines.append(
                f"| {label} | {result['n']} | {result['wins']} | {result['avg_ask'] * 100:.1f}c | "
                f"{result['net_cents']:+.2f}c | {fmt(result['daily_t'])} | {result['edge_z']:+.2f} |"
            )
        all_result = summarize(screened, quantity)
        calendar_days = len({entry.date for entry in entries})
        estimated_daily = all_result["total_dollars_10ct"] / calendar_days if calendar_days else float("nan")
        lines.extend(
            [
                "",
                f"At {quantity} contracts, the archive averages approximately ${estimated_daily:.2f} per calendar day "
                "before unobserved depth/slippage. A typical win earns roughly $1.40 at this size; a settlement loss "
                "costs roughly $8.50, so the result depends on keeping the loss rate well below the market-implied rate.",
                "",
            ]
        )

    lines.extend(
        [
            "## Limits",
            "",
            "- Candles are hourly BBO closes, so intrahour stop fills and queue depth cannot be reconstructed.",
            "- A displayed ask does not prove the desired size was available; this is signal validation, not fill validation.",
            "- Cities can share weather regimes, so day-level t-statistics are shown instead of treating every strike as independent.",
            "- No live rule should be promoted from this archive unless it is positive in the chronological holdout and then survives forward paper execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantity", type=int, default=10)
    parser.add_argument("--output", default=os.path.join(ROOT, "WEATHER_ENTRY_EXIT_REPORT.md"))
    args = parser.parse_args()
    report = run(args.quantity)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(report)
    print(report)


if __name__ == "__main__":
    main()
