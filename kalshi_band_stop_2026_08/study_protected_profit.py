#!/usr/bin/env python3
"""Replay the clarified Safe_Y/Ideal_Y protected-profit state machine."""

from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

from study_band_stop import (
    HOLDOUT_DATA,
    OLD_DATA,
    SERIES,
    Market,
    aggregate,
    bh_qvalues,
    bootstrap_daily,
    favorite_side,
    fee,
    load_markets,
    one_sided_t_pvalue,
    side_quotes,
    split_old,
)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out_protected"
EPSILON = 1e-9
BANDS = ((0.50, 0.60), (0.55, 0.65), (0.60, 0.70), (0.65, 0.75), (0.70, 0.80))
STOPS = (0.35, 0.40, 0.45, 0.50, 0.55)
SAFE_DELTAS = (0.04, 0.06, 0.08)
IDEAL_DELTAS = (0.10, 0.13, 0.16)
REM_WINDOWS = ((1, 4), (5, 8), (9, 12), (1, 12))
SPREAD_CAPS = (0.01, 0.02)
PATH_ORDERS = ("high_low", "low_high")


@dataclass(frozen=True)
class Config:
    band_low: float
    band_high: float
    stop_price: float
    safe_delta: float
    ideal_delta: float
    min_remaining: int
    max_remaining: int
    spread_cap: float

    @property
    def key(self) -> str:
        return (
            f"b{self.band_low:.2f}-{self.band_high:.2f}_stop{self.stop_price:.2f}_"
            f"safe{self.safe_delta:.2f}_ideal{self.ideal_delta:.2f}_"
            f"r{self.min_remaining}-{self.max_remaining}_s{self.spread_cap:.2f}"
        )


@dataclass(frozen=True)
class Stress:
    name: str
    entry_slippage: float
    protected_slippage: float
    stop_slippage: float
    breached_floor_slippage: float


BASELINE = Stress("baseline", 0.00, 0.00, 0.00, 0.00)
STANDARD = Stress("standard", 0.01, 0.01, 0.01, 0.00)
SEVERE = Stress("severe", 0.01, 0.02, 0.02, 0.05)


@dataclass
class Trade:
    ticker: str
    family: str
    day: str
    side: str
    signal_ts: int
    remaining_minutes: float
    entry: float
    safe_y: float
    ideal_y: float
    stop_price: float
    exit_price: float | None
    exit_reason: str
    outcome_win: bool
    pnl: float
    stopped: bool
    floor_breach: bool
    safe_armed: bool
    ideal_armed: bool
    path_order: str
    volume: float


def configurations() -> list[Config]:
    return [
        Config(low, high, stop, safe, ideal, min_rem, max_rem, spread)
        for low, high in BANDS
        for stop in STOPS
        if stop <= low - 0.05 + EPSILON
        for safe in SAFE_DELTAS
        for ideal in IDEAL_DELTAS
        if ideal > safe
        for min_rem, max_rem in REM_WINDOWS
        for spread in SPREAD_CAPS
    ]


def sale_pnl(entry: float, exit_price: float) -> float:
    return exit_price - entry - fee(entry) - fee(exit_price)


def settlement_pnl(entry: float, win: bool) -> float:
    return (1.0 if win else 0.0) - entry - fee(entry)


def process_price(
    price: float,
    config: Config,
    safe_y: float,
    ideal_y: float,
    safe_armed: bool,
    ideal_armed: bool,
    stress: Stress,
    candle_low: float,
) -> tuple[float | None, str | None, bool, bool]:
    if price <= config.stop_price + EPSILON:
        stop_fill = max(0.01, config.stop_price - stress.stop_slippage)
        if stress.breached_floor_slippage and candle_low <= 0.40 + EPSILON:
            stop_fill = max(0.01, min(stop_fill, 0.40 - stress.breached_floor_slippage))
        return stop_fill, "absolute_stop", safe_armed, ideal_armed

    if ideal_armed and price <= ideal_y + EPSILON:
        return max(config.stop_price, ideal_y - stress.protected_slippage), "ideal_protection", True, True
    if safe_armed and price <= safe_y + EPSILON:
        return max(config.stop_price, safe_y - stress.protected_slippage), "safe_protection", True, False

    if price >= ideal_y - EPSILON:
        return None, None, True, True
    if price >= safe_y - EPSILON:
        return None, None, True, ideal_armed
    return None, None, safe_armed, ideal_armed


