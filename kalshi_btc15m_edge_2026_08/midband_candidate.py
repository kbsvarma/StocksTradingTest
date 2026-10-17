#!/usr/bin/env python3
"""Independent replay and execution study for the 85-90c/rem8 anomaly.

This cell was discovered in a 35-cell fresh-period diagnostic surface. It is
therefore reported with multiplicity penalties and is not allowed to use the
same fresh sample as a promotion holdout.
"""

from __future__ import annotations

import math
import statistics
import time

import study


DISCOVERY_END = study.VALIDATION_START


def cell_trade(
    market: study.Market,
    low: float,
    high: float,
    remainder: int,
    require_unbroken: bool = False,
) -> study.Trade | None:
    quote = study.favorite_quote(market.by_rem.get(remainder) or {})
    if quote is None:
        return None
    side, bid, ask, mid = quote
    if not low <= mid < high or ask - bid > 0.01 + study.EPSILON:
        return None
    if require_unbroken:
        path = study.path_state(market, remainder, side)
        if path["observations"] < 5 or path["flips"] or not path["all_same"]:
            return None
    entry = study.executable_entry(market, remainder, side)
    if entry is None or entry >= 0.99:
        return None
    return study.Trade(
        "midband", market.ticker, market.close_ts, side, entry,
        study.settlement_pnl(entry, market.result == side), remainder,
    )


def cell(markets, low, high, remainder, require_unbroken=False):
    return [
        trade for market in markets
        if (trade := cell_trade(market, low, high, remainder, require_unbroken))
    ]


def first_in_window(markets, low, high, remainders):
    trades = []
    for market in markets:
        for remainder in remainders:
            trade = cell_trade(market, low, high, remainder)
            if trade:
                trades.append(trade)
                break
    return trades


def exit_bid(market: study.Market, remainder: int, side: str) -> float | None:
    quote = study.side_quote(market.by_rem.get(remainder) or {}, side)
    return None if quote is None else quote[0]


def managed_exit(
    trades: list[study.Trade],
    markets: dict[str, study.Market],
    take_profit: float | None,
    stop_loss: float | None,
) -> list[study.Trade]:
    output = []
    for trade in trades:
        market = markets[trade.ticker]
        pnl = trade.pnl
        for remainder in range(trade.signal_rem - 1, 0, -1):
            bid = exit_bid(market, remainder, trade.side)
            if bid is None:
                continue
            gain = bid - trade.entry
            should_exit = (
                (take_profit is not None and gain >= take_profit)
                or (stop_loss is not None and gain <= -stop_loss)
            )
            if should_exit:
                pnl = bid - trade.entry - study.fee(trade.entry) - study.fee(bid)
                break
        output.append(
            study.Trade(
                f"managed_tp{take_profit}_sl{stop_loss}", trade.ticker,
                trade.close_ts, trade.side, trade.entry, pnl, trade.signal_rem,
            )
        )
    return output


