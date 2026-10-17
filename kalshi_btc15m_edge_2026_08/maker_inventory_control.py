#!/usr/bin/env python3
"""Queue-aware maker capture with a short inventory grace period."""

from __future__ import annotations

import math
import statistics

import maker_capture
import study


def fill_times(window: dict, trades: list[dict]) -> tuple[float | None, float | None]:
    start = window["start"]
    bid, ask = start["yes_bid"], start["yes_ask"]
    bid_ahead, ask_ahead = start["bid_size"], start["ask_size"]
    bid_consumed = ask_consumed = 0.0
    bid_fill = ask_fill = None
    for trade in trades:
        if bid_fill is None and trade["taker_side"] == "no":
            if trade["yes"] < bid - study.EPSILON:
                bid_fill = trade["ts"]
            elif abs(trade["yes"] - bid) <= study.EPSILON:
                bid_consumed += trade["count"]
                if bid_consumed >= bid_ahead + 1:
                    bid_fill = trade["ts"]
        if ask_fill is None and trade["taker_side"] == "yes":
            if trade["yes"] > ask + study.EPSILON:
                ask_fill = trade["ts"]
            elif abs(trade["yes"] - ask) <= study.EPSILON:
                ask_consumed += trade["count"]
                if ask_consumed >= ask_ahead + 1:
                    ask_fill = trade["ts"]
    return bid_fill, ask_fill


def first_exit_print(trades: list[dict], after: float, taker_side: str) -> dict | None:
    return next(
        (
            trade for trade in trades
            if trade["ts"] >= after and trade["taker_side"] == taker_side
        ),
        None,
    )


def simulate(window: dict, trades: list[dict], grace_seconds: float) -> maker_capture.QuoteResult:
    start, end = window["start"], window["end"]
    bid, ask = start["yes_bid"], start["yes_ask"]
    bid_fill, ask_fill = fill_times(window, trades)
    first_candidates = [value for value in (bid_fill, ask_fill) if value is not None]
    if not first_candidates:
        return maker_capture.QuoteResult(
            window["ticker"], start["ts"], 0.0, 0, "none", ask - bid
        )
    first = min(first_candidates)
    if bid_fill is not None and ask_fill is not None and abs(bid_fill - ask_fill) <= grace_seconds:
        return maker_capture.QuoteResult(
            window["ticker"], start["ts"], ask - bid, 2, "both", ask - bid
        )

    deadline = first + grace_seconds
    if bid_fill == first:
        exit_print = first_exit_print(trades, deadline, "no")
        exit_bid = exit_print["yes"] if exit_print else end["yes_bid"]
        pnl = exit_bid - bid - study.fee(exit_bid)
        fill_type = "bid_only"
    else:
        exit_print = first_exit_print(trades, deadline, "yes")
        no_exit_bid = 1 - exit_print["yes"] if exit_print else 1 - end["yes_ask"]
        no_entry = 1 - ask
        pnl = no_exit_bid - no_entry - study.fee(no_exit_bid)
        fill_type = "ask_only"
    return maker_capture.QuoteResult(
        window["ticker"], start["ts"], pnl, 1, fill_type, ask - bid
    )


def day_pvalue(rows: list[maker_capture.QuoteResult], stress_cents: float = 0.10) -> float:
    grouped = {}
    for row in rows:
        grouped.setdefault(row.ts // 86400, []).append(
            row.pnl - row.maker_fills * stress_cents / 100
        )
    values = [sum(items) / len(items) for items in grouped.values()]
    if len(values) < 2 or statistics.stdev(values) == 0:
        return 1.0
    statistic = statistics.mean(values) / (statistics.stdev(values) / math.sqrt(len(values)))
    return 0.5 * math.erfc(statistic / math.sqrt(2))


def main() -> None:
    markets = study.load_markets()
    windows = maker_capture.market_windows(markets)
    trade_windows = maker_capture.pull(windows)
    split = sorted(window["start"]["ts"] for window in windows)[len(windows) // 2]
    variants = {}
    for grace in (1.0, 2.0, 5.0):
        variants[grace] = [
            simulate(
                window,
                trade_windows[(window["ticker"], window["start"]["ts"])],
                grace,
            )
            for window in windows
        ]
        print(f"\n=== grace={grace:.0f}s ===")
        for stress in (0.0, 0.10, 0.25):
            print(f"maker stress={stress:.2f}c")
            maker_capture.summary(
                "first half", [row for row in variants[grace] if row.ts < split], stress
            )
            maker_capture.summary(
                "final half", [row for row in variants[grace] if row.ts >= split], stress
            )

    print("\n=== three-variant multiplicity audit, 0.10c maker stress ===")
    ranked = sorted((day_pvalue(rows), grace, rows) for grace, rows in variants.items())
    for rank, (pvalue, grace, rows) in enumerate(ranked, start=1):
        adjusted = [row.pnl - row.maker_fills * 0.001 for row in rows]
        limit = 0.05 * rank / len(ranked)
        print(
            f"grace={grace:.0f}s n={len(rows)} EV/quote={100*statistics.mean(adjusted):+.3f}c "
            f"day-p={pvalue:.4f} BH={limit:.4f} {'PASS' if pvalue <= limit else 'fail'}"
        )


if __name__ == "__main__":
    main()
