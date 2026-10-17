#!/usr/bin/env python3
"""Test whether the rem8 anomaly is an early favorite-price breakout."""

from __future__ import annotations

import statistics

import midband_candidate as midband
import study


def side_mid(market: study.Market, remainder: int, side: str) -> float | None:
    quote = study.side_quote(market.by_rem.get(remainder) or {}, side)
    return None if quote is None else (quote[0] + quote[1]) / 2


def breakout(
    markets: list[study.Market],
    remainders: tuple[int, ...],
    cross_level: float,
    max_mid: float,
    minimum_jump: float,
    lookback: int,
) -> list[study.Trade]:
    trades = []
    for market in markets:
        for remainder in remainders:
            quote = study.favorite_quote(market.by_rem.get(remainder) or {})
            if quote is None:
                continue
            side, bid, ask, mid = quote
            if not cross_level <= mid < max_mid or ask - bid > 0.01 + study.EPSILON:
                continue
            prior = side_mid(market, remainder + lookback, side)
            if prior is None or prior >= cross_level or mid - prior < minimum_jump:
                continue
            entry = study.executable_entry(market, remainder, side)
            if entry is None or entry > 0.95:
                continue
            trades.append(
                study.Trade(
                    "breakout", market.ticker, market.close_ts, side, entry,
                    study.settlement_pnl(entry, market.result == side), remainder,
                )
            )
            break
    return trades


def momentum(
    markets: list[study.Market],
    remainders: tuple[int, ...],
    low: float,
    high: float,
    minimum_jump: float,
    lookback: int,
) -> list[study.Trade]:
    trades = []
    for market in markets:
        for remainder in remainders:
            quote = study.favorite_quote(market.by_rem.get(remainder) or {})
            if quote is None:
                continue
            side, bid, ask, mid = quote
            prior = side_mid(market, remainder + lookback, side)
            if (
                not low <= mid < high
                or ask - bid > 0.01 + study.EPSILON
                or prior is None
                or mid - prior < minimum_jump
            ):
                continue
            entry = study.executable_entry(market, remainder, side)
            if entry is None or entry > 0.95:
                continue
            trades.append(
                study.Trade(
                    "momentum", market.ticker, market.close_ts, side, entry,
                    study.settlement_pnl(entry, market.result == side), remainder,
                )
            )
            break
    return trades


def report(label: str, trades: list[study.Trade], split: int) -> None:
    print(f"\n{label}")
    for segment_label, start, end in (
        ("discovery", None, study.VALIDATION_START),
        ("historical validation", study.VALIDATION_START, study.FRESH_START),
        ("fresh first", study.FRESH_START, split),
        ("fresh final", split, None),
    ):
        rows = study.segment(trades, start, end)
        print(study.summarize(segment_label, rows))
        if rows:
            print(
                f"{'':34} avg_entry={statistics.mean(t.entry for t in rows):.4f} "
                f"yes={sum(t.side == 'yes' for t in rows)/len(rows):.1%}"
            )


def main() -> None:
    markets = study.load_markets()
    base = midband.cell(markets, 0.85, 0.90, 8)
    split = midband.fresh_split(base)
    definitions = {
        "cross 85, rem8-7, >=3c/1m": breakout(markets, (8, 7), 0.85, 0.93, 0.03, 1),
        "cross 85, rem10-6, >=3c/1m": breakout(markets, (10, 9, 8, 7, 6), 0.85, 0.93, 0.03, 1),
        "cross 85, rem10-6, >=5c/2m": breakout(markets, (10, 9, 8, 7, 6), 0.85, 0.93, 0.05, 2),
        "momentum >=3c/1m, 85-93": momentum(markets, (10, 9, 8, 7, 6), 0.85, 0.93, 0.03, 1),
        "momentum >=5c/2m, 85-93": momentum(markets, (10, 9, 8, 7, 6), 0.85, 0.93, 0.05, 2),
        "momentum >=8c/2m, 80-93": momentum(markets, (10, 9, 8, 7, 6), 0.80, 0.93, 0.08, 2),
    }
    for label, trades in definitions.items():
        report(label, trades, split)


if __name__ == "__main__":
    main()
