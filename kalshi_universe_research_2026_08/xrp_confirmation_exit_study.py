#!/usr/bin/env python3
"""Test whether an early XRP entry can be de-risked by later confirmation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from candidate_validation import CANDIDATES, max_drawdown, replay
from common import bh_adjust, chronological_splits, cluster_bootstrap, fee, summarize
from fixed_expiry_study import load_series, quote_for_side


def candle_by_remaining(market: dict, candles: list[dict]) -> dict[int, dict]:
    from common import parse_ts

    close_ts = parse_ts(market["close_time"])
    return {
        (close_ts - int(candle["t"])) // 60: candle
        for candle in candles
        if 0 < (close_ts - int(candle["t"])) // 60 <= 14
    }


def favorite_side_at(candle: dict, side: str, phase: str) -> tuple[float, float, float] | None:
    return quote_for_side(candle, side, phase)


def apply_policy(
    baseline: list[dict],
    markets: dict[str, dict],
    candles: dict[str, list[dict]],
    *,
    checkpoint_remaining: int,
    confirmation_mid: float,
) -> list[dict]:
    output = []
    for entry_trade in baseline:
        market = markets[entry_trade["ticker"]]
        by_remaining = candle_by_remaining(market, candles[entry_trade["ticker"]])
        checkpoint = by_remaining.get(checkpoint_remaining)
        next_candle = by_remaining.get(checkpoint_remaining - 1)
        checkpoint_quote = favorite_side_at(checkpoint, entry_trade["side"], "close") if checkpoint else None
        execution_quote = favorite_side_at(next_candle, entry_trade["side"], "open") if next_candle else None

        history_sides = []
        for remaining in range(14, checkpoint_remaining - 1, -1):
            candle = by_remaining.get(remaining)
            if candle is None:
                continue
            yes_bid = candle.get("bc")
            yes_ask = candle.get("ac")
            if yes_bid is None or yes_ask is None:
                continue
            yes_mid = (float(yes_bid) + float(yes_ask)) / 2.0
            history_sides.append("yes" if yes_mid >= 0.5 else "no")

        confirmed = bool(
            checkpoint_quote
            and checkpoint_quote[2] >= confirmation_mid
            and checkpoint_quote[1] - checkpoint_quote[0] <= 0.010001
            and len(history_sides) >= 5
            and set(history_sides) == {entry_trade["side"]}
        )
        row = dict(entry_trade)
        row["confirmed"] = confirmed
        row["checkpoint_remaining"] = checkpoint_remaining
        row["confirmation_mid"] = confirmation_mid
        row["underlying_settlement_won"] = entry_trade["won"]
        if confirmed or checkpoint_quote is None or execution_quote is None:
            row["action"] = "hold"
            row["won"] = row["pnl"] > 0.0
            output.append(row)
            continue

        exit_price = min(checkpoint_quote[0], execution_quote[0])
        entry_price = float(entry_trade["entry"])
        pnl = exit_price - entry_price - fee(entry_price) - fee(exit_price)
        stressed_entry = min(0.999, entry_price + 0.01)
        stressed_exit = max(0.001, exit_price - 0.01)
        stressed_pnl = (
            stressed_exit
            - stressed_entry
            - fee(stressed_entry)
            - fee(stressed_exit)
        )
        row.update(
            {
                "action": "exit",
                "exit_price": exit_price,
                "pnl": pnl,
                "pnl_stress_1c": stressed_pnl,
                "won": pnl > 0.0,
            }
        )
        output.append(row)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../kalshi_crypto15m_2026_07/data"))
    parser.add_argument("--output", type=Path, default=Path("results/xrp_confirmation_exit.json"))
    args = parser.parse_args()
    config = CANDIDATES["xrp_early_80_90"]
    baseline = replay(args.data_dir, config)
    markets, candles = load_series(args.data_dir, config["series"])
    cells = []
    for checkpoint in (8, 7, 6, 5, 4):
        for threshold in (0.90, 0.92, 0.93, 0.95):
            trades = apply_policy(
                baseline,
                markets,
                candles,
                checkpoint_remaining=checkpoint,
                confirmation_mid=threshold,
            )
            train, validation, test = chronological_splits(trades)
            train_metrics = summarize(train)
            oos = validation + test
            oos_metrics = summarize(oos)
            cells.append(
                {
                    "config": {
                        "checkpoint_remaining": checkpoint,
                        "confirmation_mid": threshold,
                    },
                    "metrics": summarize(trades),
                    "selection_train": train_metrics,
                    "validation": summarize(validation),
                    "test": summarize(test),
                    "out_of_sample": oos_metrics,
                    "counts": {
                        "held": sum(row["action"] == "hold" for row in trades),
                        "exited": sum(row["action"] == "exit" for row in trades),
                    },
                    "max_drawdown_dollars_per_contract": max_drawdown(trades, "pnl"),
                    "max_drawdown_stress_dollars_per_contract": max_drawdown(
                        trades, "pnl_stress_1c"
                    ),
                    "_oos": oos,
                }
            )

    adjusted = bh_adjust(
        cell["selection_train"].get("cluster_p_one_sided", 1.0) for cell in cells
    )
    for cell, q_value in zip(cells, adjusted):
        cell["selection_train"]["bh_q"] = q_value
        bootstrap = cluster_bootstrap(cell["_oos"])
        cell["out_of_sample"]["bootstrap"] = bootstrap
        cell["passes_locked_gate"] = bool(
            q_value <= 0.10
            and cell["validation"].get("mean_cents", -1.0) > 0.0
            and cell["test"].get("mean_cents", -1.0) > 0.0
            and cell["out_of_sample"].get("stress_1c_mean_cents", -1.0) > 0.0
            and bootstrap.get("p05_cents", -1.0) > 0.0
        )
        cell.pop("_oos")

    payload = {
        "baseline": summarize(baseline),
        "tested_cells": len(cells),
        "survivors": sum(cell["passes_locked_gate"] for cell in cells),
        "cells": cells,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    passing = [cell for cell in cells if cell["passes_locked_gate"]]
    print(json.dumps(sorted(passing, key=lambda row: -row["out_of_sample"]["mean_cents"]), indent=2))
    print(f"tested {len(cells)} policies; survivors {len(passing)}")


if __name__ == "__main__":
    main()
