#!/usr/bin/env python3
"""Locked-candidate validation and overlap analysis for fixed-expiry leads."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from common import cluster_bootstrap, contract_pnl, summarize
from fixed_expiry_study import load_series, minute_candidate, one_per_event


CANDIDATES = {
    "xrp_early_80_90": {
        "series": "KXXRP15M",
        "window": (8, 12),
        "band": (0.80, 0.90),
        "spread": 0.01,
        "observations": 3,
    },
    "xrp_mid_90_95": {
        "series": "KXXRP15M",
        "window": (4, 8),
        "band": (0.90, 0.95),
        "spread": 0.01,
        "observations": 8,
    },
    "xrp_deployed_shape_93_99": {
        "series": "KXXRP15M",
        "window": (4, 8),
        "band": (0.93, 0.99),
        "spread": 0.01,
        "observations": 5,
    },
    "zec_95_99": {
        "series": "KXZEC15M",
        "window": (4, 8),
        "band": (0.95, 0.99),
        "spread": 0.01,
        "observations": 8,
    },
}


def replay(data_dir: Path, config: dict) -> list[dict]:
    markets, candles = load_series(data_dir, config["series"])
    trades = one_per_event(
        [
            trade
            for ticker, market in markets.items()
            if (
                trade := minute_candidate(
                    market,
                    candles.get(ticker, []),
                    window=config["window"],
                    band=config["band"],
                    spread=config["spread"],
                    observations=config["observations"],
                )
            )
        ]
    )
    for trade in trades:
        trade["pnl_stress_2c"] = contract_pnl(min(0.999, trade["entry"] + 0.02), trade["won"])
    return sorted(trades, key=lambda row: (row["timestamp"], row["ticker"]))


def max_drawdown(trades: list[dict], field: str) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in trades:
        equity += float(trade[field])
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def tail_report(trades: list[dict]) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        by_day[trade["cluster"]].append(trade)
    day_pnls = {
        day: sum(float(trade["pnl"]) for trade in rows)
        for day, rows in by_day.items()
    }
    ordered_days = sorted(day_pnls)
    losses = [trade for trade in trades if not trade["won"]]
    loss_days = sorted({trade["cluster"] for trade in losses})
    intervals = []
    for left, right in zip(loss_days, loss_days[1:]):
        intervals.append((ordered_days.index(right) - ordered_days.index(left)))
    return {
        "calendar_days": len(ordered_days),
        "trades_per_day": len(trades) / len(ordered_days) if ordered_days else None,
        "losing_trade_days": sum(value < 0 for value in day_pnls.values()),
        "fraction_losing_trade_days": (
            sum(value < 0 for value in day_pnls.values()) / len(day_pnls) if day_pnls else None
        ),
        "worst_day_dollars_per_contract": min(day_pnls.values()) if day_pnls else None,
        "best_day_dollars_per_contract": max(day_pnls.values()) if day_pnls else None,
        "median_days_between_loss_days": statistics.median(intervals) if intervals else None,
        "max_drawdown_dollars_per_contract": max_drawdown(trades, "pnl"),
        "max_drawdown_1c_stress_dollars_per_contract": max_drawdown(
            trades, "pnl_stress_1c"
        ),
    }


def report(trades: list[dict]) -> dict:
    metrics = summarize(trades)
    bootstrap = cluster_bootstrap(trades)
    stress_2c = 100.0 * statistics.mean(trade["pnl_stress_2c"] for trade in trades)
    entries = [float(trade["entry"]) for trade in trades]
    return {
        **metrics,
        "stress_2c_mean_cents": stress_2c,
        "mean_entry_cents": 100.0 * statistics.mean(entries),
        "median_entry_cents": 100.0 * statistics.median(entries),
        "bootstrap": bootstrap,
        "tail": tail_report(trades),
    }


def overlap(left: list[dict], right: list[dict]) -> dict:
    left_by_event = {trade["event"]: trade for trade in left}
    right_by_event = {trade["event"]: trade for trade in right}
    common = sorted(set(left_by_event) & set(right_by_event))
    early_only = [trade for event, trade in left_by_event.items() if event not in right_by_event]
    both_early = [left_by_event[event] for event in common]
    both_late = [right_by_event[event] for event in common]
    return {
        "left_n": len(left),
        "right_n": len(right),
        "shared_events": len(common),
        "share_of_left_also_right": len(common) / len(left) if left else None,
        "left_only_n": len(early_only),
        "left_only_mean_cents": (
            100.0 * statistics.mean(trade["pnl"] for trade in early_only)
            if early_only
            else None
        ),
        "shared_left_mean_cents": (
            100.0 * statistics.mean(trade["pnl"] for trade in both_early)
            if common
            else None
        ),
        "shared_right_mean_cents": (
            100.0 * statistics.mean(trade["pnl"] for trade in both_late)
            if common
            else None
        ),
        "same_terminal_side_on_shared": (
            sum(left_by_event[event]["side"] == right_by_event[event]["side"] for event in common)
            / len(common)
            if common
            else None
        ),
    }


def daily_correlation(left: list[dict], right: list[dict]) -> float | None:
    def totals(rows: list[dict]) -> dict[str, float]:
        output: dict[str, float] = defaultdict(float)
        for row in rows:
            output[row["cluster"]] += float(row["pnl"])
        return output

    left_days = totals(left)
    right_days = totals(right)
    days = sorted(set(left_days) & set(right_days))
    if len(days) < 3:
        return None
    x = [left_days[day] for day in days]
    y = [right_days[day] for day in days]
    if statistics.stdev(x) == 0 or statistics.stdev(y) == 0:
        return None
    return statistics.correlation(x, y) if hasattr(statistics, "correlation") else (
        sum((a - statistics.mean(x)) * (b - statistics.mean(y)) for a, b in zip(x, y))
        / math.sqrt(
            sum((a - statistics.mean(x)) ** 2 for a in x)
            * sum((b - statistics.mean(y)) ** 2 for b in y)
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../kalshi_crypto15m_2026_07/data"))
    parser.add_argument("--output", type=Path, default=Path("results/candidate_validation.json"))
    args = parser.parse_args()
    trades = {name: replay(args.data_dir, config) for name, config in CANDIDATES.items()}
    payload = {
        "candidates": {
            name: {"config": CANDIDATES[name], "metrics": report(rows)}
            for name, rows in trades.items()
        },
        "overlap": {
            "xrp_early_vs_deployed_shape": overlap(
                trades["xrp_early_80_90"], trades["xrp_deployed_shape_93_99"]
            ),
            "xrp_mid_vs_deployed_shape": overlap(
                trades["xrp_mid_90_95"], trades["xrp_deployed_shape_93_99"]
            ),
        },
        "daily_correlations": {
            "xrp_early_vs_xrp_deployed_shape": daily_correlation(
                trades["xrp_early_80_90"], trades["xrp_deployed_shape_93_99"]
            ),
            "xrp_early_vs_zec": daily_correlation(
                trades["xrp_early_80_90"], trades["zec_95_99"]
            ),
            "xrp_deployed_shape_vs_zec": daily_correlation(
                trades["xrp_deployed_shape_93_99"], trades["zec_95_99"]
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
