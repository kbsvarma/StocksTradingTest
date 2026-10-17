"""Shared, conservative statistics for the Kalshi universe study."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def utc_day(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def fee(price: float, multiplier: float = 1.0) -> float:
    """Exact per-contract quadratic fee used for research comparisons."""
    return 0.07 * multiplier * price * (1.0 - price)


def contract_pnl(price: float, won: bool, multiplier: float = 1.0) -> float:
    return (1.0 - price if won else -price) - fee(price, multiplier)


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def bh_adjust(p_values: Iterable[float]) -> list[float]:
    values = list(p_values)
    count = len(values)
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    adjusted = [1.0] * count
    running = 1.0
    for reverse_rank, (index, value) in enumerate(reversed(ordered), start=1):
        rank = count - reverse_rank + 1
        running = min(running, value * count / rank)
        adjusted[index] = min(1.0, running)
    return adjusted


def chronological_splits(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    clusters = sorted({row["cluster"] for row in rows})
    first_cut = max(1, int(len(clusters) * 0.50))
    second_cut = max(first_cut + 1, int(len(clusters) * 0.75))
    train = set(clusters[:first_cut])
    validation = set(clusters[first_cut:second_cut])
    test = set(clusters[second_cut:])
    return (
        [row for row in rows if row["cluster"] in train],
        [row for row in rows if row["cluster"] in validation],
        [row for row in rows if row["cluster"] in test],
    )


def mean_cents(rows: list[dict], field: str = "pnl") -> float | None:
    if not rows:
        return None
    return 100.0 * statistics.mean(float(row[field]) for row in rows)


def cluster_bootstrap(
    rows: list[dict],
    *,
    field: str = "pnl",
    iterations: int = 20_000,
) -> dict:
    by_cluster: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_cluster[row["cluster"]].append(row)
    clusters = sorted(by_cluster)
    if not clusters:
        return {"probability_positive": None, "p05_cents": None, "p95_cents": None}
    seed_text = "|".join(clusters) + f"|{len(rows)}|{field}"
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        sample = [rng.choice(clusters) for _ in clusters]
        values = [float(row[field]) for cluster in sample for row in by_cluster[cluster]]
        draws.append(100.0 * sum(values) / len(values))
    draws.sort()
    return {
        "probability_positive": sum(value > 0.0 for value in draws) / iterations,
        "p05_cents": draws[int(iterations * 0.05)],
        "p95_cents": draws[int(iterations * 0.95)],
    }


def summarize(rows: list[dict], *, with_bootstrap: bool = False) -> dict:
    ordered = sorted(rows, key=lambda row: (row["cluster"], row.get("timestamp", 0)))
    if not ordered:
        return {"n": 0}
    train, validation, test = chronological_splits(ordered)
    by_cluster: dict[str, float] = defaultdict(float)
    for row in ordered:
        by_cluster[row["cluster"]] += float(row["pnl"])
    cluster_values = list(by_cluster.values())
    if len(cluster_values) > 1 and statistics.stdev(cluster_values) > 0:
        t_stat = statistics.mean(cluster_values) / (
            statistics.stdev(cluster_values) / math.sqrt(len(cluster_values))
        )
        p_one_sided = 1.0 - normal_cdf(t_stat)
    else:
        t_stat = None
        p_one_sided = 1.0
    bootstrap = cluster_bootstrap(ordered) if with_bootstrap else {}
    return {
        "n": len(ordered),
        "wins": sum(bool(row["won"]) for row in ordered),
        "losses": sum(not bool(row["won"]) for row in ordered),
        "clusters": len(by_cluster),
        "trades_per_cluster": len(ordered) / len(by_cluster),
        "mean_cents": mean_cents(ordered),
        "stress_1c_mean_cents": mean_cents(ordered, "pnl_stress_1c"),
        "train_mean_cents": mean_cents(train),
        "validation_mean_cents": mean_cents(validation),
        "test_mean_cents": mean_cents(test),
        "cluster_t": t_stat,
        "cluster_p_one_sided": p_one_sided,
        **bootstrap,
    }