def simulate_path(
    market: Market,
    side: str,
    entry: float,
    first_index: int,
    config: Config,
    stress: Stress,
    path_order: str,
    signal_ts: int,
    remaining_minutes: float,
) -> Trade:
    safe_y = min(0.99, entry + config.safe_delta)
    ideal_y = min(0.99, entry + config.ideal_delta)
    safe_armed = False
    ideal_armed = False
    exit_price = None
    exit_reason = "settlement"
    floor_breach = False

    for candle in market.candles[first_index:]:
        quote = side_quotes(candle, side)
        if quote is None:
            continue
        middle = (
            (quote["bid_high"], quote["bid_low"])
            if path_order == "high_low"
            else (quote["bid_low"], quote["bid_high"])
        )
        prices = (quote["bid_open"], *middle, quote["bid_close"])
        for price in prices:
            result, reason, safe_armed, ideal_armed = process_price(
                price,
                config,
                safe_y,
                ideal_y,
                safe_armed,
                ideal_armed,
                stress,
                quote["bid_low"],
            )
            if result is not None:
                exit_price = result
                exit_reason = reason or "unknown_exit"
                floor_breach = exit_reason == "absolute_stop" and quote["bid_low"] <= 0.40 + EPSILON
                break
        if exit_price is not None:
            break

    outcome_win = market.result == side
    pnl = settlement_pnl(entry, outcome_win) if exit_price is None else sale_pnl(entry, exit_price)
    return Trade(
        ticker=market.ticker,
        family=market.family,
        day=market.day,
        side=side,
        signal_ts=signal_ts,
        remaining_minutes=remaining_minutes,
        entry=entry,
        safe_y=safe_y,
        ideal_y=ideal_y,
        stop_price=config.stop_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        outcome_win=outcome_win,
        pnl=pnl,
        stopped=exit_reason == "absolute_stop",
        floor_breach=floor_breach,
        safe_armed=safe_armed,
        ideal_armed=ideal_armed,
        path_order=path_order,
        volume=market.volume,
    )


def simulate_market(market: Market, config: Config, stress: Stress, path_order: str) -> Trade | None:
    signal = None
    for index, candle in enumerate(market.candles[:-1]):
        remaining = (market.close_ts - int(candle["t"])) / 60.0
        if not (config.min_remaining - EPSILON <= remaining <= config.max_remaining + EPSILON):
            continue
        favorite = favorite_side(candle)
        if favorite is None:
            continue
        side, quote = favorite
        midpoint = (quote["bid_close"] + quote["ask_close"]) / 2.0
        spread = quote["ask_close"] - quote["bid_close"]
        if midpoint < 0.50 - EPSILON:
            continue
        if not (config.band_low - EPSILON <= quote["ask_close"] <= config.band_high + EPSILON):
            continue
        if spread > config.spread_cap + EPSILON:
            continue
        signal = (index, side, quote, remaining)
        break
    if signal is None:
        return None

    index, side, quote, remaining = signal
    next_quote = side_quotes(market.candles[index + 1], side)
    if next_quote is None:
        return None
    next_mid = (next_quote["bid_open"] + next_quote["ask_open"]) / 2.0
    if next_mid < 0.50 - EPSILON:
        return None
    entry = max(quote["ask_close"], next_quote["ask_open"]) + stress.entry_slippage
    if entry > config.band_high + EPSILON or entry >= 1.0 - EPSILON:
        return None
    return simulate_path(
        market,
        side,
        entry,
        index + 1,
        config,
        stress,
        path_order,
        int(market.candles[index]["t"]),
        remaining,
    )


