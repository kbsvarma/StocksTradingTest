#!/usr/bin/env python3
"""Causal, event-deduplicated study of Kalshi hourly ladders."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SERIES = (
    "KXBTCD",
    "KXETHD",
    "KXSOLD",
    "KXXRPD",
    "KXBNBD",
    "KXDOGED",
    "KXGOLDH",
    "KXSILVERH",
    "KXWTIH",
)
REMAINING = (20, 15, 10, 7, 5, 3)
BANDS = ((0.80, 0.90), (0.90, 0.93), (0.93, 0.95), (0.95, 0.97), (0.97, 0.991))
HOLDS = (1, 3, 5)
SPREADS = (0.01, 0.02, 0.03)
MECHANISMS = (
    "stable_favorite",
    "unbroken_favorite",
    "stabilized_flip",
    "strengthening_3c",
    "strengthening_5c",
)
SCALP_TARGETS = (0.03, 0.05, 0.08)
SCALP_STOPS = (0.05, 0.10, 0.20)


def number(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def side_quote(candle: dict, side: str, phase: str = "close") -> tuple[float, float] | None:
    suffix = "c" if phase == "close" else "o"
    yes_bid = number(candle.get(f"b{suffix}"))
    yes_ask = number(candle.get(f"a{suffix}"))
    if yes_bid is None or yes_ask is None:
        return None
    if side == "yes":
        return yes_bid, yes_ask
    return 1.0 - yes_ask, 1.0 - yes_bid


def favorite_quote(candle: dict, phase: str = "close") -> tuple[str, float, float] | None:
    yes = side_quote(candle, "yes", phase)
    if yes is None:
        return None
    yes_bid, yes_ask = yes
    if (yes_bid + yes_ask) / 2.0 >= 0.5:
        return "yes", yes_bid, yes_ask
    return "no", 1.0 - yes_ask, 1.0 - yes_bid


def load_series(data_dir: Path, series: str) -> tuple[dict[str, list[dict]], dict[str, dict[int, dict]], dict]:
    markets = load_jsonl(data_dir / f"markets_{series}.jsonl")
    candle_rows = load_jsonl(data_dir / f"candles_{series}.jsonl")
    candles = {
        row["ticker"]: {int(candle["t"]): candle for candle in row.get("candles", [])}
        for row in candle_rows
    }
    events: dict[str, list[dict]] = defaultdict(list)
    duplicate_tickers = 0
    seen: set[str] = set()
    for market in markets:
        ticker = market["ticker"]
        if ticker in seen:
            duplicate_tickers += 1
            continue
        seen.add(ticker)
        events[market["event_ticker"]].append(market)
    close_times = [parse_ts(market["close_time"]) for market in markets]
    quality = {
        "markets": len(markets),
        "events": len(events),
        "candle_markets": len(candles),
        "duplicate_tickers": duplicate_tickers,
        "settled_labeled_markets": sum(market.get("result") in ("yes", "no") for market in markets),
        "first_close_utc": datetime.fromtimestamp(min(close_times), timezone.utc).isoformat() if close_times else None,
        "last_close_utc": datetime.fromtimestamp(max(close_times), timezone.utc).isoformat() if close_times else None,
    }
    return events, candles, quality


def candidate_snapshot(market: dict, candles: dict[int, dict], remaining: int) -> dict | None:
    if market.get("result") not in ("yes", "no") or not candles:
        return None
    close_ts = parse_ts(market["close_time"])
    signal_ts = close_ts - remaining * 60
    signal = candles.get(signal_ts)
    signal_quote = favorite_quote(signal) if signal else None
    if signal_quote is None:
        return None
    side, bid, ask = signal_quote
    spread = ask - bid
    if spread < -1e-9:
        return None

    next_candle = candles.get(signal_ts + 60)
    next_quote = side_quote(next_candle, side, "open") if next_candle else None
    if next_quote is None:
        return None
    entry = max(ask, next_quote[1])
    if not 0.0 < entry <= 0.99:
        return None

    observed = []
    prior_volume = 0.0
    for candle_ts in sorted(candles):
        if candle_ts > signal_ts:
            break
        if candle_ts < close_ts - 26 * 60:
            continue
        candle = candles[candle_ts]
        prior_volume += number(candle.get("v")) or 0.0
        quote = favorite_quote(candle)
        if quote is not None:
            observed.append((candle_ts, quote[0], quote[2]))
    if prior_volume <= 0.0:
        return None

    flags: dict[str, dict[int, bool]] = {mechanism: {} for mechanism in MECHANISMS}
    for hold in HOLDS:
        exact = []
        for offset in range(hold):
            candle = candles.get(signal_ts - offset * 60)
            quote = favorite_quote(candle) if candle else None
            if quote is None:
                exact = []
                break
            exact.append(quote)
        stable = len(exact) == hold and all(quote[0] == side for quote in exact)
        prior_sides = [item[1] for item in observed if item[0] < signal_ts - (hold - 1) * 60]
        had_opposite = any(prior_side != side for prior_side in prior_sides)
        unbroken = len(observed) >= hold and all(item[1] == side for item in observed)
        strengthening = ask - exact[-1][2] if stable and exact else -1.0
        flags["stable_favorite"][hold] = stable
        flags["unbroken_favorite"][hold] = unbroken
        flags["stabilized_flip"][hold] = stable and had_opposite
        flags["strengthening_3c"][hold] = stable and hold > 1 and strengthening >= 0.03 - 1e-9
        flags["strengthening_5c"][hold] = stable and hold > 1 and strengthening >= 0.05 - 1e-9

    exit_path = []
    for observed_ts in range(signal_ts + 60, close_ts - 60, 60):
        observed_candle = candles.get(observed_ts)
        following_candle = candles.get(observed_ts + 60)
        observed_quote = side_quote(observed_candle, side) if observed_candle else None
        following_quote = side_quote(following_candle, side, "open") if following_candle else None
        if observed_quote is None or following_quote is None:
            continue
        exit_path.append({"observed_bid": observed_quote[0], "exit_bid": following_quote[0]})

    won = market["result"] == side
    pnl = (1.0 - entry if won else -entry) - fee(entry)
    stressed_entry = min(1.0, entry + 0.01)
    stressed_pnl = (1.0 - stressed_entry if won else -stressed_entry) - fee(stressed_entry)
    return {
        "ticker": market["ticker"],
        "event_ticker": market["event_ticker"],
        "ts": signal_ts + 60,
        "close_ts": close_ts,
        "cluster": day(close_ts),
        "remaining": remaining,
        "side": side,
        "signal_bid": bid,
        "signal_ask": ask,
        "signal_mid": (bid + ask) / 2.0,
        "spread": spread,
        "entry": entry,
        "prior_volume": prior_volume,
        "signal_minute_volume": number(signal.get("v")) or 0.0,
        "signal_open_interest": number(signal.get("oi")) or 0.0,
        "win": won,
        "pnl": pnl,
        "stressed_pnl": stressed_pnl,
        "observed_count": len(observed),
        "ever_flipped": any(item[1] != side for item in observed),
        "flags": flags,
        "exit_path": exit_path,
    }


def make_config(mechanism: str, remaining: int, band: tuple[float, float], hold: int, spread: float) -> tuple:
    return mechanism, remaining, band[0], band[1], hold, spread


def build_base_trades(events: dict[str, list[dict]], candles: dict[str, dict[int, dict]]) -> tuple[dict[tuple, list[dict]], dict[tuple[str, int], list[dict]], dict]:
    trades: dict[tuple, list[dict]] = defaultdict(list)
    snapshots_by_event_remaining: dict[tuple[str, int], list[dict]] = {}
    snapshot_count = 0
    events_with_snapshot: set[str] = set()
    for event_ticker, markets in events.items():
        by_remaining: dict[int, list[dict]] = defaultdict(list)
        for market in markets:
            market_candles = candles.get(market["ticker"], {})
            for remaining in REMAINING:
                snapshot = candidate_snapshot(market, market_candles, remaining)
                if snapshot is not None:
                    by_remaining[remaining].append(snapshot)
                    snapshot_count += 1
                    events_with_snapshot.add(event_ticker)

        for remaining, snapshots in by_remaining.items():
            snapshots_by_event_remaining[(event_ticker, remaining)] = snapshots
            for mechanism in MECHANISMS:
                for hold in HOLDS:
                    mechanism_pool = [
                        snapshot
                        for snapshot in snapshots
                        if snapshot["flags"][mechanism][hold]
                    ]
                    if not mechanism_pool:
                        continue
                    for band in BANDS:
                        band_pool = [
                            snapshot
                            for snapshot in mechanism_pool
                            if band[0] <= snapshot["signal_ask"] < band[1]
                        ]
                        if not band_pool:
                            continue
                        for spread_cap in SPREADS:
                            eligible = [
                                snapshot
                                for snapshot in band_pool
                                if snapshot["spread"] <= spread_cap + 1e-9
                            ]
                            if not eligible:
                                continue
                            selected = min(
                                eligible,
                                key=lambda item: (
                                    item["entry"],
                                    item["spread"],
                                    -item["prior_volume"],
                                    item["ticker"],
                                ),
                            )
                            trades[make_config(mechanism, remaining, band, hold, spread_cap)].append(selected)
    return trades, snapshots_by_event_remaining, {
        "candidate_snapshots": snapshot_count,
        "events_with_causal_snapshot": len(events_with_snapshot),
        "base_configurations_with_trades": len(trades),
    }


def locked_late_rules(
    events: dict[str, list[dict]],
    snapshots_by_event_remaining: dict[tuple[str, int], list[dict]],
) -> dict[str, list[dict]]:
    """Replay the previously unfinished 5/7-minute BTC hypothesis literally."""

    by_rule: dict[str, list[dict]] = {"exact_7m": [], "exact_5m": [], "first_7m_then_5m": []}

    def selected(event_ticker: str, remaining: int) -> dict | None:
        eligible = [
            trade
            for trade in snapshots_by_event_remaining.get((event_ticker, remaining), [])
            if 0.93 <= trade["signal_mid"] < 0.99
            and trade["signal_ask"] <= 0.99
            and trade["spread"] <= 0.01 + 1e-9
            and trade["observed_count"] >= 10
            and not trade["ever_flipped"]
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (item["entry"], item["spread"], -item["prior_volume"], item["ticker"]),
        )

    for event_ticker in events:
        at_seven = selected(event_ticker, 7)
        at_five = selected(event_ticker, 5)
        if at_seven:
            by_rule["exact_7m"].append(at_seven)
        if at_five:
            by_rule["exact_5m"].append(at_five)
        first = at_seven or at_five
        if first:
            by_rule["first_7m_then_5m"].append(first)
    return by_rule


def max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 200
    epsilon = 3.0e-14
    floor = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < floor:
        d = floor
    d = 1.0 / d
    value = d
    for iteration in range(1, maximum_iterations + 1):
        doubled = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + doubled) * (a + doubled))
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        value *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / ((a + doubled) * (qap + doubled))
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        delta = d * c
        value *= delta
        if abs(delta - 1.0) < epsilon:
            return value
    return value


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_survival(t_statistic: float, degrees_freedom: int) -> float:
    if degrees_freedom <= 0:
        return 1.0
    x = degrees_freedom / (degrees_freedom + t_statistic * t_statistic)
    half_tail = 0.5 * _regularized_beta(x, degrees_freedom / 2.0, 0.5)
    return half_tail if t_statistic >= 0.0 else 1.0 - half_tail


def cluster_p_value(trades: list[dict], field: str) -> float:
    if not trades:
        return 1.0
    by_day: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_day[trade["cluster"]].append(trade[field])
    day_means = [sum(values) / len(values) for values in by_day.values()]
    groups = len(day_means)
    if groups < 2:
        return 1.0
    mean = sum(day_means) / groups
    variance = sum((value - mean) ** 2 for value in day_means) / (groups - 1)
    if variance <= 0.0:
        return 0.0 if mean > 0.0 else 1.0
    t_statistic = mean / math.sqrt(variance / groups)
    return student_t_survival(t_statistic, groups - 1)


def cluster_bootstrap_low(trades: list[dict], field: str, seed: int, iterations: int = 4000) -> float:
    by_day: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_day[trade["cluster"]].append(trade[field])
    days = sorted(by_day)
    if len(days) < 2:
        return 0.0
    randomizer = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [value for _ in days for value in by_day[randomizer.choice(days)]]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.05 * iterations)]


def summarize(trades: list[dict], field: str = "pnl", bootstrap: bool = False, seed: int = 20260811) -> dict:
    ordered = sorted(trades, key=lambda trade: (trade["ts"], trade["ticker"]))
    if not ordered:
        return {"n": 0, "mean_cents": 0.0, "days": 0, "cluster_p": 1.0}
    values = [trade[field] for trade in ordered]
    unique_days = {trade["cluster"] for trade in ordered}
    output = {
        "n": len(ordered),
        "wins": sum(value > 0.0 for value in values),
        "settlement_wins": sum(bool(trade["win"]) for trade in ordered),
        "mean_cents": 100.0 * sum(values) / len(values),
        "total_per_contract": sum(values),
        "average_entry": sum(trade["entry"] for trade in ordered) / len(ordered),
        "days": len(unique_days),
        "trades_per_active_day": len(ordered) / len(unique_days),
        "max_drawdown_per_contract": max_drawdown(values),
        "cluster_p": cluster_p_value(ordered, field),
    }
    if all("exit_reason" not in trade or trade["exit_reason"] == "settlement" for trade in ordered):
        entries = [
            min(1.0, trade["entry"] + (0.01 if field == "stressed_pnl" else 0.0))
            for trade in ordered
        ]
        required = sum(entry + fee(entry) for entry in entries) / len(entries)
        settlement_wins = sum(bool(trade["win"]) for trade in ordered)
        output["average_break_even_probability"] = required
        output["settlement_win_rate"] = settlement_wins / len(ordered)
        if settlement_wins == len(ordered):
            lower = 0.05 ** (1.0 / len(ordered))
            output["zero_loss_exact_95pct_lower_win_rate"] = lower
            output["zero_loss_uniform_prior_probability_edge_positive"] = 1.0 - required ** (len(ordered) + 1)
            output["zero_loss_tail_bound_supports_edge"] = lower > required
    if bootstrap:
        output["day_bootstrap_5pct_cents"] = 100.0 * cluster_bootstrap_low(ordered, field, seed)
    return output


def split_days(events: dict[str, list[dict]]) -> dict[str, set[str]]:
    days = sorted(
        {
            day(parse_ts(market["close_time"]))
            for markets in events.values()
            for market in markets[:1]
        }
    )
    first = max(1, int(len(days) * 0.50))
    second = max(first + 1, int(len(days) * 0.75))
    return {
        "discovery": set(days[:first]),
        "validation": set(days[first:second]),
        "test": set(days[second:]),
    }


def subset(trades: list[dict], allowed_days: set[str]) -> list[dict]:
    return [trade for trade in trades if trade["cluster"] in allowed_days]


def scalp_trade(trade: dict, target: float, stop: float) -> dict:
    output = dict(trade)
    exit_reason = "settlement"
    pnl = trade["pnl"]
    for path_point in trade["exit_path"]:
        observed_change = path_point["observed_bid"] - trade["entry"]
        if observed_change >= target - 1e-9:
            exit_price = path_point["exit_bid"]
            pnl = exit_price - trade["entry"] - fee(trade["entry"]) - fee(exit_price)
            exit_reason = "target"
            break
        if observed_change <= -stop + 1e-9:
            exit_price = path_point["exit_bid"]
            pnl = exit_price - trade["entry"] - fee(trade["entry"]) - fee(exit_price)
            exit_reason = "stop"
            break
    stressed_pnl = pnl - 0.01
    output.update({"pnl": pnl, "stressed_pnl": stressed_pnl, "exit_reason": exit_reason})
    return output


def bh_adjust(rows: list[dict]) -> None:
    indexed = sorted(enumerate(rows), key=lambda item: item[1]["discovery"]["cluster_p"])
    count = len(indexed)
    running = 1.0
    for reverse_rank, (index, row) in enumerate(reversed(indexed), start=1):
        rank = count - reverse_rank + 1
        adjusted = min(running, row["discovery"]["cluster_p"] * count / max(rank, 1))
        rows[index]["discovery_bh_q"] = adjusted
        running = adjusted


def serialize_config(config: tuple) -> dict:
    mechanism, remaining, low, high, hold, spread = config[:6]
    output = {
        "mechanism": mechanism,
        "remaining_minutes": remaining,
        "band": [low, high],
        "hold_minutes": hold,
        "spread_cap": spread,
    }
    if len(config) > 6:
        output.update({"target": config[6], "stop": config[7]})
    return output


def evaluate_series(data_dir: Path, series: str, write_ledgers: Path) -> dict:
    events, candles, quality = load_series(data_dir, series)
    splits = split_days(events)
    base_trades, snapshots_by_event_remaining, causal_quality = build_base_trades(events, candles)
    quality.update(causal_quality)
    rows = []
    trade_lookup: dict[tuple, list[dict]] = {}

    for config, trades in base_trades.items():
        trade_lookup[config] = trades
        rows.append(
            {
                "kind": "hold",
                "config_key": config,
                "config": serialize_config(config),
                "discovery": summarize(subset(trades, splits["discovery"])),
            }
        )
        if config[0] not in ("stable_favorite", "unbroken_favorite"):
            continue
        for target in SCALP_TARGETS:
            for stop in SCALP_STOPS:
                scalp_config = config + (target, stop)
                scalp_trades = [scalp_trade(trade, target, stop) for trade in trades]
                trade_lookup[scalp_config] = scalp_trades
                rows.append(
                    {
                        "kind": "scalp",
                        "config_key": scalp_config,
                        "config": serialize_config(scalp_config),
                        "discovery": summarize(subset(scalp_trades, splits["discovery"])),
                    }
                )

    bh_adjust(rows)
    eligible = [
        row
        for row in rows
        if row["discovery"]["n"] >= 100 and row["discovery"]["mean_cents"] > 0.0
    ]
    exploratory = [
        row
        for row in rows
        if row["discovery"]["n"] >= 30 and row["discovery"]["mean_cents"] > 0.0
    ]

    def rank(row: dict) -> tuple:
        return (
            row["discovery_bh_q"],
            -row["discovery"]["mean_cents"],
            -row["discovery"]["n"],
        )

    selected = min(eligible, key=rank) if eligible else (min(exploratory, key=rank) if exploratory else None)
    top_discovery = sorted(exploratory, key=rank)[:10]
    compact_top = [
        {
            "kind": row["kind"],
            "config": row["config"],
            "discovery": row["discovery"],
            "discovery_bh_q": row["discovery_bh_q"],
        }
        for row in top_discovery
    ]
    output = {
        "series": series,
        "data_quality": quality,
        "split_days": {name: len(values) for name, values in splits.items()},
        "tested_configurations": len(rows),
        "selected": None,
        "selected_by_kind": {},
        "top_discovery": compact_top,
        "locked_late_rule": {},
    }
    for name, locked_trades in locked_late_rules(events, snapshots_by_event_remaining).items():
        out_of_sample = subset(
            locked_trades,
            splits["validation"] | splits["test"],
        )
        output["locked_late_rule"][name] = {
            split_name: {
                "raw": summarize(subset(locked_trades, split_days_set), bootstrap=True, seed=501 + index),
                "stressed": summarize(
                    subset(locked_trades, split_days_set),
                    "stressed_pnl",
                    bootstrap=True,
                    seed=601 + index,
                ),
            }
            for index, (split_name, split_days_set) in enumerate(splits.items())
        }
        output["locked_late_rule"][name]["oos"] = {
            "raw": summarize(out_of_sample, bootstrap=True, seed=701),
            "stressed": summarize(out_of_sample, "stressed_pnl", bootstrap=True, seed=702),
        }
        output["locked_late_rule"][name]["full"] = {
            "raw": summarize(locked_trades, bootstrap=True, seed=703),
            "stressed": summarize(locked_trades, "stressed_pnl", bootstrap=True, seed=704),
        }
        locked_path = write_ledgers / f"locked_{series}_{name}.jsonl"
        with locked_path.open("w") as handle:
            for trade in sorted(locked_trades, key=lambda item: (item["ts"], item["ticker"])):
                clean = {key: value for key, value in trade.items() if key not in ("flags", "exit_path")}
                handle.write(json.dumps(clean, sort_keys=True) + "\n")

    def materialize(row: dict, seed_base: int) -> tuple[dict, list[dict]]:
        configured_trades = trade_lookup[row["config_key"]]
        discovery = subset(configured_trades, splits["discovery"])
        validation = subset(configured_trades, splits["validation"])
        test = subset(configured_trades, splits["test"])
        out_of_sample = validation + test
        detail = {
            "kind": row["kind"],
            "config": row["config"],
            "discovery_bh_q": row["discovery_bh_q"],
            "discovery": summarize(discovery, bootstrap=True, seed=seed_base + 1),
            "validation": summarize(validation, bootstrap=True, seed=seed_base + 2),
            "validation_stressed": summarize(validation, "stressed_pnl", bootstrap=True, seed=seed_base + 3),
            "test": summarize(test, bootstrap=True, seed=seed_base + 4),
            "test_stressed": summarize(test, "stressed_pnl", bootstrap=True, seed=seed_base + 5),
            "oos": summarize(out_of_sample, bootstrap=True, seed=seed_base + 6),
            "oos_stressed": summarize(out_of_sample, "stressed_pnl", bootstrap=True, seed=seed_base + 7),
        }
        checks = {
            "discovery_n_ge_100": detail["discovery"]["n"] >= 100,
            "discovery_bh_q_le_0_10": detail["discovery_bh_q"] <= 0.10,
            "oos_n_ge_50": detail["oos"]["n"] >= 50,
            "validation_positive": detail["validation"]["mean_cents"] > 0.0,
            "test_positive": detail["test"]["mean_cents"] > 0.0,
            "validation_stressed_positive": detail["validation_stressed"]["mean_cents"] > 0.0,
            "test_stressed_positive": detail["test_stressed"]["mean_cents"] > 0.0,
            "oos_stressed_bootstrap_5pct_positive": detail["oos_stressed"].get("day_bootstrap_5pct_cents", 0.0) > 0.0,
        }
        if row["kind"] == "hold" and detail["oos"].get("settlement_wins") == detail["oos"].get("n"):
            checks["oos_zero_loss_tail_bound_supports_raw_edge"] = bool(
                detail["oos"].get("zero_loss_tail_bound_supports_edge", False)
            )
            checks["oos_zero_loss_tail_bound_supports_stressed_edge"] = bool(
                detail["oos_stressed"].get("zero_loss_tail_bound_supports_edge", False)
            )
        detail["checks"] = checks
        detail["passed"] = all(checks.values())
        return detail, configured_trades

    for index, kind in enumerate(("hold", "scalp")):
        kind_eligible = [row for row in eligible if row["kind"] == kind]
        kind_exploratory = [row for row in exploratory if row["kind"] == kind]
        kind_selected = (
            min(kind_eligible, key=rank)
            if kind_eligible
            else (min(kind_exploratory, key=rank) if kind_exploratory else None)
        )
        if kind_selected is not None:
            output["selected_by_kind"][kind] = materialize(kind_selected, 800 + index * 20)[0]

    if selected is None:
        output["verdict"] = "reject_no_positive_discovery_configuration"
        return output

    selected_output, selected_trades = materialize(selected, 100)
    output["selected"] = selected_output
    output["verdict"] = "candidate_passed" if selected_output["passed"] else "reject_failed_holdout"

    ledger_path = write_ledgers / f"selected_{series}.jsonl"
    with ledger_path.open("w") as handle:
        for trade in sorted(selected_trades, key=lambda item: (item["ts"], item["ticker"])):
            clean = {key: value for key, value in trade.items() if key not in ("flags", "exit_path")}
            handle.write(json.dumps(clean, sort_keys=True) + "\n")
    return output


def annotate_locked_multiplicity(results: dict) -> None:
    rows = []
    for series_result in results["series"]:
        for rule_name, rule in series_result.get("locked_late_rule", {}).items():
            rows.append(
                {
                    "series_result": series_result,
                    "rule_name": rule_name,
                    "rule": rule,
                    "discovery": rule["discovery"]["raw"],
                }
            )
    indexed = sorted(enumerate(rows), key=lambda item: item[1]["discovery"]["cluster_p"])
    count = len(indexed)
    running = 1.0
    for reverse_rank, (index, row) in enumerate(reversed(indexed), start=1):
        rank = count - reverse_rank + 1
        adjusted = min(running, row["discovery"]["cluster_p"] * count / max(rank, 1))
        rows[index]["bh_q"] = adjusted
        running = adjusted
    for row in rows:
        rule = row["rule"]
        rule["discovery_bh_q_across_series_and_rules"] = row["bh_q"]
        checks = {
            "discovery_n_ge_100": rule["discovery"]["raw"]["n"] >= 100,
            "discovery_bh_q_le_0_10": row["bh_q"] <= 0.10,
            "oos_n_ge_50": rule["oos"]["raw"]["n"] >= 50,
            "validation_stressed_positive": rule["validation"]["stressed"]["mean_cents"] > 0.0,
            "test_stressed_positive": rule["test"]["stressed"]["mean_cents"] > 0.0,
            "oos_stressed_bootstrap_5pct_positive": rule["oos"]["stressed"].get("day_bootstrap_5pct_cents", 0.0) > 0.0,
        }
        if rule["oos"]["raw"].get("settlement_wins") == rule["oos"]["raw"].get("n"):
            checks["oos_zero_loss_tail_bound_supports_raw_edge"] = bool(
                rule["oos"]["raw"].get("zero_loss_tail_bound_supports_edge", False)
            )
            checks["oos_zero_loss_tail_bound_supports_stressed_edge"] = bool(
                rule["oos"]["stressed"].get("zero_loss_tail_bound_supports_edge", False)
            )
        rule["checks"] = checks
        rule["passed"] = all(checks.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--series", nargs="*", default=list(SERIES))
    args = parser.parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "series": [],
    }
    for series in args.series:
        print(f"studying {series}", flush=True)
        result = evaluate_series(args.data_dir, series, args.results_dir)
        results["series"].append(result)
        (args.results_dir / "hourly_study.json").write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n"
        )
        selected = result.get("selected") or {}
        oos = selected.get("oos_stressed") or {}
        print(
            f"{series}: {result['verdict']}; OOS stress n={oos.get('n', 0)} "
            f"mean={oos.get('mean_cents', 0.0):+.3f}c",
            flush=True,
        )
    annotate_locked_multiplicity(results)
    (args.results_dir / "hourly_study.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
