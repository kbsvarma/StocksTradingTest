#!/usr/bin/env python3
"""Execute the frozen early-entry and scalp rule families."""

from __future__ import annotations

import math
import statistics

import study


TEST_START = study.parse_time("2026-07-01T00:00:00Z")
FINAL_START = study.FRESH_START


def quote_mid(market: study.Market, remainder: int, side: str) -> float | None:
    quote = study.side_quote(market.by_rem.get(remainder) or {}, side)
    return None if quote is None else (quote[0] + quote[1]) / 2


def scalp_exit(
    market: study.Market,
    side: str,
    signal_remainder: int,
    entry: float,
    target: float | None,
    stop: float | None,
    horizon: int,
) -> float | None:
    last_bid = None
    observed = 0
    for remainder in range(signal_remainder - 1, 0, -1):
        quote = study.side_quote(market.by_rem.get(remainder) or {}, side)
        if quote is None:
            continue
        observed += 1
        last_bid = quote[0]
        move = last_bid - entry
        if target is not None and move >= target:
            break
        if stop is not None and move <= -stop:
            break
        if observed >= horizon:
            break
    if last_bid is None:
        return None
    return last_bid - entry - study.fee(entry) - study.fee(last_bid)


def early_momentum(
    markets: list[study.Market],
    target: float | None = 0.05,
    stop: float | None = 0.08,
    horizon: int = 3,
) -> list[study.Trade]:
    trades = []
    for market in markets:
        remainder = 12
        quote = study.favorite_quote(market.by_rem.get(remainder) or {})
        if quote is None:
            continue
        side, bid, ask, mid = quote
        prior = quote_mid(market, 14, side)
        if (
            prior is None
            or not 0.60 <= mid < 0.80
            or mid - prior < 0.08
            or ask - bid > 0.01 + study.EPSILON
        ):
            continue
        entry = study.executable_entry(market, remainder, side)
        if entry is None or entry > 0.85:
            continue
        pnl = scalp_exit(market, side, remainder, entry, target, stop, horizon)
        if pnl is None:
            continue
        trades.append(
            study.Trade(
                "early_momentum", market.ticker, market.close_ts, side,
                entry, pnl, remainder,
            )
        )
    return trades


def early_reversal(markets: list[study.Market]) -> list[study.Trade]:
    trades = []
    for market in markets:
        remainder = 12
        quote = study.favorite_quote(market.by_rem.get(remainder) or {})
        if quote is None:
            continue
        favorite_side, favorite_bid, favorite_ask, favorite_mid = quote
        prior = quote_mid(market, 14, favorite_side)
        if (
            prior is None
            or not 0.70 <= favorite_mid < 0.85
            or favorite_mid - prior < 0.15
            or favorite_ask - favorite_bid > 0.01 + study.EPSILON
        ):
            continue
        side = "no" if favorite_side == "yes" else "yes"
        entry = study.executable_entry(market, remainder, side)
        if entry is None or entry > 0.35:
            continue
        pnl = scalp_exit(market, side, remainder, entry, 0.05, 0.08, 3)
        if pnl is None:
            continue
        trades.append(
            study.Trade(
                "early_reversal", market.ticker, market.close_ts, side,
                entry, pnl, remainder,
            )
        )
    return trades


def index_lag(markets: list[study.Market]) -> list[study.Trade]:
    perps = study.load_perp()
    trades = []
    for market in markets:
        for remainder in (12, 10):
            decision_ts = market.close_ts - remainder * 60
            current_index = study.previous_value(perps, decision_ts, 60)
            prior_index = study.previous_value(perps, decision_ts - 60, 60)
            if current_index is None or prior_index is None:
                continue
            move_bps = math.log(current_index / prior_index) * 10_000
            if abs(move_bps) < 5:
                continue
            side = "yes" if move_bps > 0 else "no"
            current_quote = study.side_quote(market.by_rem.get(remainder) or {}, side)
            prior_mid = quote_mid(market, remainder + 1, side)
            if current_quote is None or prior_mid is None:
                continue
            bid, ask = current_quote
            current_mid = (bid + ask) / 2
            if (
                current_mid - prior_mid > 0.02 + study.EPSILON
                or ask - bid > 0.01 + study.EPSILON
                or ask > 0.75
            ):
                continue
            entry = study.executable_entry(market, remainder, side)
            if entry is None or entry > 0.78:
                continue
            pnl = scalp_exit(market, side, remainder, entry, 0.05, 0.05, 2)
            if pnl is None:
                continue
            trades.append(
                study.Trade(
                    "index_lag", market.ticker, market.close_ts, side,
                    entry, pnl, remainder,
                )
            )
            break
    return trades


