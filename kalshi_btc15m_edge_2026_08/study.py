#!/usr/bin/env python3
"""BTC15M edge study with chronological validation and executable prices.

This script deliberately tests a small, pre-specified hypothesis set:

1. the parked unbroken-favorite rule as a control;
2. a path-risk filter on that control;
3. an expanding, cell-calibrated favorite rule;
4. a volatility-scaled settlement equation using the BTC perpetual as a BRTI
   proxy;
5. a wobble-recovery rule; and
6. a second-level order-book imbalance scalp.

No result from this script changes a live bot. One position per market is
enforced within every strategy. Entries cross the displayed ask, and candle
strategies use the worse of the signal-close ask and the following minute-open
ask whenever that following quote exists.
"""

from __future__ import annotations

import bisect
import calendar
import json
import math
import os
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass


ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
OLD = os.path.join(REPO, "kalshi_crypto15m_2026_07", "data")
PERP_OLD = os.path.join(REPO, "kalshi_perps_2026_07", "data")
BOOK_PATH = os.path.join(REPO, "kalshi_unbroken_bot", "state", "book_capture.jsonl")
FRESH_START = calendar.timegm(time.strptime("2026-07-27T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
VALIDATION_START = calendar.timegm(time.strptime("2026-06-15T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
EPSILON = 1e-9


@dataclass(frozen=True)
class Market:
    ticker: str
    close_ts: int
    result: str
    strike: float | None
    expiration: float | None
    volume: float
    by_rem: dict[int, dict]


@dataclass(frozen=True)
class Trade:
    strategy: str
    ticker: str
    close_ts: int
    side: str
    entry: float
    pnl: float
    signal_rem: int
    prediction: float | None = None
    raw_edge: float | None = None


def parse_time(value: str) -> int:
    value = value.split(".")[0].replace("Z", "")
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%S"))


def number(value) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def fee(price: float) -> float:
    return 0.07 * price * (1 - price)


def settlement_pnl(entry: float, won: bool) -> float:
    return (1 - entry if won else -entry) - fee(entry)


def read_jsonl(path: str) -> list[dict]:
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_markets() -> list[Market]:
    metadata = {}
    candles = {}
    for path in (
        os.path.join(OLD, "markets_KXBTC15M.jsonl"),
        os.path.join(ROOT, "data", "markets_fresh.jsonl"),
    ):
        for row in read_jsonl(path):
            if row.get("result") in {"yes", "no"}:
                metadata[row["ticker"]] = row
    for path in (
        os.path.join(OLD, "candles_KXBTC15M.jsonl"),
        os.path.join(ROOT, "data", "candles_fresh.jsonl"),
    ):
        for row in read_jsonl(path):
            if row.get("candles"):
                candles[row["ticker"]] = row["candles"]

    output = []
    for ticker, market in metadata.items():
        rows = candles.get(ticker)
        if not rows:
            continue
        close_ts = parse_time(market["close_time"])
        by_rem = {}
        for candle in rows:
            try:
                remainder = (close_ts - int(candle["t"])) // 60
            except (KeyError, TypeError, ValueError):
                continue
            by_rem[remainder] = candle
        output.append(
            Market(
                ticker=ticker,
                close_ts=close_ts,
                result=market["result"],
                strike=number(market.get("floor_strike")),
                expiration=number(
                    market.get("expiration_value")
                    or market.get("settlement_value_dollars")
                ),
                volume=number(market.get("volume_fp")) or 0.0,
                by_rem=by_rem,
            )
        )
    return sorted(output, key=lambda row: row.close_ts)


def yes_quote(candle: dict, field: str = "close") -> tuple[float, float] | None:
    bid_key = "bc" if field == "close" else "bo"
    ask_key = "ac" if field == "close" else "ao"
    bid = number(candle.get(bid_key))
    ask = number(candle.get(ask_key))
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return bid, ask


def favorite_quote(candle: dict, field: str = "close") -> tuple[str, float, float, float] | None:
    quote = yes_quote(candle, field)
    if quote is None:
        return None
    bid, ask = quote
    mid = (bid + ask) / 2
    if mid >= 0.5:
        return "yes", bid, ask, mid
    return "no", 1 - ask, 1 - bid, 1 - mid


def side_quote(candle: dict, side: str, field: str = "close") -> tuple[float, float] | None:
    quote = yes_quote(candle, field)
    if quote is None:
        return None
    bid, ask = quote
    return (bid, ask) if side == "yes" else (1 - ask, 1 - bid)


def executable_entry(market: Market, remainder: int, side: str) -> float | None:
    signal = side_quote(market.by_rem.get(remainder) or {}, side)
    if signal is None:
        return None
    entry = signal[1]
    following = side_quote(market.by_rem.get(remainder - 1) or {}, side, "open")
    if following is not None:
        entry = max(entry, following[1])
    return entry if 0 < entry < 1 else None


def path_state(market: Market, remainder: int, side: str) -> dict:
    sides = []
    mids = []
    by_remainder = {}
    for rem in range(14, remainder - 1, -1):
        quote = favorite_quote(market.by_rem.get(rem) or {})
        if quote is None:
            continue
        current_side, _, _, favorite_mid = quote
        sides.append(current_side)
        side_mid = favorite_mid if current_side == side else 1 - favorite_mid
        mids.append(side_mid)
        by_remainder[rem] = side_mid
    flips = sum(left != right for left, right in zip(sides, sides[1:]))
    current = by_remainder.get(remainder)
    prior_3 = by_remainder.get(remainder + 3)
    momentum_3 = None if current is None or prior_3 is None else current - prior_3
    one_minute_changes = [right - left for left, right in zip(mids, mids[1:])]
    return {
        "observations": len(sides),
        "flips": flips,
        "momentum_3": momentum_3,
        "largest_drop": min(one_minute_changes, default=0.0),
        "max_mid": max(mids, default=0.0),
        "all_same": bool(sides) and all(value == side for value in sides),
    }


def first_unbroken_trade(
    market: Market,
    label: str,
    band_low: float,
    band_high: float,
    path_filter: bool = False,
) -> Trade | None:
    for remainder in range(8, 3, -1):
        quote = favorite_quote(market.by_rem.get(remainder) or {})
        if quote is None:
            continue
        side, bid, ask, mid = quote
        if not band_low <= mid < band_high or ask - bid > 0.01 + EPSILON:
            continue
        path = path_state(market, remainder, side)
        if path["observations"] < 5 or path["flips"] or not path["all_same"]:
            continue
        if path_filter:
            if path["momentum_3"] is None or path["momentum_3"] < 0:
                continue
            if path["largest_drop"] < -0.03 - EPSILON:
                continue
        entry = executable_entry(market, remainder, side)
        if entry is None or entry > 0.99:
            continue
        won = market.result == side
        return Trade(
            label, market.ticker, market.close_ts, side, entry,
            settlement_pnl(entry, won), remainder,
        )
    return None


def unbroken_strategies(markets: list[Market]) -> dict[str, list[Trade]]:
    definitions = (
        ("control_unbroken_90_99", 0.90, 0.99, False),
        ("parked_unbroken_93_99", 0.93, 0.99, False),
        ("path_filtered_90_99", 0.90, 0.99, True),
    )
    output = {}
    for label, low, high, filtered in definitions:
        output[label] = [
            trade
            for market in markets
            if (trade := first_unbroken_trade(market, label, low, high, filtered))
        ]
    return output


def wobble_recovery(markets: list[Market]) -> list[Trade]:
    """Buy a still-leading favorite after it fell from >=95c into 82-90c."""
    trades = []
    for market in markets:
        for remainder in range(8, 3, -1):
            quote = favorite_quote(market.by_rem.get(remainder) or {})
            if quote is None:
                continue
            side, bid, ask, mid = quote
            if not 0.82 <= mid < 0.90 or ask - bid > 0.01 + EPSILON:
                continue
            path = path_state(market, remainder, side)
            if path["max_mid"] < 0.95 or path["flips"] or path["observations"] < 5:
                continue
            if path["momentum_3"] is None or path["momentum_3"] >= -0.03:
                continue
            entry = executable_entry(market, remainder, side)
            if entry is None:
                continue
            trades.append(
                Trade(
                    "wobble_recovery", market.ticker, market.close_ts, side,
                    entry, settlement_pnl(entry, market.result == side), remainder,
                )
            )
            break
    return trades


PRICE_BUCKETS = (0.70, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.99, 1.01)


def bucket(value: float, boundaries: tuple[float, ...]) -> tuple[float, float] | None:
    for low, high in zip(boundaries, boundaries[1:]):
        if low <= value < high:
            return low, high
    return None


def calibration_row(market: Market, remainder: int = 6) -> dict | None:
    quote = favorite_quote(market.by_rem.get(remainder) or {})
    if quote is None:
        return None
    side, bid, ask, mid = quote
    price_bucket = bucket(mid, PRICE_BUCKETS)
    if price_bucket is None or ask - bid > 0.01 + EPSILON:
        return None
    path = path_state(market, remainder, side)
    if path["flips"] or path["observations"] < 5 or not path["all_same"]:
        return None
    momentum = path["momentum_3"]
    if momentum is None:
        return None
    momentum_bucket = "down" if momentum < -0.02 else "up" if momentum > 0.02 else "flat"
    entry = executable_entry(market, remainder, side)
    if entry is None or entry > 0.995:
        return None
    return {
        "ticker": market.ticker,
        "ts": market.close_ts,
        "side": side,
        "entry": entry,
        "won": market.result == side,
        "cell": (*price_bucket, momentum_bucket),
        "rem": remainder,
    }


def wilson_lower(wins: int, count: int, z: float = 1.2815515655) -> float:
    if count == 0:
        return 0.0
    rate = wins / count
    denominator = 1 + z * z / count
    center = (rate + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count)) / denominator
    return center - radius


def calibrated_policy(markets: list[Market]) -> list[Trade]:
    """Walk-forward cell calibration with no parameter chosen on test data."""
    rows = [row for market in markets if (row := calibration_row(market))]
    history = defaultdict(list)
    trades = []
    for row in rows:
        prior = [item for item in history[row["cell"]] if item["ts"] < row["ts"] - 60]
        if len(prior) >= 120 and row["ts"] >= VALIDATION_START:
            wins = sum(item["won"] for item in prior)
            estimate = (wins + 2 * statistics.mean(item["entry"] for item in prior)) / (len(prior) + 2)
            lower = wilson_lower(wins, len(prior))
            break_even = row["entry"] + fee(row["entry"]) + 0.0025
            if lower > break_even:
                trades.append(
                    Trade(
                        "walk_forward_calibration", row["ticker"], row["ts"],
                        row["side"], row["entry"],
                        settlement_pnl(row["entry"], row["won"]), row["rem"],
                        estimate, estimate - row["entry"] - fee(row["entry"]),
                    )
                )
        history[row["cell"]].append(row)
    return trades


def load_perp() -> dict[int, float]:
    output = {}
    old_path = os.path.join(PERP_OLD, "perp_candles_KXBTCPERP.jsonl")
    for row in read_jsonl(old_path):
        for candle in row.get("c") or []:
            bid, ask = number(candle.get("b")), number(candle.get("a"))
            if bid is not None and ask is not None and 0 < bid <= ask:
                output[int(candle["t"])] = 10_000 * (bid + ask) / 2
    fresh_path = os.path.join(ROOT, "data", "perp_fresh.jsonl")
    for row in read_jsonl(fresh_path):
        for candle in row.get("candles") or []:
            bid, ask = number(candle.get("bid")), number(candle.get("ask"))
            if bid is not None and ask is not None and 0 < bid <= ask:
                output[int(candle["t"])] = 10_000 * (bid + ask) / 2
    return output


def previous_value(values: dict[int, float], timestamp: int, tolerance: int = 180) -> float | None:
    for delay in range(0, tolerance + 1, 60):
        if timestamp - delay in values:
            return values[timestamp - delay]
    return None


def robust_sigma(values: dict[int, float], timestamp: int) -> float | None:
    prices = [
        previous_value(values, sample, 0)
        for sample in range(timestamp - 1800, timestamp + 1, 60)
    ]
    prices = [value for value in prices if value is not None]
    returns = [math.log(right / left) for left, right in zip(prices, prices[1:]) if left > 0]
    if len(returns) < 20:
        return None
    center = statistics.median(returns)
    mad = statistics.median(abs(value - center) for value in returns)
    sigma = 1.4826 * mad
    return sigma if sigma > 1e-7 else None


def settlement_rows(markets: list[Market], perps: dict[int, float], remainder: int) -> list[dict]:
    output = []
    for market in markets:
        if market.strike is None or market.expiration is None:
            continue
        decision_ts = market.close_ts - remainder * 60
        index = previous_value(perps, decision_ts)
        sigma = robust_sigma(perps, decision_ts)
        candle = market.by_rem.get(remainder) or {}
        quote = yes_quote(candle)
        if index is None or sigma is None or quote is None:
            continue
        yes_bid, yes_ask = quote
        if yes_ask - yes_bid > 0.02 + EPSILON:
            continue
        yes_entry = executable_entry(market, remainder, "yes")
        no_entry = executable_entry(market, remainder, "no")
        if yes_entry is None or no_entry is None:
            continue
        scale = sigma * math.sqrt(remainder + 0.5)
        standardized_future = math.log(market.expiration / index) / scale
        threshold = math.log(market.strike / index) / scale
        output.append(
            {
                "market": market,
                "ts": market.close_ts,
                "z_future": standardized_future,
                "threshold": threshold,
                "yes_entry": yes_entry,
                "no_entry": no_entry,
                "yes_mid": (yes_bid + yes_ask) / 2,
                "result_yes": market.result == "yes",
                "rem": remainder,
            }
        )
    return output


def settlement_equation(markets: list[Market], remainder: int) -> tuple[list[Trade], dict]:
    rows = settlement_rows(markets, load_perp(), remainder)
    history = []
    trades = []
    brier_model = []
    brier_market = []
    for row in rows:
        prior = [item for item in history if item["ts"] >= row["ts"] - 28 * 86400]
        if len(prior) >= 800 and row["ts"] >= VALIDATION_START:
            residuals = sorted(item["z_future"] for item in prior)
            count_yes = len(residuals) - bisect.bisect_left(residuals, row["threshold"])
            p_yes = (count_yes + 1) / (len(residuals) + 2)
            actual = 1.0 if row["result_yes"] else 0.0
            brier_model.append((row["ts"], (p_yes - actual) ** 2))
            brier_market.append((row["ts"], (row["yes_mid"] - actual) ** 2))
            candidates = [
                ("yes", p_yes, row["yes_entry"]),
                ("no", 1 - p_yes, row["no_entry"]),
            ]
            side, probability, entry = max(
                candidates, key=lambda item: item[1] - item[2] - fee(item[2])
            )
            edge = probability - entry - fee(entry)
            if edge > 0.01 and entry <= 0.99:
                won = (row["result_yes"] and side == "yes") or (not row["result_yes"] and side == "no")
                trades.append(
                    Trade(
                        f"settlement_equation_rem{remainder}", row["market"].ticker,
                        row["ts"], side, entry, settlement_pnl(entry, won),
                        remainder, probability, edge,
                    )
                )
        history.append(row)
    metrics = {"model": brier_model, "market": brier_market, "rows": len(rows)}
    return trades, metrics


def book_quote(row: dict) -> dict | None:
    try:
        yes = [(float(price), float(size)) for price, size in row.get("yes") or []]
        no = [(float(price), float(size)) for price, size in row.get("no") or []]
    except (TypeError, ValueError):
        return None
    if not yes or not no:
        return None
    yes_bid, bid_size = max(yes, key=lambda item: item[0])
    no_bid, ask_size = max(no, key=lambda item: item[0])
    yes_ask = 1 - no_bid
    if not 0 < yes_bid <= yes_ask < 1:
        return None
    denominator = bid_size + ask_size
    if denominator <= 0:
        return None
    imbalance = bid_size / denominator
    microprice = (yes_ask * bid_size + yes_bid * ask_size) / denominator
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "imbalance": imbalance,
        "microprice": microprice,
        "mid": (yes_bid + yes_ask) / 2,
    }


def load_books() -> dict[str, list[dict]]:
    output = defaultdict(list)
    for row in read_jsonl(BOOK_PATH):
        if "KXBTC15M" in row.get("ticker", ""):
            quote = book_quote(row)
            if quote:
                output[row["ticker"]].append({"ts": int(row["ts"]), **quote})
    for rows in output.values():
        rows.sort(key=lambda row: row["ts"])
    return output


def first_snapshot(rows: list[dict], target: int, max_delay: int = 40) -> dict | None:
    timestamps = [row["ts"] for row in rows]
    index = bisect.bisect_left(timestamps, target)
    if index >= len(rows) or rows[index]["ts"] > target + max_delay:
        return None
    return rows[index]


def imbalance_scalps(markets: list[Market], horizon: int) -> list[Trade]:
    """Standard top-of-book imbalance signal, crossed in and crossed out."""
    market_by_ticker = {market.ticker: market for market in markets}
    trades = []
    for ticker, rows in load_books().items():
        market = market_by_ticker.get(ticker)
        if market is None:
            continue
        for remainder in (8, 6, 4):
            signal = first_snapshot(rows, market.close_ts - remainder * 60)
            if signal is None:
                continue
            spread = signal["yes_ask"] - signal["yes_bid"]
            if spread > 0.01 + EPSILON or not 0.20 <= signal["mid"] <= 0.80:
                continue
            if signal["imbalance"] >= 0.70:
                side = "yes"
                entry = signal["yes_ask"]
                displayed = signal["ask_size"]
            elif signal["imbalance"] <= 0.30:
                side = "no"
                entry = 1 - signal["yes_bid"]
                displayed = signal["bid_size"]
            else:
                continue
            if displayed < 10:
                continue
            exit_quote = first_snapshot(rows, signal["ts"] + horizon)
            if exit_quote is None:
                continue
            exit_bid = exit_quote["yes_bid"] if side == "yes" else 1 - exit_quote["yes_ask"]
            pnl = exit_bid - entry - fee(entry) - fee(exit_bid)
            trades.append(
                Trade(
                    f"book_imbalance_{horizon}s", ticker, market.close_ts,
                    side, entry, pnl, remainder,
                )
            )
            break
    return sorted(trades, key=lambda trade: trade.close_ts)


def max_drawdown(trades: list[Trade], stress_cents: float = 0.0) -> float:
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda row: row.close_ts):
        equity += trade.pnl - stress_cents / 100
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def bootstrap_days(trades: list[Trade], stress_cents: float = 0.0, iterations: int = 10_000) -> dict:
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade.close_ts // 86400].append(trade.pnl - stress_cents / 100)
    days = sorted(grouped)
    if not days:
        return {"p_positive": 0.0, "p05": float("nan"), "days": 0}
    randomizer = random.Random(20260807 + int(stress_cents * 100))
    means = []
    for _ in range(iterations):
        pnl = count = 0
        for day in (randomizer.choice(days) for _ in days):
            pnl += sum(grouped[day])
            count += len(grouped[day])
        means.append(100 * pnl / count)
    means.sort()
    return {
        "p_positive": sum(value > 0 for value in means) / len(means),
        "p05": means[int(0.05 * (len(means) - 1))],
        "days": len(days),
    }


def segment(trades: list[Trade], start: int | None = None, end: int | None = None) -> list[Trade]:
    return [
        trade for trade in trades
        if (start is None or trade.close_ts >= start) and (end is None or trade.close_ts < end)
    ]


def summarize(label: str, trades: list[Trade]) -> str:
    if not trades:
        return f"{label:34} n=0"
    pnl = [trade.pnl for trade in trades]
    raw = statistics.mean(pnl) * 100
    stressed_025 = raw - 0.25
    stressed_050 = raw - 0.50
    wins = sum(value > 0 for value in pnl)
    bootstrap = bootstrap_days(trades, 0.25)
    duration = max((max(t.close_ts for t in trades) - min(t.close_ts for t in trades)) / 86400, 1)
    return (
        f"{label:34} n={len(trades):4d} win={wins/len(trades):6.1%} "
        f"raw={raw:+7.3f}c stress25={stressed_025:+7.3f}c "
        f"stress50={stressed_050:+7.3f}c tr/day={len(trades)/duration:5.1f} "
        f"DD25=${max_drawdown(trades, 0.25):+6.2f} "
        f"bootP={bootstrap['p_positive']:5.1%} p05={bootstrap['p05']:+6.3f}c"
    )


def print_strategy(label: str, trades: list[Trade]) -> None:
    print(f"\n=== {label} ===")
    print(summarize("all", trades))
    print(summarize("pre-validation", segment(trades, end=VALIDATION_START)))
    print(summarize("validation-to-fresh", segment(trades, VALIDATION_START, FRESH_START)))
    print(summarize("fresh holdout", segment(trades, FRESH_START)))
    if trades:
        by_entry = (
            ("entry<90", [t for t in trades if t.entry < 0.90]),
            ("entry90-95", [t for t in trades if 0.90 <= t.entry < 0.95]),
            ("entry95+", [t for t in trades if t.entry >= 0.95]),
        )
        for bucket_label, rows in by_entry:
            if rows:
                print(summarize(bucket_label, rows))


def brier_summary(metrics: dict) -> str:
    model = metrics["model"]
    market = dict(metrics["market"])
    lines = []
    for label, start, end in (
        ("validation", VALIDATION_START, FRESH_START),
        ("fresh", FRESH_START, None),
    ):
        model_values = [value for ts, value in model if ts >= start and (end is None or ts < end)]
        market_values = [value for ts, value in market.items() if ts >= start and (end is None or ts < end)]
        if model_values and market_values:
            lines.append(
                f"{label}: model={statistics.mean(model_values):.5f}, "
                f"market={statistics.mean(market_values):.5f}, "
                f"delta={statistics.mean(model_values)-statistics.mean(market_values):+.5f}"
            )
    return "; ".join(lines)


def main() -> None:
    markets = load_markets()
    print(
        f"loaded {len(markets)} BTC markets: "
        f"{time.strftime('%Y-%m-%d', time.gmtime(markets[0].close_ts))} to "
        f"{time.strftime('%Y-%m-%d', time.gmtime(markets[-1].close_ts))}"
    )
    print(f"fresh markets={sum(m.close_ts >= FRESH_START for m in markets)}")

    strategies = unbroken_strategies(markets)
    strategies["walk_forward_calibration"] = calibrated_policy(markets)
    strategies["wobble_recovery"] = wobble_recovery(markets)

    for remainder in (6, 2):
        trades, metrics = settlement_equation(markets, remainder)
        strategies[f"settlement_equation_rem{remainder}"] = trades
        print(f"\nsettlement equation rem={remainder} Brier: {brier_summary(metrics)}")

    for horizon in (30, 60, 120):
        strategies[f"book_imbalance_{horizon}s"] = imbalance_scalps(markets, horizon)

    for label, trades in strategies.items():
        print_strategy(label, trades)

    print("\n=== promotion gates ===")
    for label, trades in strategies.items():
        fresh = segment(trades, FRESH_START)
        validation = segment(trades, VALIDATION_START, FRESH_START)
        fresh_bootstrap = bootstrap_days(fresh, 0.25)
        validation_ev = statistics.mean(t.pnl for t in validation) - 0.0025 if validation else -1
        fresh_ev = statistics.mean(t.pnl for t in fresh) - 0.0025 if fresh else -1
        passed = (
            len(validation) >= 200
            and len(fresh) >= 100
            and validation_ev > 0
            and fresh_ev > 0
            and fresh_bootstrap["p_positive"] >= 0.95
            and fresh_bootstrap["p05"] > 0
        )
        print(
            f"{label:34} {'PASS' if passed else 'FAIL'} "
            f"validation_n={len(validation)} fresh_n={len(fresh)} "
            f"validation25={100*validation_ev:+.3f}c fresh25={100*fresh_ev:+.3f}c "
            f"freshP={fresh_bootstrap['p_positive']:.1%} p05={fresh_bootstrap['p05']:+.3f}c"
        )


if __name__ == "__main__":
    main()
