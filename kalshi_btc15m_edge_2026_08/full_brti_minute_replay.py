#!/usr/bin/env python3
"""Expand frozen S2 to the full archive using final-minute candle opens."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

import brti_scalp_study
import settlement_average_study as settlement
import study


FULL_TAPE = Path(__file__).resolve().parent / "data" / "brti_seconds_full.jsonl"
MIN_PROBABILITY = 0.80
MIN_EXPECTED_EV = 0.03
MAX_SPREAD = 0.02


@dataclass(frozen=True)
class Row:
    ticker: str
    close_ts: int
    result: str
    p_yes: float
    market_mid: float
    side: str
    entry: float
    p_win: float
    expected_ev: float
    pnl: float


@dataclass(frozen=True)
class Calibration:
    close_ts: int
    result: str
    p_yes: float
    market_mid: float


def build_rows(
    markets: list[study.Market], tape: settlement.BrtiTape
) -> tuple[list[Row], dict, list[Calibration], list[Row]]:
    rows = []
    all_rows = []
    calibrations = []
    counters = {
        "candidate_markets": 0,
        "missing_tape": 0,
        "reconstruction_rejected": 0,
        "missing_quote": 0,
        "qualified": 0,
    }
    for market in markets:
        candle = market.by_rem.get(0)
        if candle is None or market.strike is None or market.expiration is None:
            continue
        counters["candidate_markets"] += 1
        opening = tape.boundary_average(market.close_ts - 15 * 60)
        closing = tape.boundary_average(market.close_ts)
        timestamp = market.close_ts - 60
        current = tape.by_second.get(timestamp - 1)
        sigma = tape.innovation_sigma(timestamp)
        if opening is None or closing is None or current is None or sigma is None:
            counters["missing_tape"] += 1
            continue
        if (
            abs(opening - market.strike) > settlement.RECONSTRUCTION_TOLERANCE
            or abs(closing - market.expiration) > settlement.RECONSTRUCTION_TOLERANCE
        ):
            counters["reconstruction_rejected"] += 1
            continue
        p_yes = brti_scalp_study.future_average_probability(market.strike, current, sigma)
        side = "yes" if p_yes >= 0.5 else "no"
        quote = study.side_quote(candle, side, "open")
        yes_quote = study.yes_quote(candle, "open")
        if quote is None or yes_quote is None:
            counters["missing_quote"] += 1
            continue
        bid, ask = quote
        yes_bid, yes_ask = yes_quote
        p_win = max(p_yes, 1 - p_yes)
        expected_ev = p_win - ask - study.fee(ask)
        calibrations.append(
            Calibration(market.close_ts, market.result, p_yes, (yes_bid + yes_ask) / 2)
        )
        won = market.result == side
        row = Row(
            market.ticker,
            market.close_ts,
            market.result,
            p_yes,
            (yes_bid + yes_ask) / 2,
            side,
            ask,
            p_win,
            expected_ev,
            study.settlement_pnl(ask, won),
        )
        if ask - bid <= MAX_SPREAD + study.EPSILON:
            all_rows.append(row)
        if (
            p_win < MIN_PROBABILITY
            or expected_ev < MIN_EXPECTED_EV
            or ask - bid > MAX_SPREAD + study.EPSILON
        ):
            continue
        counters["qualified"] += 1
        rows.append(row)
    return rows, counters, calibrations, all_rows


def brier(rows: list, source: str) -> float:
    if not rows:
        return float("nan")
    return statistics.mean(
        (
            (row.p_yes if source == "model" else row.market_mid)
            - float(row.result == "yes")
        )
        ** 2
        for row in rows
    )


def segment(rows: list, start: int | None = None, end: int | None = None) -> list:
    return [
        row
        for row in rows
        if (start is None or row.close_ts >= start) and (end is None or row.close_ts < end)
    ]


def summarize(label: str, rows: list[Row]) -> None:
    if not rows:
        print(f"{label:28} n=0")
        return
    trades = [
        study.Trade("S2_full", row.ticker, row.close_ts, row.side, row.entry, row.pnl, 1)
        for row in rows
    ]
    bootstrap = study.bootstrap_days(trades, 0.25, 20_000)
    print(
        f"{label:28} n={len(rows):4d} "
        f"win={sum(row.pnl > 0 for row in rows)/len(rows):6.1%} "
        f"raw={100*statistics.mean(row.pnl for row in rows):+7.3f}c "
        f"stress25={100*statistics.mean(row.pnl for row in rows)-0.25:+7.3f}c "
        f"stress50={100*statistics.mean(row.pnl for row in rows)-0.50:+7.3f}c "
        f"modelEV={100*statistics.mean(row.expected_ev for row in rows):+7.3f}c "
        f"Brier(model/market)={brier(rows, 'model'):.4f}/{brier(rows, 'market'):.4f} "
        f"bootP={bootstrap['p_positive']:5.1%} p05={bootstrap['p05']:+6.3f}c"
    )


def main() -> None:
    tape = settlement.BrtiTape.load(FULL_TAPE)
    rows, counters, calibrations, all_rows = build_rows(study.load_markets(), tape)
    print(f"whole_second_BRTI={len(tape.by_second)} counters={counters}")
    july_start = study.FRESH_START - 26 * 86400
    for label, values in (
        ("discovery", segment(calibrations, end=study.VALIDATION_START)),
        ("validation", segment(calibrations, study.VALIDATION_START, july_start)),
        ("historical", segment(calibrations, july_start, study.FRESH_START)),
        ("final", segment(calibrations, study.FRESH_START)),
    ):
        if values:
            print(
                f"calibration {label:10} n={len(values):4d} "
                f"Brier(model/market)={brier(values, 'model'):.5f}/"
                f"{brier(values, 'market'):.5f}"
            )
    summarize("discovery before Jun15", segment(rows, end=study.VALIDATION_START))
    summarize(
        "validation Jun15-Jun30",
        segment(rows, study.VALIDATION_START, study.FRESH_START - 26 * 86400),
    )
    summarize("historical Jul1-Jul26", segment(rows, july_start, study.FRESH_START))
    summarize("final Jul27+", segment(rows, study.FRESH_START))
    summarize("all", rows)
    if rows:
        print(
            f"YES/NO={sum(row.side == 'yes' for row in rows)}/"
            f"{sum(row.side == 'no' for row in rows)} "
            f"mean_entry={statistics.mean(row.entry for row in rows):.4f} "
            f"mean_p={statistics.mean(row.p_win for row in rows):.4f}"
        )
    print("\nneighbor sensitivity (diagnostic only; primary remains p>=0.80, EV>=3c):")
    for minimum_probability in (0.75, 0.80, 0.85, 0.90):
        for minimum_ev in (0.01, 0.02, 0.03, 0.04, 0.05):
            selected = [
                row
                for row in all_rows
                if row.p_win >= minimum_probability and row.expected_ev >= minimum_ev
            ]
            if not selected:
                continue
            parts = (
                segment(selected, end=study.VALIDATION_START),
                segment(selected, study.VALIDATION_START, july_start),
                segment(selected, july_start, study.FRESH_START),
                segment(selected, study.FRESH_START),
            )
            evs = [
                100 * statistics.mean(row.pnl for row in part) - 0.25
                if part
                else float("nan")
                for part in parts
            ]
            print(
                f"p>={minimum_probability:.2f} EV>={100*minimum_ev:.0f}c "
                f"n={len(selected):4d} all25="
                f"{100*statistics.mean(row.pnl for row in selected)-0.25:+7.3f}c "
                f"segments=" + ",".join(
                    "nan" if value != value else f"{value:+.2f}" for value in evs
                )
            )


if __name__ == "__main__":
    main()
