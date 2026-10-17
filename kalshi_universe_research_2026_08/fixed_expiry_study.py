#!/usr/bin/env python3
"""Event-deduplicated search for reusable fixed-expiry price-path effects."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from common import (
    bh_adjust,
    cluster_bootstrap,
    contract_pnl,
    mean_cents,
    parse_ts,
    summarize,
    utc_day,
)


HOURLY_SERIES = {
    "KXAAAGASD": "gas_daily",
    "KXWTI": "wti_daily",
    "KXBRENTD": "brent_daily",
    "KXGOLDD": "gold_daily",
    "KXUSDJPY": "fx_daily",
    "KXSPOTIFYD": "entertainment_daily",
    "KXINX": "spx_daily_ladder",
    "KXNASDAQ100": "nasdaq_daily_ladder",
    "KXINXDUD": "spx_daily_binary",
    "KXNASDAQDUD": "nasdaq_daily_binary",
}
MINUTE_SERIES = {
    "KXBTC15M": "btc_15m",
    "KXETH15M": "eth_15m",
    "KXSOL15M": "sol_15m",
    "KXXRP15M": "xrp_15m",
    "KXDOGE15M": "doge_15m",
    "KXBNB15M": "bnb_15m",
    "KXHYPE15M": "hype_15m",
    "KXNEAR15M": "near_15m",
    "KXZEC15M": "zec_15m",
}
BANDS = ((0.80, 0.90), (0.90, 0.95), (0.93, 0.99), (0.95, 0.99))
PRICE_EPSILON = 1e-9


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quote(candle: dict, phase: str = "close") -> tuple[str, float, float, float] | None:
    suffix = "c" if phase == "close" else "o"
    bid_raw = candle.get(f"b{suffix}")
    ask_raw = candle.get(f"a{suffix}")
    if bid_raw is None:
        bid_raw = candle.get("b")
    if ask_raw is None:
        ask_raw = candle.get("a")
    if bid_raw is None or ask_raw is None:
        return None
    bid = float(bid_raw)
    ask = float(ask_raw)
    if not 0.0 <= bid <= ask <= 1.0:
        return None
    midpoint = (bid + ask) / 2.0
    if midpoint >= 0.5:
        return "yes", bid, ask, midpoint
    return "no", 1.0 - ask, 1.0 - bid, 1.0 - midpoint


def quote_for_side(candle: dict, side: str, phase: str = "close") -> tuple[float, float, float] | None:
    suffix = "c" if phase == "close" else "o"
    bid_raw = candle.get(f"b{suffix}")
    ask_raw = candle.get(f"a{suffix}")
    if bid_raw is None:
        bid_raw = candle.get("b")
    if ask_raw is None:
        ask_raw = candle.get("a")
    if bid_raw is None or ask_raw is None:
        return None
    yes_bid = float(bid_raw)
    yes_ask = float(ask_raw)
    if not 0.0 <= yes_bid <= yes_ask <= 1.0:
        return None
    bid, ask = (yes_bid, yes_ask) if side == "yes" else (1.0 - yes_ask, 1.0 - yes_bid)
    return bid, ask, (bid + ask) / 2.0


def load_series(data_dir: Path, series: str) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    markets = {
        row["ticker"]: row
        for row in load_jsonl(data_dir / f"markets_{series}.jsonl")
        if row.get("result") in ("yes", "no")
    }
    candles = {
        row["ticker"]: sorted(row.get("candles", []), key=lambda item: int(item["t"]))
        for row in load_jsonl(data_dir / f"candles_{series}.jsonl")
    }
    return markets, candles


def nearest_before(candles: list[dict], target: int, maximum_age: int) -> dict | None:
    choices = [row for row in candles if int(row["t"]) <= target]
    if not choices:
        return None
    selected = choices[-1]
    return selected if target - int(selected["t"]) <= maximum_age else None


def first_after(candles: list[dict], timestamp: int, maximum_wait: int) -> dict | None:
    for row in candles:
        row_ts = int(row["t"])
        if timestamp < row_ts <= timestamp + maximum_wait:
            return row
    return None


def hourly_candidate(
    market: dict,
    candles: list[dict],
    *,
    hours: int,
    band: tuple[float, float],
    spread: float,
    hold: int,
    mechanism: str,
) -> dict | None:
    close_ts = parse_ts(market["close_time"])
    target = close_ts - hours * 3600
    signal = nearest_before(candles, target, 5400)
    if signal is None:
        return None
    current = quote(signal)
    if current is None:
        return None
    side, bid, ask, midpoint = current
    if (
        not band[0] <= midpoint < band[1]
        or ask - bid > spread + PRICE_EPSILON
        or ask > 0.99
    ):
        return None
    history = []
    for offset in range(hold - 1, -1, -1):
        prior = nearest_before(candles, int(signal["t"]) - offset * 3600, 1200)
        prior_quote = quote(prior) if prior else None
        if prior_quote is None or prior_quote[0] != side:
            return None
        history.append(prior_quote[3])
    if mechanism == "strengthening" and midpoint - history[0] < 0.05:
        return None
    if mechanism == "drawdown" and max(history) - midpoint < 0.05:
        return None
    execution = first_after(candles, int(signal["t"]), 5400)
    execution_quote = quote_for_side(execution, side, "open") if execution else None
    if execution_quote is None:
        return None
    entry = max(ask, execution_quote[1])
    if entry > 0.99:
        return None
    won = market["result"] == side
    return {
        "ticker": market["ticker"],
        "event": market.get("event_ticker") or market["ticker"],
        "timestamp": int(execution["t"]),
        "side": side,
        "entry": entry,
        "midpoint": midpoint,
        "spread": ask - bid,
        "won": won,
        "pnl": contract_pnl(entry, won),
        "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
    }


def minute_candidate(
    market: dict,
    candles: list[dict],
    *,
    window: tuple[int, int],
    band: tuple[float, float],
    spread: float,
    observations: int,
) -> dict | None:
    """Return the first executable never-flipped favorite in the window.

    ``observations`` is a minimum sample count, not a rolling hold length.
    Every observed favorite from minute 14 through the signal must be the
    same side. This full-path condition is intentionally stricter than merely
    requiring the latest N closes to agree.
    """
    close_ts = parse_ts(market["close_time"])
    by_remaining = {
        (close_ts - int(row["t"])) // 60: row
        for row in candles
        if 0 < (close_ts - int(row["t"])) // 60 <= 14
    }
    path = {
        remaining: quote(row)
        for remaining, row in by_remaining.items()
        if quote(row) is not None
    }
    for remaining in range(window[1], window[0] - 1, -1):
        current = path.get(remaining)
        if current is None:
            continue
        side, bid, ask, midpoint = current
        if (
            not band[0] <= midpoint < band[1]
            or ask - bid > spread + PRICE_EPSILON
            or ask > 0.99
        ):
            continue
        observed = [
            path[minute][0]
            for minute in range(14, remaining - 1, -1)
            if minute in path
        ]
        if len(observed) < observations or len(set(observed)) != 1:
            continue
        execution = by_remaining.get(remaining - 1)
        execution_quote = quote_for_side(execution, side, "open") if execution else None
        if execution_quote is None:
            continue
        entry = max(ask, execution_quote[1])
        if entry > 0.99:
            continue
        won = market["result"] == side
        return {
            "ticker": market["ticker"],
            "event": market.get("event_ticker") or market["ticker"],
            "timestamp": int(execution["t"]),
            "side": side,
            "entry": entry,
            "midpoint": midpoint,
            "spread": ask - bid,
            "won": won,
            "pnl": contract_pnl(entry, won),
            "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
        }
    return None


def one_per_event(candidates: list[dict]) -> list[dict]:
    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        by_event[row["event"]].append(row)
    selected = []
    for rows in by_event.values():
        choice = max(rows, key=lambda row: (row["midpoint"], -row["spread"], row["ticker"]))
        choice = dict(choice)
        choice["cluster"] = utc_day(choice["timestamp"])
        selected.append(choice)
    return selected


def study_hourly(data_dir: Path) -> list[dict]:
    results = []
    for series, family in HOURLY_SERIES.items():
        markets, candles = load_series(data_dir, series)
        for hours in (18, 12, 10, 8, 6, 4, 3, 2, 1):
            for band in BANDS:
                for spread in (0.02, 0.04):
                    for hold in (1, 2, 3):
                        for mechanism in ("stable", "strengthening", "drawdown"):
                            candidates = [
                                trade
                                for ticker, market in markets.items()
                                if (
                                    trade := hourly_candidate(
                                        market,
                                        candles.get(ticker, []),
                                        hours=hours,
                                        band=band,
                                        spread=spread,
                                        hold=hold,
                                        mechanism=mechanism,
                                    )
                                )
                            ]
                            trades = one_per_event(candidates)
                            results.append(
                                {
                                    "series": series,
                                    "family": family,
                                    "mechanism": mechanism,
                                    "config": {
                                        "hours": hours,
                                        "band": band,
                                        "spread": spread,
                                        "hold": hold,
                                    },
                                    "metrics": summarize(trades),
                                    "_trades": trades,
                                }
                            )
    return results


def study_minute(data_dir: Path) -> list[dict]:
    results = []
    for series, family in MINUTE_SERIES.items():
        markets, candles = load_series(data_dir, series)
        for window in ((1, 4), (4, 8), (4, 12), (8, 12)):
            for band in BANDS:
                for spread in (0.01, 0.02):
                    for observations in (3, 5, 8):
                        trades = one_per_event(
                            [
                                trade
                                for ticker, market in markets.items()
                                if (
                                    trade := minute_candidate(
                                        market,
                                        candles.get(ticker, []),
                                        window=window,
                                        band=band,
                                        spread=spread,
                                        observations=observations,
                                    )
                                )
                            ]
                        )
                        results.append(
                            {
                                "series": series,
                                "family": family,
                                "mechanism": "unbroken_favorite",
                                "config": {
                                    "window": window,
                                    "band": band,
                                    "spread": spread,
                                    "observations": observations,
                                },
                                "metrics": summarize(trades),
                                "_trades": trades,
                            }
                        )
    return results


def add_multiplicity_and_verdict(results: list[dict]) -> None:
    # Configuration discovery and multiplicity correction must not see the
    # validation or test periods. Otherwise the nominal holdout is already
    # contaminated by the model-selection step.
    family_clusters: dict[str, set[str]] = defaultdict(set)
    for row in results:
        family_clusters[row["family"]].update(
            trade["cluster"] for trade in row["_trades"]
        )
    family_split_sets = {}
    for family, cluster_values in family_clusters.items():
        clusters = sorted(cluster_values)
        first_cut = max(1, int(len(clusters) * 0.50))
        second_cut = max(first_cut + 1, int(len(clusters) * 0.75))
        family_split_sets[family] = (
            set(clusters[:first_cut]),
            set(clusters[first_cut:second_cut]),
            set(clusters[second_cut:]),
        )

    eligible = []
    for row in results:
        train_clusters, validation_clusters, test_clusters = family_split_sets[
            row["family"]
        ]
        train = [
            trade for trade in row["_trades"] if trade["cluster"] in train_clusters
        ]
        validation = [
            trade
            for trade in row["_trades"]
            if trade["cluster"] in validation_clusters
        ]
        test = [
            trade for trade in row["_trades"] if trade["cluster"] in test_clusters
        ]
        out_of_sample = validation + test
        train_metrics = summarize(train)
        oos_metrics = summarize(out_of_sample)
        row["_partitions"] = {
            "train": train,
            "validation": validation,
            "test": test,
            "out_of_sample": out_of_sample,
        }
        row["metrics"].update(
            {
                "selection_train_n": train_metrics.get("n", 0),
                "selection_train_clusters": train_metrics.get("clusters", 0),
                "selection_train_mean_cents": train_metrics.get("mean_cents"),
                "selection_train_cluster_p_one_sided": train_metrics.get(
                    "cluster_p_one_sided", 1.0
                ),
                "train_mean_cents": mean_cents(train),
                "validation_mean_cents": mean_cents(validation),
                "test_mean_cents": mean_cents(test),
                "oos_n": oos_metrics.get("n", 0),
                "oos_clusters": oos_metrics.get("clusters", 0),
                "oos_mean_cents": oos_metrics.get("mean_cents"),
                "oos_stress_1c_mean_cents": oos_metrics.get("stress_1c_mean_cents"),
            }
        )
        if train_metrics.get("n", 0) >= 20:
            eligible.append(row)

    adjusted = bh_adjust(
        row["metrics"]["selection_train_cluster_p_one_sided"] for row in eligible
    )
    for row, q_value in zip(eligible, adjusted):
        metrics = row["metrics"]
        metrics["selection_train_bh_q"] = q_value
        preliminary = bool(
            metrics["n"] >= 50
            and metrics["selection_train_n"] >= 25
            and metrics.get("selection_train_mean_cents", -1.0) > 0.0
            and q_value <= 0.10
            and metrics.get("validation_mean_cents", -1.0) > 0.0
            and metrics.get("test_mean_cents", -1.0) > 0.0
            and metrics.get("oos_stress_1c_mean_cents", -1.0) > 0.0
        )
        if preliminary:
            oos_bootstrap = cluster_bootstrap(row["_partitions"]["out_of_sample"])
            metrics.update(
                {
                    "oos_probability_positive": oos_bootstrap["probability_positive"],
                    "oos_p05_cents": oos_bootstrap["p05_cents"],
                    "oos_p95_cents": oos_bootstrap["p95_cents"],
                }
            )
        row["passes_historical_gate"] = bool(
            preliminary and metrics.get("oos_p05_cents", -1.0) > 0.0
        )
    for row in results:
        row.setdefault("passes_historical_gate", False)
        row.pop("_trades", None)
        row.pop("_partitions", None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timely-dir", type=Path, default=Path("../kalshi_timely_2026_07/data"))
    parser.add_argument("--crypto-dir", type=Path, default=Path("../kalshi_crypto15m_2026_07/data"))
    parser.add_argument("--output", type=Path, default=Path("results/fixed_expiry_surface.json"))
    args = parser.parse_args()
    results = study_hourly(args.timely_dir) + study_minute(args.crypto_dir)
    add_multiplicity_and_verdict(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    survivors = [row for row in results if row.get("passes_historical_gate")]
    print(json.dumps(sorted(survivors, key=lambda row: -row["metrics"]["mean_cents"]), indent=2))
    print(f"tested {len(results)} cells; historical survivors {len(survivors)}")


if __name__ == "__main__":
    main()