def oscillation_lock(markets: list[study.Market]) -> list[study.Trade]:
    trades = []
    for market in markets:
        initiated = None
        for remainder in (13, 12, 11, 10):
            candidates = []
            for side in ("yes", "no"):
                current = study.side_quote(market.by_rem.get(remainder) or {}, side)
                initial_mid = quote_mid(market, 14, side)
                if current is None or initial_mid is None:
                    continue
                bid, ask = current
                if ask <= 0.30 and initial_mid >= 0.40 and ask - bid <= 0.01 + study.EPSILON:
                    candidates.append((ask, side))
            if not candidates:
                continue
            _, side = min(candidates)
            entry = study.executable_entry(market, remainder, side)
            if entry is not None and entry <= 0.32:
                initiated = (remainder, side, entry)
                break
        if initiated is None:
            continue

        entry_remainder, side, entry = initiated
        opposite = "no" if side == "yes" else "yes"
        pnl = None
        last_bid = None
        for remainder in range(entry_remainder - 1, 4, -1):
            opposite_entry = study.executable_entry(market, remainder, opposite)
            if opposite_entry is not None:
                all_in = entry + opposite_entry + study.fee(entry) + study.fee(opposite_entry)
                if all_in <= 0.95 + study.EPSILON:
                    pnl = 1 - all_in
                    break
            current = study.side_quote(market.by_rem.get(remainder) or {}, side)
            if current is None:
                continue
            last_bid = current[0]
            if last_bid <= entry - 0.10:
                pnl = last_bid - entry - study.fee(entry) - study.fee(last_bid)
                break
        if pnl is None and last_bid is not None:
            pnl = last_bid - entry - study.fee(entry) - study.fee(last_bid)
        if pnl is None:
            continue
        trades.append(
            study.Trade(
                "oscillation_lock", market.ticker, market.close_ts, side,
                entry, pnl, entry_remainder,
            )
        )
    return trades


def periods(trades: list[study.Trade]):
    return (
        ("discovery", study.segment(trades, end=study.VALIDATION_START)),
        ("validation", study.segment(trades, study.VALIDATION_START, TEST_START)),
        ("historical test", study.segment(trades, TEST_START, FINAL_START)),
        ("final holdout", study.segment(trades, FINAL_START)),
    )


def report(label: str, trades: list[study.Trade]) -> None:
    print(f"\n=== {label} ===")
    for period, rows in periods(trades):
        print(study.summarize(period, rows))


def day_pvalue(trades: list[study.Trade], stress_cents: float = 0.25) -> float:
    grouped = {}
    for trade in trades:
        grouped.setdefault(trade.close_ts // 86400, []).append(trade.pnl - stress_cents / 100)
    values = [sum(rows) / len(rows) for rows in grouped.values()]
    if len(values) < 2 or statistics.stdev(values) == 0:
        return 1.0
    statistic = statistics.mean(values) / (statistics.stdev(values) / math.sqrt(len(values)))
    return 0.5 * math.erfc(statistic / math.sqrt(2))


def main() -> None:
    markets = study.load_markets()
    strategies = {
        "M1 primary TP5/SL8/H3": early_momentum(markets, 0.05, 0.08, 3),
        "M1 sensitivity TP10/SL8/H3": early_momentum(markets, 0.10, 0.08, 3),
        "M1 sensitivity TP5/noSL/H3": early_momentum(markets, 0.05, None, 3),
        "M1 sensitivity TP5/SL8/H5": early_momentum(markets, 0.05, 0.08, 5),
        "R1 early reversal": early_reversal(markets),
        "L1 independent-index lag": index_lag(markets),
        "O1 oscillation lock": oscillation_lock(markets),
    }
    for label, trades in strategies.items():
        report(label, trades)

    print("\n=== frozen-family multiplicity audit on final holdout ===")
    rows = []
    for label, trades in strategies.items():
        final = study.segment(trades, FINAL_START)
        rows.append((day_pvalue(final), label, final))
    rows.sort()
    count = len(rows)
    for rank, (pvalue, label, final) in enumerate(rows, start=1):
        limit = 0.05 * rank / count
        ev25 = 100 * statistics.mean(t.pnl for t in final) - 0.25 if final else float("nan")
        print(
            f"{rank}. {label:34} n={len(final):4d} ev25={ev25:+7.3f}c "
            f"day-p={pvalue:.4f} BH={limit:.4f} {'PASS' if pvalue <= limit else 'fail'}"
        )


if __name__ == "__main__":
    main()