def replay(markets: Sequence[Market], config: Config, stress: Stress, path_order: str) -> list[Trade]:
    return [
        trade
        for market in markets
        if (trade := simulate_market(market, config, stress, path_order)) is not None
    ]


def replay_worst(markets: Sequence[Market], config: Config, stress: Stress) -> list[Trade]:
    by_path = {order: replay(markets, config, stress, order) for order in PATH_ORDERS}
    indexed = {
        order: {trade.ticker: trade for trade in trades}
        for order, trades in by_path.items()
    }
    common = sorted(set(indexed["high_low"]) & set(indexed["low_high"]))
    return [
        min((indexed[order][ticker] for order in PATH_ORDERS), key=lambda trade: trade.pnl)
        for ticker in common
    ]


def config_distance(left: Config, right: Config) -> int:
    return sum(
        a != b
        for a, b in zip(
            asdict(left).values(),
            asdict(right).values(),
        )
    )


def select_candidate(
    configs: Sequence[Config],
    discovery: dict[str, dict[str, Any]],
    qvalues: dict[str, float],
) -> tuple[Config | None, dict[str, float]]:
    eligible = []
    for config in configs:
        stats = discovery[config.key]
        if stats["n"] < 100 or stats["mean"] <= 0 or qvalues[config.key] > 0.05:
            continue
        neighbour_means = [
            discovery[other.key]["mean"]
            for other in configs
            if config_distance(config, other) == 1
        ]
        positive_share = mean(value > 0 for value in neighbour_means) if neighbour_means else 0.0
        neighbour_median = median(neighbour_means) if neighbour_means else 0.0
        if positive_share < 0.60 or neighbour_median <= 0:
            continue
        eligible.append((stats["mean"], config, positive_share, neighbour_median))
    if not eligible:
        return None, {"positive_share": 0.0, "median": 0.0}
    _, config, positive_share, neighbour_median = max(eligible, key=lambda item: item[0])
    return config, {"positive_share": positive_share, "median": neighbour_median}


def evaluate(markets: Sequence[Market], config: Config, stress: Stress) -> dict[str, Any]:
    trades = replay_worst(markets, config, stress)
    stats = aggregate(trades)
    stats.update(bootstrap_daily(trades, iterations=10_000))
    stats["safe_arm_rate"] = mean(trade.safe_armed for trade in trades) if trades else 0.0
    stats["ideal_arm_rate"] = mean(trade.ideal_armed for trade in trades) if trades else 0.0
    stats["full_settlement_rate"] = mean(trade.exit_reason == "settlement" for trade in trades) if trades else 0.0
    return stats


def evaluate_path(
    markets: Sequence[Market], config: Config, stress: Stress, path_order: str
) -> dict[str, Any]:
    trades = replay(markets, config, stress, path_order)
    stats = aggregate(trades)
    stats["safe_arm_rate"] = mean(trade.safe_armed for trade in trades) if trades else 0.0
    stats["ideal_arm_rate"] = mean(trade.ideal_armed for trade in trades) if trades else 0.0
    stats["full_settlement_rate"] = mean(trade.exit_reason == "settlement" for trade in trades) if trades else 0.0
    return stats


def reference_config() -> Config:
    return Config(0.60, 0.70, 0.45, 0.06, 0.13, 1, 12, 0.02)


def cents(value: float) -> str:
    return f"{100 * value:+.3f}c"


