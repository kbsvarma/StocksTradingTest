#!/usr/bin/env python3
"""Causal replay of the preregistered 15-minute band/stop strategy."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
OLD_DATA = ROOT.parent / "kalshi_crypto15m_2026_07" / "data"
HOLDOUT_DATA = ROOT / "data"
OUT = ROOT / "out"
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")
BANDS = ((0.50, 0.60), (0.55, 0.65), (0.60, 0.70), (0.65, 0.75))
REM_WINDOWS = ((3, 12), (5, 12), (7, 12))
SPREAD_CAPS = (0.01, 0.02, 0.03)
STOP_DISTANCES = (0.08, 0.10, 0.12)
STOP_ANCHORS = ("band_low", "entry")
EXIT_POLICIES = ("hard_stop_hold", "fixed_take_10", "two_phase")
BUY_FEE_MULTIPLIER = 0.07
EPSILON = 1e-9


@dataclass(frozen=True)
class Config:
    band_low: float
    band_high: float
    min_remaining: int
    max_remaining: int
    spread_cap: float
    stop_distance: float
    stop_anchor: str
    exit_policy: str

    @property
    def key(self) -> str:
        return (
            f"b{self.band_low:.2f}-{self.band_high:.2f}_"
            f"r{self.min_remaining}-{self.max_remaining}_"
            f"s{self.spread_cap:.2f}_sl{self.stop_distance:.2f}_{self.stop_anchor}_{self.exit_policy}"
        )


@dataclass(frozen=True)
class Stress:
    name: str
    entry_slippage: float
    stop_slippage: float
    target_slippage: float
    floor_extra_slippage: float


BASELINE = Stress("baseline", 0.00, 0.00, 0.00, 0.00)
STANDARD = Stress("standard", 0.01, 0.01, 0.00, 0.00)
SEVERE = Stress("severe", 0.01, 0.02, 0.01, 0.05)


@dataclass
class Market:
    ticker: str
    family: str
    close_ts: int
    result: str
    volume: float
    candles: list[dict[str, Any]]

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.close_ts, timezone.utc).date().isoformat()


@dataclass
class Trade:
    ticker: str
    family: str
    day: str
    side: str
    signal_ts: int
    remaining_minutes: float
    entry: float
    exit_price: float | None
    exit_reason: str
    outcome_win: bool
    pnl: float
    stopped: bool
    floor_breach: bool
    volume: float


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def iso_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fee(price: float) -> float:
    return BUY_FEE_MULTIPLIER * price * (1.0 - price)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_markets(data_dir: Path, family: str) -> list[Market]:
    market_rows = load_jsonl(data_dir / f"markets_{family}.jsonl")
    candle_rows = load_jsonl(data_dir / f"candles_{family}.jsonl")
    candle_map = {row["ticker"]: row.get("candles", []) for row in candle_rows}
    loaded: list[Market] = []
    for row in market_rows:
        result = str(row.get("result") or "").lower()
        if result not in {"yes", "no"}:
            continue
        close_ts = iso_timestamp(row["close_time"])
        open_ts = iso_timestamp(row["open_time"])
        candles = sorted(
            (
                candle
                for candle in candle_map.get(row["ticker"], [])
                if open_ts < int(candle.get("t", 0)) <= close_ts
            ),
            key=lambda candle: int(candle["t"]),
        )
        if len(candles) < 3:
            continue
        loaded.append(
            Market(
                ticker=row["ticker"],
                family=family,
                close_ts=close_ts,
                result=result,
                volume=number(row.get("volume_fp")) or 0.0,
                candles=candles,
            )
        )
    return sorted(loaded, key=lambda market: (market.close_ts, market.ticker))


def side_quotes(candle: dict[str, Any], side: str) -> dict[str, float] | None:
    values = {key: number(candle.get(key)) for key in ("bo", "bc", "bl", "bh", "ao", "ac", "al", "ah")}
    if any(value is None for value in values.values()):
        return None
    quote = {key: float(value) for key, value in values.items() if value is not None}
    if side == "yes":
        return {
            "bid_open": quote["bo"],
            "bid_close": quote["bc"],
            "bid_low": quote["bl"],
            "bid_high": quote["bh"],
            "ask_open": quote["ao"],
            "ask_close": quote["ac"],
            "ask_low": quote["al"],
            "ask_high": quote["ah"],
        }
    return {
        "bid_open": 1.0 - quote["ao"],
        "bid_close": 1.0 - quote["ac"],
        "bid_low": 1.0 - quote["ah"],
        "bid_high": 1.0 - quote["al"],
        "ask_open": 1.0 - quote["bo"],
        "ask_close": 1.0 - quote["bc"],
        "ask_low": 1.0 - quote["bh"],
        "ask_high": 1.0 - quote["bl"],
    }


def favorite_side(candle: dict[str, Any], at_open: bool = False) -> tuple[str, dict[str, float]] | None:
    suffix = "open" if at_open else "close"
    yes = side_quotes(candle, "yes")
    no = side_quotes(candle, "no")
    if yes is None or no is None:
        return None
    yes_mid = (yes[f"bid_{suffix}"] + yes[f"ask_{suffix}"]) / 2.0
    no_mid = (no[f"bid_{suffix}"] + no[f"ask_{suffix}"]) / 2.0
    if yes_mid >= no_mid:
        return "yes", yes
    return "no", no


def settlement_pnl(entry: float, win: bool) -> float:
    return (1.0 if win else 0.0) - entry - fee(entry)


def sale_pnl(entry: float, exit_price: float) -> float:
    return exit_price - entry - fee(entry) - fee(exit_price)


def simulated_exit(
    market: Market,
    side: str,
    entry: float,
    first_candle_index: int,
    config: Config,
    stress: Stress,
) -> tuple[float | None, str, bool, bool]:
    stop_base = config.band_low if config.stop_anchor == "band_low" else entry
    stop = max(0.01, stop_base - config.stop_distance)
    floor = max(0.01, stop - 0.05)
    fixed_target = min(0.99, entry + 0.10)
    safe = min(0.99, entry + 0.06)
    ideal = min(0.99, entry + 0.13)
    safe_armed = False
    peak_bid = 0.0
    trailing = safe

    for candle in market.candles[first_candle_index:]:
        quote = side_quotes(candle, side)
        if quote is None:
            continue
        bid_low = quote["bid_low"]
        bid_high = quote["bid_high"]

        # The stop wins every same-minute ordering ambiguity.
        if bid_low <= stop + EPSILON:
            floor_breach = bid_low <= floor + EPSILON
            if floor_breach:
                exit_price = max(0.01, floor - stress.floor_extra_slippage)
                return exit_price, "emergency_floor", True, True
            exit_price = max(floor, stop - stress.stop_slippage)
            return exit_price, "stop", True, False

        if config.exit_policy == "fixed_take_10":
            if bid_high >= fixed_target - EPSILON:
                return max(0.01, fixed_target - stress.target_slippage), "take_10", False, False
            continue

        if config.exit_policy != "two_phase":
            continue

        if safe_armed and bid_low <= trailing + EPSILON:
            return max(0.01, trailing - stress.target_slippage), "safe_trail", False, False

        if bid_high >= ideal - EPSILON:
            return max(0.01, ideal - stress.target_slippage), "ideal", False, False

        if not safe_armed and bid_high >= safe - EPSILON:
            safe_armed = True
            peak_bid = max(peak_bid, bid_high)
            trailing = max(safe, peak_bid - 0.01)
            if bid_low <= safe + EPSILON:
                return max(0.01, safe - stress.target_slippage), "safe_same_minute", False, False
            continue

        if safe_armed:
            peak_bid = max(peak_bid, bid_high)
            trailing = max(safe, peak_bid - 0.01)

    return None, "settlement", False, False


def simulate_market(market: Market, config: Config, stress: Stress) -> Trade | None:
    signal: tuple[int, str, dict[str, float]] | None = None
    for index, candle in enumerate(market.candles[:-1]):
        remaining = (market.close_ts - int(candle["t"])) / 60.0
        if remaining < config.min_remaining - EPSILON or remaining > config.max_remaining + EPSILON:
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
        signal = (index, side, quote)
        break

    if signal is None:
        return None

    signal_index, side, signal_quote = signal
    next_quote = side_quotes(market.candles[signal_index + 1], side)
    if next_quote is None:
        return None
    next_mid = (next_quote["bid_open"] + next_quote["ask_open"]) / 2.0
    if next_mid < 0.50 - EPSILON:
        return None
    entry = max(signal_quote["ask_close"], next_quote["ask_open"]) + stress.entry_slippage
    if entry > config.band_high + EPSILON or entry >= 1.0 - EPSILON:
        return None

    exit_price, reason, stopped, floor_breach = simulated_exit(
        market,
        side,
        entry,
        signal_index + 1,
        config,
        stress,
    )
    outcome_win = market.result == side
    pnl = settlement_pnl(entry, outcome_win) if exit_price is None else sale_pnl(entry, exit_price)
    return Trade(
        ticker=market.ticker,
        family=market.family,
        day=market.day,
        side=side,
        signal_ts=int(market.candles[signal_index]["t"]),
        remaining_minutes=(market.close_ts - int(market.candles[signal_index]["t"])) / 60.0,
        entry=entry,
        exit_price=exit_price,
        exit_reason=reason,
        outcome_win=outcome_win,
        pnl=pnl,
        stopped=stopped,
        floor_breach=floor_breach,
        volume=market.volume,
    )


def configurations() -> list[Config]:
    return [
        Config(low, high, min_rem, max_rem, spread, stop, anchor, policy)
        for low, high in BANDS
        for min_rem, max_rem in REM_WINDOWS
        for spread in SPREAD_CAPS
        for stop in STOP_DISTANCES
        for anchor in STOP_ANCHORS
        for policy in EXIT_POLICIES
    ]


def max_drawdown(trades: Sequence[Trade]) -> float:
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in sorted(trades, key=lambda item: (item.signal_ts, item.ticker)):
        cumulative += trade.pnl
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return drawdown


def aggregate(trades: Sequence[Trade]) -> dict[str, float | int]:
    if not trades:
        return {
            "n": 0,
            "days": 0,
            "net": 0.0,
            "mean": 0.0,
            "win_rate": 0.0,
            "stop_rate": 0.0,
            "floor_rate": 0.0,
            "trades_per_day": 0.0,
            "max_drawdown": 0.0,
            "mean_entry": 0.0,
        }
    days = len({trade.day for trade in trades})
    return {
        "n": len(trades),
        "days": days,
        "net": sum(trade.pnl for trade in trades),
        "mean": mean(trade.pnl for trade in trades),
        "win_rate": mean(1.0 if trade.outcome_win else 0.0 for trade in trades),
        "stop_rate": mean(1.0 if trade.stopped else 0.0 for trade in trades),
        "floor_rate": mean(1.0 if trade.floor_breach else 0.0 for trade in trades),
        "trades_per_day": len(trades) / days,
        "max_drawdown": max_drawdown(trades),
        "mean_entry": mean(trade.entry for trade in trades),
    }


def daily_means(trades: Sequence[Trade]) -> list[float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.day].append(trade.pnl)
    return [mean(grouped[day]) for day in sorted(grouped)]


def one_sided_t_pvalue(trades: Sequence[Trade]) -> float:
    values = daily_means(trades)
    if len(values) < 3:
        return 1.0
    sigma = stdev(values)
    if sigma <= EPSILON:
        return 0.0 if mean(values) > 0 else 1.0
    t_stat = mean(values) / (sigma / math.sqrt(len(values)))
    try:
        from scipy.stats import t as student_t

        return float(student_t.sf(t_stat, df=len(values) - 1))
    except ImportError:
        return 0.5 * math.erfc(t_stat / math.sqrt(2.0))


def bh_qvalues(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    count = len(ordered)
    qvalues: dict[str, float] = {}
    running = 1.0
    for rank_from_end, (key, pvalue) in enumerate(reversed(ordered), start=1):
        rank = count - rank_from_end + 1
        running = min(running, pvalue * count / rank)
        qvalues[key] = min(1.0, running)
    return qvalues


def bootstrap_daily(trades: Sequence[Trade], iterations: int = 10_000) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.day].append(trade.pnl)
    days = sorted(grouped)
    if not days:
        return {"prob_positive": 0.0, "p05_mean": 0.0, "median_mean": 0.0}
    seed_text = "|".join(trade.ticker for trade in trades[:50]) + str(len(trades))
    rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16))
    samples: list[float] = []
    for _ in range(iterations):
        selected = [rng.choice(days) for _ in days]
        pnls = [pnl for day in selected for pnl in grouped[day]]
        samples.append(mean(pnls))
    samples.sort()
    return {
        "prob_positive": sum(value > 0 for value in samples) / len(samples),
        "p05_mean": samples[int(0.05 * (len(samples) - 1))],
        "median_mean": median(samples),
    }


def split_old(markets: Sequence[Market]) -> tuple[list[Market], list[Market]]:
    days = sorted({market.day for market in markets})
    split_index = max(1, min(len(days) - 1, math.ceil(len(days) * 0.60)))
    discovery_days = set(days[:split_index])
    return (
        [market for market in markets if market.day in discovery_days],
        [market for market in markets if market.day not in discovery_days],
    )


def config_distance(left: Config, right: Config) -> int:
    left_values = (
        left.band_low,
        left.min_remaining,
        left.spread_cap,
        left.stop_distance,
        left.stop_anchor,
        left.exit_policy,
    )
    right_values = (
        right.band_low,
        right.min_remaining,
        right.spread_cap,
        right.stop_distance,
        right.stop_anchor,
        right.exit_policy,
    )
    return sum(a != b for a, b in zip(left_values, right_values))


def replay(markets: Sequence[Market], config: Config, stress: Stress) -> list[Trade]:
    return [trade for market in markets if (trade := simulate_market(market, config, stress)) is not None]


def select_candidate(
    configs: Sequence[Config],
    discovery_results: dict[str, dict[str, Any]],
    qvalues: dict[str, float],
) -> tuple[Config | None, dict[str, Any]]:
    eligible: list[tuple[float, Config, dict[str, Any]]] = []
    for config in configs:
        result = discovery_results[config.key]
        if qvalues[config.key] > 0.05 or result["mean"] <= 0 or result["n"] < 100:
            continue
        neighbours = [
            discovery_results[other.key]["mean"]
            for other in configs
            if config_distance(config, other) == 1
        ]
        positive_share = mean(value > 0 for value in neighbours) if neighbours else 0.0
        neighbour_median = median(neighbours) if neighbours else 0.0
        if positive_share < 0.60 or neighbour_median <= 0:
            continue
        plateau = {"positive_share": positive_share, "median": neighbour_median, "count": len(neighbours)}
        eligible.append((float(result["mean"]), config, plateau))
    if not eligible:
        return None, {"positive_share": 0.0, "median": 0.0, "count": 0}
    _, config, plateau = max(eligible, key=lambda item: item[0])
    return config, plateau


def cents(value: float) -> str:
    return f"{value * 100:+.3f}c"


def write_trades(path: Path, trades: Sequence[Trade]) -> None:
    if not trades:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()))
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def study_family(family: str) -> dict[str, Any]:
    old = load_markets(OLD_DATA, family)
    holdout = load_markets(HOLDOUT_DATA, family)
    discovery, validation = split_old(old)
    configs = configurations()
    discovery_trades: dict[str, list[Trade]] = {}
    discovery_results: dict[str, dict[str, Any]] = {}
    pvalues: dict[str, float] = {}
    for index, config in enumerate(configs, start=1):
        trades = replay(discovery, config, STANDARD)
        discovery_trades[config.key] = trades
        discovery_results[config.key] = aggregate(trades)
        pvalues[config.key] = one_sided_t_pvalue(trades)
        if index % 100 == 0:
            print(f"  {family}: discovery {index}/{len(configs)}", flush=True)
    qvalues = bh_qvalues(pvalues)
    candidate, plateau = select_candidate(configs, discovery_results, qvalues)

    rows: list[dict[str, Any]] = []
    for config in configs:
        disc = discovery_results[config.key]
        val_trades = replay(validation, config, STANDARD)
        val = aggregate(val_trades)
        rows.append(
            {
                **asdict(config),
                "key": config.key,
                "discovery_n": disc["n"],
                "discovery_mean": disc["mean"],
                "discovery_q": qvalues[config.key],
                "validation_n": val["n"],
                "validation_mean": val["mean"],
            }
        )
    with (OUT / f"surface_{family}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    result: dict[str, Any] = {
        "family": family,
        "old_markets": len(old),
        "holdout_markets": len(holdout),
        "discovery_days": len({market.day for market in discovery}),
        "validation_days": len({market.day for market in validation}),
        "holdout_days": len({market.day for market in holdout}),
        "candidate": asdict(candidate) if candidate else None,
        "candidate_key": candidate.key if candidate else None,
        "plateau": plateau,
        "passes": False,
    }
    if candidate is None:
        return result

    slices = {"discovery": discovery, "validation": validation, "holdout": holdout}
    result["slices"] = {}
    for slice_name, markets in slices.items():
        result["slices"][slice_name] = {}
        for stress in (BASELINE, STANDARD, SEVERE):
            trades = replay(markets, candidate, stress)
            stats = aggregate(trades)
            stats.update(bootstrap_daily(trades, iterations=10_000))
            result["slices"][slice_name][stress.name] = stats
            if slice_name == "holdout":
                write_trades(OUT / f"trades_{family}_{stress.name}.csv", trades)

    standard_validation = result["slices"]["validation"]["standard"]
    standard_holdout = result["slices"]["holdout"]["standard"]
    severe_holdout = result["slices"]["holdout"]["severe"]
    result["passes"] = bool(
        standard_validation["mean"] > 0
        and standard_holdout["n"] >= 200
        and standard_holdout["days"] >= 10
        and standard_holdout["mean"] > 0
        and standard_holdout["p05_mean"] > 0
        and severe_holdout["mean"] > 0
    )
    return result


def report(results: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# Kalshi 15-Minute Band/Stop Study",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "The July 28-August 11 holdout was pulled only after `PREREG.md` was locked.",
        "All reported PnL is net of exact fractional Kalshi fees.",
        "",
        "## Verdict",
        "",
    ]
    passers = [result for result in results if result.get("passes")]
    if passers:
        lines.append(
            f"{len(passers)} family/families clear every preregistered gate: "
            + ", ".join(result["family"] for result in passers)
            + "."
        )
    else:
        lines.append("No family clears every preregistered gate. Do not implement this rule live.")
    lines.extend(["", "## Family Results", ""])
    for result in results:
        lines.append(f"### {result['family']}")
        lines.append("")
        lines.append(
            f"Markets: old {result['old_markets']:,}; holdout {result['holdout_markets']:,} "
            f"across {result['holdout_days']} UTC days."
        )
        if result["candidate"] is None:
            lines.append("No discovery candidate survived FDR and the local-plateau requirement.")
            lines.append("")
            continue
        candidate = result["candidate"]
        lines.append(
            "Selected on discovery only: "
            f"ask {candidate['band_low']:.0%}-{candidate['band_high']:.0%}, "
            f"remaining {candidate['min_remaining']}-{candidate['max_remaining']}m, "
            f"spread <= {candidate['spread_cap']:.0%}, stop {candidate['stop_distance']:.0%} "
            f"below `{candidate['stop_anchor']}`, "
            f"exit `{candidate['exit_policy']}`."
        )
        lines.append(
            f"Local plateau: {result['plateau']['positive_share']:.0%} positive neighbours; "
            f"median neighbour {cents(result['plateau']['median'])}."
        )
        lines.append("")
        lines.append("| Slice / stress | Trades | Trades/day | Net/contract | 5th pct | P(edge>0) | Stop | Floor | Max DD |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for slice_name in ("discovery", "validation", "holdout"):
            for stress_name in ("baseline", "standard", "severe"):
                stats = result["slices"][slice_name][stress_name]
                lines.append(
                    f"| {slice_name} / {stress_name} | {stats['n']} | {stats['trades_per_day']:.2f} | "
                    f"{cents(stats['mean'])} | {cents(stats['p05_mean'])} | {stats['prob_positive']:.1%} | "
                    f"{stats['stop_rate']:.1%} | {stats['floor_rate']:.1%} | ${stats['max_drawdown']:.2f} |"
                )
        lines.append("")
        lines.append(f"Promotion verdict: **{'PASS' if result['passes'] else 'FAIL'}**.")
        lines.append("")
    lines.extend(
        [
            "## Execution Meaning",
            "",
            "`standard` assumes paying one cent worse than the next visible ask and losing one cent "
            "through the stop. `severe` adds two-cent stop slippage, one-cent target slippage, and "
            "a five-cent failure beyond the emergency floor whenever the minute trades through it.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for family in SERIES:
        print(f"== {family}", flush=True)
        results.append(study_family(family))
    (OUT / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    (ROOT / "REPORT.md").write_text(report(results) + "\n")
    print(f"wrote {ROOT / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
