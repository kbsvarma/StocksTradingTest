"""Shared execution and statistical helpers for expiry-market research."""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

from scipy import stats


def number(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def fee(price: float, multiplier: float = 1.0) -> float:
    return 0.07 * multiplier * price * (1.0 - price)


def side_quote(candle: dict, side: str, phase: str = "close") -> tuple[float, float] | None:
    suffix = "c" if phase == "close" else "o"
    bid = number(candle.get(f"b{suffix}"))
    ask = number(candle.get(f"a{suffix}"))
    if bid is None or ask is None:
        return None
    if side == "yes":
        return bid, ask
    return 1.0 - ask, 1.0 - bid


def favorite_quote(candle: dict, phase: str = "close") -> tuple[str, float, float] | None:
    yes_quote = side_quote(candle, "yes", phase)
    if yes_quote is None:
        return None
    yes_bid, yes_ask = yes_quote
    yes_mid = (yes_bid + yes_ask) / 2.0
    if yes_mid >= 0.5:
        return "yes", yes_bid, yes_ask
    no_bid, no_ask = 1.0 - yes_ask, 1.0 - yes_bid
    return "no", no_bid, no_ask


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def poisson_binomial_tail(probabilities: list[float], wins: int) -> float:
    """Exact P(X >= wins) for independent Bernoulli probabilities."""
    if not probabilities:
        return 1.0
    distribution = [1.0] + [0.0] * len(probabilities)
    populated = 0
    for probability in probabilities:
        probability = min(1.0, max(0.0, probability))
        for count in range(populated + 1, 0, -1):
            distribution[count] = (
                distribution[count] * (1.0 - probability)
                + distribution[count - 1] * probability
            )
        distribution[0] *= 1.0 - probability
        populated += 1
    return min(1.0, max(0.0, sum(distribution[wins:])))


def cluster_bootstrap(
    trades: list[dict],
    iterations: int = 4000,
    seed: int = 20260727,
) -> tuple[float, float]:
    by_cluster: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_cluster[trade["cluster"]].append(trade["pnl"])
    clusters = sorted(by_cluster)
    if len(clusters) < 2:
        return 0.0, 0.0
    randomizer = random.Random(seed)
    means: list[float] = []
    for _ in range(iterations):
        sample: list[float] = []
        for _ in clusters:
            sample.extend(by_cluster[randomizer.choice(clusters)])
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * iterations)], means[int(0.975 * iterations)]


def summarize(trades: list[dict]) -> dict:
    ordered = sorted(trades, key=lambda trade: (trade["ts"], trade["ticker"]))
    if not ordered:
        return {
            "n": 0,
            "mean_pnl_cents": 0.0,
            "cluster_count": 0,
            "cluster_p_one_sided": 1.0,
            "exact_break_even_p": 1.0,
        }

    pnls = [trade["pnl"] for trade in ordered]
    wins = sum(bool(trade["win"]) for trade in ordered)
    entries = [trade["entry"] for trade in ordered]
    break_even_probabilities = [
        min(0.999999, entry + fee(entry)) for entry in entries
    ]
    exact_p = poisson_binomial_tail(break_even_probabilities, wins)

    by_cluster: dict[str, list[float]] = defaultdict(list)
    for trade in ordered:
        by_cluster[trade["cluster"]].append(trade["pnl"])
    cluster_means = [
        sum(values) / len(values) for values in by_cluster.values()
    ]
    if len(cluster_means) >= 2:
        cluster_test = stats.ttest_1samp(cluster_means, 0.0, alternative="greater")
        cluster_t = float(cluster_test.statistic)
        cluster_p = float(cluster_test.pvalue)
    else:
        cluster_t = 0.0
        cluster_p = 1.0
    bootstrap_low, bootstrap_high = cluster_bootstrap(ordered)

    split = len(ordered) * 3 // 5
    train = ordered[:split]
    holdout = ordered[split:]
    elapsed_days = max(
        1.0,
        (ordered[-1]["ts"] - ordered[0]["ts"]) / 86400.0,
    )
    return {
        "n": len(ordered),
        "wins": wins,
        "win_rate": wins / len(ordered),
        "average_entry": sum(entries) / len(entries),
        "mean_pnl_cents": 100.0 * sum(pnls) / len(pnls),
        "total_pnl_per_contract": sum(pnls),
        "max_drawdown_per_contract": max_drawdown(pnls),
        "trades_per_day": len(ordered) / elapsed_days,
        "cluster_count": len(by_cluster),
        "cluster_t": cluster_t,
        "cluster_p_one_sided": cluster_p,
        "bootstrap_mean_low_cents": bootstrap_low * 100.0,
        "bootstrap_mean_high_cents": bootstrap_high * 100.0,
        "exact_break_even_p": exact_p,
        "train_n": len(train),
        "train_mean_pnl_cents": (
            100.0 * sum(item["pnl"] for item in train) / len(train)
            if train
            else 0.0
        ),
        "holdout_n": len(holdout),
        "holdout_mean_pnl_cents": (
            100.0 * sum(item["pnl"] for item in holdout) / len(holdout)
            if holdout
            else 0.0
        ),
    }


def benjamini_hochberg(rows: list[dict], key: str) -> None:
    indexed = sorted(
        enumerate(rows),
        key=lambda item: item[1]["metrics"].get(key, 1.0),
    )
    count = len(indexed)
    running = 1.0
    for reverse_rank, (index, row) in enumerate(reversed(indexed), start=1):
        rank = count - reverse_rank + 1
        adjusted = min(
            running,
            row["metrics"].get(key, 1.0) * count / max(rank, 1),
        )
        rows[index]["metrics"][f"{key}_bh"] = adjusted
        running = adjusted