def fresh_split(trades: list[study.Trade]) -> int:
    times = sorted({trade.close_ts for trade in trades if trade.close_ts >= study.FRESH_START})
    return times[len(times) // 2]


def report(label: str, trades: list[study.Trade], split: int) -> None:
    print(f"\n{label}")
    print(study.summarize("discovery", study.segment(trades, end=DISCOVERY_END)))
    print(study.summarize("historical validation", study.segment(trades, DISCOVERY_END, study.FRESH_START)))
    print(study.summarize("fresh first half", study.segment(trades, study.FRESH_START, split)))
    print(study.summarize("fresh final half", study.segment(trades, split)))


def normal_pvalue(trades: list[study.Trade], stress_cents: float = 0.25) -> float:
    daily = {}
    for trade in trades:
        day = trade.close_ts // 86400
        daily.setdefault(day, []).append(trade.pnl - stress_cents / 100)
    values = [sum(rows) / len(rows) for rows in daily.values()]
    if len(values) < 2 or statistics.stdev(values) == 0:
        return 1.0
    t_stat = statistics.mean(values) / (statistics.stdev(values) / math.sqrt(len(values)))
    return 0.5 * math.erfc(t_stat / math.sqrt(2))


def multiplicity(markets: list[study.Market]) -> None:
    """Benjamini-Hochberg audit of the same 35 diagnostic cells."""
    rows = []
    for low, high in zip(study.PRICE_BUCKETS, study.PRICE_BUCKETS[1:]):
        if low < 0.80:
            continue
        for remainder in (8, 7, 6, 5, 4, 3, 2):
            trades = study.segment(cell(markets, low, high, remainder), study.FRESH_START)
            if len(trades) < 40:
                continue
            rows.append(
                {
                    "cell": f"{low:.2f}-{high:.2f}/r{remainder}",
                    "p": normal_pvalue(trades),
                    "n": len(trades),
                    "ev": 100 * statistics.mean(t.pnl for t in trades) - 0.25,
                }
            )
    rows.sort(key=lambda row: row["p"])
    count = len(rows)
    print(f"\n=== multiplicity audit: {count} eligible cells ===")
    for rank, row in enumerate(rows[:12], start=1):
        row["bh_limit"] = 0.05 * rank / count
        print(
            f"{rank:2d} {row['cell']:14} n={row['n']:3d} ev25={row['ev']:+6.2f}c "
            f"day-p={row['p']:.4f} BH-limit={row['bh_limit']:.4f} "
            f"{'PASS' if row['p'] <= row['bh_limit'] else 'fail'}"
        )


def book_depth(markets: list[study.Market]) -> None:
    books = study.load_books()
    market_by_ticker = {market.ticker: market for market in markets}
    rows = []
    for ticker, snapshots in books.items():
        market = market_by_ticker.get(ticker)
        if not market:
            continue
        signal = study.first_snapshot(snapshots, market.close_ts - 8 * 60)
        if signal is None:
            continue
        yes_mid = signal["mid"]
        favorite_mid = max(yes_mid, 1 - yes_mid)
        if not 0.85 <= favorite_mid < 0.90:
            continue
        if yes_mid >= 0.5:
            favorite_ask = signal["yes_ask"]
            favorite_ask_size = signal["ask_size"]
        else:
            favorite_ask = 1 - signal["yes_bid"]
            favorite_ask_size = signal["bid_size"]
        rows.append((favorite_ask, favorite_ask_size))
    print("\n=== second-level executable depth for 85-90/rem8 ===")
    if not rows:
        print("no aligned snapshots")
        return
    sizes = sorted(size for _, size in rows)
    print(
        f"signals={len(rows)} median top ask size={statistics.median(sizes):.1f} "
        f"p10={sizes[int(.10*(len(sizes)-1))]:.1f} "
        f"under10={sum(size < 10 for size in sizes)}"
    )


def main() -> None:
    markets = study.load_markets()
    market_map = {market.ticker: market for market in markets}
    base = cell(markets, 0.85, 0.90, 8)
    split = fresh_split(base)
    candidates = {
        "selected cell: 85-90/rem8": base,
        "neighbor: 85-90/rem7": cell(markets, 0.85, 0.90, 7),
        "neighbor: 85-90/rem6": cell(markets, 0.85, 0.90, 6),
        "wider: 85-93/rem8": cell(markets, 0.85, 0.93, 8),
        "window: first 85-90/rem8-6": first_in_window(markets, 0.85, 0.90, (8, 7, 6)),
        "unbroken only: 85-90/rem8": cell(markets, 0.85, 0.90, 8, True),
    }
    for label, trades in candidates.items():
        report(label, trades, split)

    print("\n=== exits on selected 85-90/rem8 cell ===")
    exit_rules = {
        "hold": base,
        "take +4c, no stop": managed_exit(base, market_map, 0.04, None),
        "take +6c, no stop": managed_exit(base, market_map, 0.06, None),
        "stop -10c, no take": managed_exit(base, market_map, None, 0.10),
        "take +4c / stop -10c": managed_exit(base, market_map, 0.04, 0.10),
    }
    for label, trades in exit_rules.items():
        report(label, trades, split)

    multiplicity(markets)
    book_depth(markets)
    print(
        f"\nfresh split: {time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(split))}; "
        "the entire fresh period remains discovery-contaminated for this cell."
    )


if __name__ == "__main__":
    main()
