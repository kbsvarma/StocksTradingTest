#!/usr/bin/env python3
"""Stress test the incremental rem=6 settlement-value candidate."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

import backtest as bt
import math_strategy_search as search
import state_value_study as study
import stabilized_flip_study as stabilized


FEATURE_NAMES = (
    "logit_mid",
    "logit_mid_sq",
    "drift_1m",
    "drift_2m",
    "drift_3m",
    "path_volatility",
    "flip_count",
    "persistence",
    "spread",
    "relative_volume",
    "hour_sin",
    "hour_cos",
    "asset_btc",
    "asset_eth",
    "asset_sol",
)


def _bootstrap_days(
    trades: list[study.PolicyTrade],
    iterations: int = 20_000,
) -> tuple[float, float, float]:
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade.ts // 86400].append(trade.pnl)
    days = list(grouped.values())
    if not days:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(91)
    means = []
    for _ in range(iterations):
        sample = [rng.choice(days) for _ in days]
        means.append(
            statistics.mean(value for day in sample for value in day)
        )
    means.sort()
    return (
        100 * means[int(0.025 * iterations)],
        100 * means[int(0.975 * iterations)],
        sum(value > 0 for value in means) / iterations,
    )


def _segment(
    label: str,
    trades: list[study.PolicyTrade],
    fold_ranges: list[tuple[int, int]],
) -> None:
    study.report(label, trades)
    if not trades:
        return
    values = []
    for start, end in fold_ranges:
        part = [trade for trade in trades if start <= trade.ts < end]
        values.append(
            100 * statistics.mean(trade.pnl for trade in part)
            if part else float("nan")
        )
    print(
        f"{'':42} fold EVc="
        + ", ".join("nan" if math.isnan(value) else f"{value:+.3f}" for value in values)
    )


def _coefficient_report(model, train_days: int) -> None:
    scaler = model.named_steps["standardscaler"]
    logistic = model.named_steps["logisticregression"]
    standardized = logistic.coef_[0]
    raw = standardized / scaler.scale_
    ranked = sorted(
        zip(FEATURE_NAMES, raw),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    print(
        f"  train={train_days:2d}d top coefficients: "
        + ", ".join(f"{name}={value:+.3f}" for name, value in ranked[:7])
    )


def main() -> None:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))
    markets.sort(key=lambda market: market["close_ts"])
    rows = study.build_rows(markets, 6)
    r1_tickers = {
        signal["market"]["ticker"]
        for signal in search.base_unbroken(markets, rem_low=6)
    }
    stab_tickers = {
        trade["ticker"]
        for trade in stabilized.run(markets, 5, 0.93, 0.95)
    }
    start = min(row.ts for row in rows)
    fold_ranges = []
    all_incremental = []
    all_selected = []

    print(
        f"Loaded {len(rows)} rem=6 rows; "
        f"hold floor={100*study.HOLD_EDGE_FLOOR:.2f}c; "
        f"existing stab tickers={len(stab_tickers)}\n"
    )
    print("=== Strict expanding walk-forward folds ===")
    for train_days in (30, 40, 50, 60):
        train_end = start + train_days * 86400
        test_end = train_end + 10 * 86400
        train = [row for row in rows if row.ts < train_end]
        test = [row for row in rows if train_end <= row.ts < test_end]
        if len(train) < 2000 or not test:
            continue
        model = study._fit_settlement(train)
        trades, metrics = study._evaluate_policy(train, test, allow_scalps=False)
        incremental = [trade for trade in trades if trade.ticker not in r1_tickers]
        fold_ranges.append((train_end, test_end))
        all_selected.extend(trades)
        all_incremental.extend(incremental)
        print(
            f"\nfold train={train_days:2d}d "
            f"Brier={metrics['brier_model']:.5f}/"
            f"{metrics['brier_market']:.5f}"
        )
        study.report("  all selected", trades)
        study.report("  incremental only", incremental)
        study.report(
            "  incremental cap1/interval",
            study.cap_one_per_interval(incremental),
        )
        _coefficient_report(model, train_days)

    print("\n=== Aggregate incremental stress ===")
    all_incremental.sort(key=lambda trade: trade.ts)
    _segment("all incremental", all_incremental, fold_ranges)
    _segment(
        "cap1 per interval",
        study.cap_one_per_interval(all_incremental),
        fold_ranges,
    )
    _segment(
        "flipped",
        [trade for trade in all_incremental if trade.flips > 0],
        fold_ranges,
    )
    flipped_beyond_stab = [
        trade for trade in all_incremental
        if trade.flips > 0 and trade.ticker not in stab_tickers
    ]
    _segment("flipped beyond deployed stab", flipped_beyond_stab, fold_ranges)
    _segment(
        "unbroken",
        [trade for trade in all_incremental if trade.flips == 0],
        fold_ranges,
    )

    row_by_ticker = {row.ticker: row for row in rows}
    print("\nFlipped candidate by current-leader persistence")
    for low, high in ((1, 3), (3, 5), (5, 7), (7, 99)):
        _segment(
            f"persistence {low}-{high - 1 if high < 99 else '+'}",
            [
                trade for trade in flipped_beyond_stab
                if low <= row_by_ticker[trade.ticker].persistence < high
            ],
            fold_ranges,
        )
    print("\nFlipped candidate by observed flip count")
    for flip_count in (1, 2, 3):
        _segment(
            f"flips {'3+' if flip_count == 3 else flip_count}",
            [
                trade for trade in flipped_beyond_stab
                if (
                    row_by_ticker[trade.ticker].flips == flip_count
                    if flip_count < 3
                    else row_by_ticker[trade.ticker].flips >= 3
                )
            ],
            fold_ranges,
        )

    print("\nBy entry price")
    for low, high in (
        (0.70, 0.80),
        (0.80, 0.90),
        (0.90, 0.93),
        (0.93, 0.95),
        (0.95, 1.00),
    ):
        _segment(
            f"entry {low:.2f}-{high:.2f}",
            [
                trade for trade in all_incremental
                if low <= trade.entry < high
            ],
            fold_ranges,
        )

    print("\nBy predicted edge")
    for low, high in (
        (0.015, 0.020),
        (0.020, 0.030),
        (0.030, 0.050),
        (0.050, 1.0),
    ):
        _segment(
            f"pred edge {100*low:.1f}-{100*high:.1f}c",
            [
                trade for trade in all_incremental
                if low < trade.predicted_edge <= high
            ],
            fold_ranges,
        )

    print("\nBy asset")
    for series in bt.SERIES:
        _segment(
            series,
            [trade for trade in all_incremental if trade.series == series],
            fold_ranges,
        )

    print("\nBy side")
    for side in ("yes", "no"):
        _segment(
            side,
            [trade for trade in all_incremental if trade.side == side],
            fold_ranges,
        )

    low, high, probability = _bootstrap_days(all_incremental)
    print(
        f"\nDay bootstrap: 95% CI=[{low:+.3f},{high:+.3f}]c "
        f"P(EV>0)={probability:.1%}"
    )
    low, high, probability = _bootstrap_days(flipped_beyond_stab)
    print(
        f"Flipped-beyond-stab bootstrap: 95% CI=[{low:+.3f},{high:+.3f}]c "
        f"P(EV>0)={probability:.1%}"
    )
    print("Extra entry-slippage sensitivity")
    for cents in (0.00, 0.25, 0.50, 0.75, 1.00, 1.50):
        value = statistics.mean(
            trade.pnl - cents / 100 for trade in all_incremental
        )
        print(f"  +{cents:.2f}c -> {100*value:+.3f}c")


if __name__ == "__main__":
    main()
