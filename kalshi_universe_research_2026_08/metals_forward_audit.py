#!/usr/bin/env python3
"""Forward-only transfer audit for newly launched 15-minute metal markets."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from candidate_validation import max_drawdown
from common import cluster_bootstrap, contract_pnl, summarize
from fixed_expiry_study import minute_candidate, one_per_event


SERIES = ("KXGOLD15M", "KXSILVER15M", "KXWTI15M")
RULES = {
    "prespecified_crypto_r1": {
        "window": (4, 8),
        "band": (0.93, 0.99),
        "spread": 0.01,
        "observations": 5,
    },
    "earlier_high_95_99": {
        "window": (8, 12),
        "band": (0.95, 0.99),
        "spread": 0.01,
        "observations": 3,
    },
    "earlier_broad_93_99": {
        "window": (8, 12),
        "band": (0.93, 0.99),
        "spread": 0.01,
        "observations": 3,
    },
    "posthoc_early_90_99": {
        "window": (8, 12),
        "band": (0.90, 0.99),
        "spread": 0.01,
        "observations": 3,
    },
    "posthoc_wide_window_90_99": {
        "window": (4, 12),
        "band": (0.90, 0.99),
        "spread": 0.01,
        "observations": 3,
    },
}


def compact_candles(rows: list[dict]) -> list[dict]:
    output = []
    for candle in rows:
        output.append(
            {
                "t": candle["end_period_ts"],
                "bo": (candle.get("yes_bid") or {}).get("open_dollars"),
                "bc": (candle.get("yes_bid") or {}).get("close_dollars"),
                "ao": (candle.get("yes_ask") or {}).get("open_dollars"),
                "ac": (candle.get("yes_ask") or {}).get("close_dollars"),
            }
        )
    return output


def replay(data_dir: Path, series: str, rule: dict) -> list[dict]:
    markets = {row["ticker"]: row for row in json.loads((data_dir / f"markets_{series}.json").read_text())}
    raw_candles = json.loads((data_dir / f"candles_{series}.json").read_text())
    trades = one_per_event(
        [
            trade
            for ticker, market in markets.items()
            if market.get("result") in ("yes", "no")
            and (
                trade := minute_candidate(
                    market,
                    compact_candles(raw_candles.get(ticker, [])),
                    window=rule["window"],
                    band=rule["band"],
                    spread=rule["spread"],
                    observations=rule["observations"],
                )
            )
        ]
    )
    for trade in trades:
        trade["series"] = series
        trade["pnl_stress_2c"] = contract_pnl(min(0.999, trade["entry"] + 0.02), trade["won"])
    return trades


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    base = summarize(trades)
    return {
        **base,
        "stress_2c_mean_cents": 100.0 * statistics.mean(row["pnl_stress_2c"] for row in trades),
        "mean_entry_cents": 100.0 * statistics.mean(row["entry"] for row in trades),
        "max_drawdown_dollars_per_contract": max_drawdown(
            sorted(trades, key=lambda row: row["timestamp"]), "pnl"
        ),
        "bootstrap": cluster_bootstrap(trades, iterations=10_000),
    }


def cap_one_per_window(trades: list[dict]) -> list[dict]:
    by_timestamp: dict[int, list[dict]] = defaultdict(list)
    for trade in trades:
        by_timestamp[trade["timestamp"]].append(trade)
    return [
        max(rows, key=lambda row: (row["midpoint"], -row["spread"], row["series"]))
        for rows in by_timestamp.values()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("/tmp/kalshi_metal90_99_20260807_0735"),
    )
    parser.add_argument("--output", type=Path, default=Path("results/metals_forward_audit.json"))
    args = parser.parse_args()
    payload = {"rules": {}}
    for name, rule in RULES.items():
        by_series = {series: replay(args.data_dir, series, rule) for series in SERIES}
        combined = [trade for rows in by_series.values() for trade in rows]
        payload["rules"][name] = {
            "config": rule,
            "by_series": {series: metrics(rows) for series, rows in by_series.items()},
            "all_positions": metrics(combined),
            "one_position_per_window": metrics(cap_one_per_window(combined)),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
