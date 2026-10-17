from __future__ import annotations

import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"
DATA.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)


def get_json(path: str, params: dict[str, Any] | None = None, retries: int = 8) -> dict[str, Any]:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-three-lane-research/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            code = getattr(exc, "code", None)
            if attempt == retries - 1 or code not in (429, 500, 502, 503, 504, None):
                raise
            time.sleep(min(30.0, 1.25 * (2**attempt)))
    raise RuntimeError("unreachable")


def paginate(path: str, params: dict[str, Any], key: str, limit: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        query = dict(params)
        query["limit"] = limit
        if cursor:
            query["cursor"] = cursor
        payload = get_json(path, query)
        rows.extend(payload.get(key) or [])
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return rows


def fee(price: float, multiplier: float = 1.0) -> float:
    return 0.07 * multiplier * price * (1.0 - price)


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quote_from_market(market: dict[str, Any], side: str) -> tuple[float, float] | None:
    bid = as_float(market.get(f"{side}_bid_dollars"))
    ask = as_float(market.get(f"{side}_ask_dollars"))
    if bid is None or ask is None or ask <= 0 or ask > 1 or bid < 0 or bid > ask:
        return None
    return bid, ask


def side_quote_from_candle(candle: dict[str, Any], side: str) -> tuple[float, float] | None:
    yes_bid_payload = candle.get("yes_bid") or {}
    yes_ask_payload = candle.get("yes_ask") or {}
    yes_bid = as_float(yes_bid_payload.get("close_dollars", yes_bid_payload.get("close")))
    yes_ask = as_float(yes_ask_payload.get("close_dollars", yes_ask_payload.get("close")))
    if yes_bid is None or yes_ask is None or yes_ask <= 0 or yes_ask > 1 or yes_bid < 0:
        return None
    if side == "yes":
        return yes_bid, yes_ask
    return 1.0 - yes_ask, 1.0 - yes_bid


def summarize(values: Iterable[float]) -> dict[str, float | int]:
    xs = list(values)
    if not xs:
        return {"n": 0}
    mean = sum(xs) / len(xs)
    variance = sum((x - mean) ** 2 for x in xs) / max(1, len(xs) - 1)
    return {
        "n": len(xs),
        "mean": mean,
        "total": sum(xs),
        "stdev": math.sqrt(variance),
        "win_rate": sum(x > 0 for x in xs) / len(xs),
    }


def cluster_bootstrap(
    rows: Iterable[dict[str, Any]],
    *,
    value_key: str,
    cluster_key: str,
    iterations: int = 5000,
    seed: int = 20260807,
) -> dict[str, float | int]:
    grouped: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        cluster = str(row[cluster_key])
        grouped[cluster] += float(row[value_key])
        counts[cluster] += 1
    clusters = sorted(grouped)
    if not clusters:
        return {"clusters": 0}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iterations):
        sampled = [rng.choice(clusters) for _ in clusters]
        total = sum(grouped[key] for key in sampled)
        count = sum(counts[key] for key in sampled)
        means.append(total / max(1, count))
    means.sort()
    return {
        "clusters": len(clusters),
        "probability_mean_positive": sum(value > 0 for value in means) / len(means),
        "p05": means[int(0.05 * (len(means) - 1))],
        "median": means[len(means) // 2],
        "p95": means[int(0.95 * (len(means) - 1))],
    }


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