def family_study(family: str) -> dict[str, Any]:
    old = load_markets(OLD_DATA, family)
    recent = load_markets(HOLDOUT_DATA, family)
    discovery_markets, validation_markets = split_old(old)
    configs = configurations()
    discovery_stats = {}
    pvalues = {}
    rows = []
    for index, config in enumerate(configs, start=1):
        trades = replay_worst(discovery_markets, config, STANDARD)
        stats = aggregate(trades)
        discovery_stats[config.key] = stats
        pvalues[config.key] = one_sided_t_pvalue(trades)
        if index % 250 == 0:
            print(f"  {family}: discovery {index}/{len(configs)}", flush=True)
    qvalues = bh_qvalues(pvalues)
    candidate, plateau = select_candidate(configs, discovery_stats, qvalues)
    best_observed = max(configs, key=lambda config: discovery_stats[config.key]["mean"])

    for config in configs:
        stats = discovery_stats[config.key]
        rows.append(
            {
                **asdict(config),
                "key": config.key,
                "discovery_n": stats["n"],
                "discovery_mean": stats["mean"],
                "discovery_q": qvalues[config.key],
            }
        )
    with (OUT / f"surface_{family}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    slices = {
        "discovery": discovery_markets,
        "validation": validation_markets,
        "recent": recent,
    }
    result: dict[str, Any] = {
        "family": family,
        "market_counts": {name: len(markets) for name, markets in slices.items()},
        "reference": {},
        "candidate": asdict(candidate) if candidate else None,
        "best_observed": asdict(best_observed),
        "best_observed_results": {},
        "plateau": plateau,
        "candidate_results": {},
        "passes": False,
    }
    reference = reference_config()
    for name, markets in slices.items():
        result["reference"][name] = {
            stress.name: evaluate(markets, reference, stress)
            for stress in (BASELINE, STANDARD, SEVERE)
        }
        result["reference"][name]["path_sensitivity"] = {
            order: evaluate_path(markets, reference, STANDARD, order)
            for order in PATH_ORDERS
        }
        result["best_observed_results"][name] = {
            stress.name: evaluate(markets, best_observed, stress)
            for stress in (BASELINE, STANDARD, SEVERE)
        }
    if candidate is None:
        return result
    for name, markets in slices.items():
        result["candidate_results"][name] = {
            stress.name: evaluate(markets, candidate, stress)
            for stress in (BASELINE, STANDARD, SEVERE)
        }
    validation = result["candidate_results"]["validation"]["standard"]
    recent = result["candidate_results"]["recent"]["standard"]
    recent_severe = result["candidate_results"]["recent"]["severe"]
    result["passes"] = bool(
        validation["mean"] > 0
        and recent["n"] >= 200
        and recent["days"] >= 10
        and recent["mean"] > 0
        and recent["p05_mean"] > 0
        and recent_severe["mean"] > 0
    )
    return result


