#!/usr/bin/env python3
"""Reproducible audit of the flipped-then-stabilized favorite hypothesis."""

from __future__ import annotations

import math
import time
from collections import defaultdict

import backtest as bt


OOS_TS = 1783468800
HISTORY_DAYS = 69.2


def _cluster_t(trades: list[dict], key) -> tuple[float, int]:
    mean = sum(trade["pnl"] for trade in trades) / len(trades)
    groups: dict[object, list[float]] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade["pnl"])
    group_count = len(groups)
    variance = (
        (group_count / (group_count - 1))
        * sum(
            (sum(values) - len(values) * mean) ** 2
            for values in groups.values()
        )
        / (len(trades) ** 2)
    )
    return mean / math.sqrt(variance), group_count


def run(
    markets: list[dict],
    stability_minutes: int,
    band_low: float,
    band_high: float,
    *,
    spread_epsilon: float = 1e-9,
) -> list[dict]:
    trades = []
    for market in markets:
        candles = market["by_rem"]
        favorites = {}
        for remaining in range(14, 0, -1):
            quote = bt.fav_quotes(candles.get(remaining) or {})
            if quote:
                favorites[remaining] = quote

        for remaining in range(8, 3, -1):
            if remaining not in favorites:
                continue
            side, favorite_bid, favorite_ask, favorite_mid = favorites[remaining]
            if favorite_ask - favorite_bid > 0.01 + spread_epsilon:
                continue
            if not band_low <= favorite_mid < band_high or favorite_ask > 0.99:
                continue

            flips = 0
            previous_side = None
            for earlier_remaining in range(14, remaining - 1, -1):
                if earlier_remaining not in favorites:
                    continue
                observed_side = favorites[earlier_remaining][0]
                if previous_side and observed_side != previous_side:
                    flips += 1
                previous_side = observed_side
            if flips == 0:
                continue

            held = 0
            for earlier_remaining in range(remaining, 15):
                if (
                    earlier_remaining in favorites
                    and favorites[earlier_remaining][0] == side
                ):
                    held += 1
                else:
                    break
            if held < stability_minutes:
                continue

            # The decision close is observable only after the next minute begins.
            next_candle = candles.get(remaining - 1)
            if next_candle is None:
                continue
            next_ask = bt.fav_ask_open(next_candle, side)
            if next_ask is None:
                continue
            entry = max(favorite_ask, next_ask)
            if entry > 0.99:
                continue

            win = market["result"] == side
            pnl = (1 - entry if win else -entry) - bt.fee(entry)
            trades.append(
                {
                    "ticker": market["ticker"],
                    "series": market["series"],
                    "ts": market["close_ts"],
                    "entry": entry,
                    "win": win,
                    "pnl": pnl,
                    "oos": market["close_ts"] >= OOS_TS,
                }
            )
            break
    return sorted(trades, key=lambda trade: trade["ts"])


def _mean_cents(trades: list[dict]) -> float:
    return 100 * sum(trade["pnl"] for trade in trades) / len(trades)


def report(trades: list[dict], stability_minutes: int) -> None:
    mean = sum(trade["pnl"] for trade in trades) / len(trades)
    standard_deviation = math.sqrt(
        sum((trade["pnl"] - mean) ** 2 for trade in trades)
        / (len(trades) - 1)
    )
    in_sample = [trade for trade in trades if not trade["oos"]]
    out_of_sample = [trade for trade in trades if trade["oos"]]
    interval_t, interval_count = _cluster_t(trades, lambda trade: trade["ts"])
    day_t, day_count = _cluster_t(
        trades,
        lambda trade: time.strftime("%Y-%m-%d", time.gmtime(trade["ts"])),
    )

    print(
        f"K={stability_minutes} n={len(trades)} "
        f"n/day={len(trades) / HISTORY_DAYS:.1f} "
        f"WR={sum(trade['win'] for trade in trades) / len(trades):.2%} "
        f"EV={100 * mean:+.3f}c "
        f"t_naive={mean / (standard_deviation / math.sqrt(len(trades))):+.2f} "
        f"t_interval={interval_t:+.2f} ({interval_count} intervals) "
        f"t_day={day_t:+.2f} ({day_count} days) "
        f"IS={_mean_cents(in_sample):+.3f}c "
        f"OOS={_mean_cents(out_of_sample):+.3f}c"
    )


def main() -> None:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))

    print("Flipped-then-stabilized favorites, 93-99c, taker fee included")
    for stability_minutes in (3, 4, 5, 6, 8):
        report(run(markets, stability_minutes, 0.93, 0.99), stability_minutes)

    print("\nPost-hoc candidate: 93-95c signal band (fresh holdout required)")
    for stability_minutes in (3, 4, 5, 6, 8):
        report(run(markets, stability_minutes, 0.93, 0.95), stability_minutes)

    selected = run(markets, 5, 0.93, 0.99)
    print("\nK=5 by asset")
    for series in bt.SERIES:
        asset_trades = [trade for trade in selected if trade["series"] == series]
        print(
            f"{series}: n={len(asset_trades)} "
            f"EV={_mean_cents(asset_trades):+.3f}c"
        )

    mean = sum(trade["pnl"] for trade in selected) / len(selected)
    print("\nK=5 execution sensitivity")
    for slippage_cents in (0.0, 0.1, 0.25, 0.5):
        adjusted_mean = mean - slippage_cents / 100
        print(
            f"extra slippage={slippage_cents:.2f}c "
            f"EV={adjusted_mean * 100:+.3f}c "
            f"expected/day@1={adjusted_mean * len(selected) / HISTORY_DAYS:+.3f} dollars"
        )


if __name__ == "__main__":
    main()