def metric_table(title: str, metrics: dict[str, dict[str, Any]]) -> list[str]:
    lines = [title, "", "| Slice / stress | n | Net/ct | 5th pct | P(edge>0) | Safe | Ideal | Settle | Stop | DD |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for slice_name in ("discovery", "validation", "recent"):
        for stress_name in ("baseline", "standard", "severe"):
            stats = metrics[slice_name][stress_name]
            lines.append(
                f"| {slice_name}/{stress_name} | {stats['n']} | {cents(stats['mean'])} | "
                f"{cents(stats['p05_mean'])} | {stats['prob_positive']:.1%} | "
                f"{stats['safe_arm_rate']:.1%} | {stats['ideal_arm_rate']:.1%} | "
                f"{stats['full_settlement_rate']:.1%} | {stats['stop_rate']:.1%} | "
                f"${stats['max_drawdown']:.2f} |"
            )
    return lines + [""]


def config_from_mapping(values: dict[str, Any]) -> Config:
    return Config(
        float(values["band_low"]),
        float(values["band_high"]),
        float(values["stop_price"]),
        float(values["safe_delta"]),
        float(values["ideal_delta"]),
        int(values["min_remaining"]),
        int(values["max_remaining"]),
        float(values["spread_cap"]),
    )


def enrich_existing_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for result in results:
        family = result["family"]
        rows = list(csv.DictReader((OUT / f"surface_{family}.csv").open()))
        best_row = max(rows, key=lambda row: float(row["discovery_mean"]))
        best = config_from_mapping(best_row)
        result["best_observed"] = asdict(best)
        result["best_observed_results"] = {}
        old = load_markets(OLD_DATA, family)
        recent = load_markets(HOLDOUT_DATA, family)
        discovery, validation = split_old(old)
        slices = {"discovery": discovery, "validation": validation, "recent": recent}
        reference = reference_config()
        for name, markets in slices.items():
            result["best_observed_results"][name] = {
                stress.name: evaluate(markets, best, stress)
                for stress in (BASELINE, STANDARD, SEVERE)
            }
            result["reference"][name]["path_sensitivity"] = {
                order: evaluate_path(markets, reference, STANDARD, order)
                for order in PATH_ORDERS
            }
    return results


def build_report(results: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# Protected-Profit 15-Minute Crypto Research",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report tests the clarified state machine. The user's numerical example is a benchmark; "
        "the search is allowed to choose different bands, stops, and protection distances.",
        "",
        "## Bottom Line",
        "",
    ]
    passers = [result for result in results if result["passes"]]
    if passers:
        lines.append("Validated candidates: " + ", ".join(result["family"] for result in passers) + ".")
    else:
        lines.append("No family passes every promotion gate. Nothing from this study should be enabled live.")
    lines.append("")
    for result in results:
        lines.extend([f"## {result['family']}", ""])
        lines.extend(metric_table("### Reference Example", result["reference"]))
        recent_paths = result["reference"]["recent"].get("path_sensitivity", {})
        if recent_paths:
            lines.append(
                "Recent standard-stress path sensitivity: "
                f"high-then-low {cents(recent_paths['high_low']['mean'])}; "
                f"low-then-high {cents(recent_paths['low_high']['mean'])}."
            )
            lines.append("")
        if result["candidate"] is None:
            best = result["best_observed"]
            lines.append("No discovery candidate survived FDR plus the local-plateau requirement.")
            lines.append(
                "The least-bad searched rule was "
                f"band {best['band_low']:.0%}-{best['band_high']:.0%}, stop {best['stop_price']:.0%}, "
                f"Safe +{best['safe_delta']:.0%}, Ideal +{best['ideal_delta']:.0%}, "
                f"rem {best['min_remaining']}-{best['max_remaining']}m, spread <= {best['spread_cap']:.0%}."
            )
            lines.append("")
            lines.extend(metric_table("### Least-Bad Searched Rule", result["best_observed_results"]))
            continue
        candidate = result["candidate"]
        lines.append(
            "Discovery-selected candidate: "
            f"band {candidate['band_low']:.0%}-{candidate['band_high']:.0%}, "
            f"absolute stop {candidate['stop_price']:.0%}, Safe +{candidate['safe_delta']:.0%}, "
            f"Ideal +{candidate['ideal_delta']:.0%}, rem {candidate['min_remaining']}-"
            f"{candidate['max_remaining']}m, spread <= {candidate['spread_cap']:.0%}."
        )
        lines.append(
            f"Plateau: {result['plateau']['positive_share']:.0%} positive neighbours, "
            f"median {cents(result['plateau']['median'])}."
        )
        lines.append("")
        lines.extend(metric_table("### Discovery-Selected Candidate", result["candidate_results"]))
        lines.extend([f"Promotion: **{'PASS' if result['passes'] else 'FAIL'}**.", ""])
    lines.extend(
        [
            "## Interpretation",
            "",
            "Safe_Y and Ideal_Y are protection states, not first-touch take-profit orders. The `Settle` "
            "column is the share allowed to run to the final payout. Results use the worse of the two "
            "possible minute-bar high/low orderings, so unknown intraminute sequencing cannot be chosen "
            "in the strategy's favor.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if "--finalize-only" in sys.argv:
        results = enrich_existing_results(json.loads((OUT / "results.json").read_text()))
    else:
        for family in SERIES:
            print(f"queueing {family}", flush=True)
        with ProcessPoolExecutor(max_workers=len(SERIES)) as executor:
            results = list(executor.map(family_study, SERIES))
    (OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    (ROOT / "PROTECTED_PROFIT_REPORT.md").write_text(build_report(results) + "\n")
    print(f"wrote {ROOT / 'PROTECTED_PROFIT_REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
