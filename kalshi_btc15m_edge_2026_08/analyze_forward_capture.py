#!/usr/bin/env python3
"""Analyze synchronized forward BRTI/ticker captures without placing orders."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import settlement_average_study as settlement
import study


DEFAULT_CAPTURE = Path(__file__).resolve().parent / "data" / "forward_brti_books.jsonl"


def sections(path: Path) -> list[list[dict]]:
    output: list[list[dict]] = []
    current: list[dict] = []
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("type") == "collector_market":
                if current:
                    output.append(current)
                current = [row]
            elif current:
                current.append(row)
    if current:
        output.append(current)
    return output


def analyze(rows: list[dict]) -> dict:
    meta = rows[0]
    strike = float(meta["floor_strike"])
    ticker = None
    values: list[float] = []
    states = []
    latencies = []
    final_average = None
    for row in rows[1:]:
        if row.get("type") == "ticker":
            ticker = row["msg"]
            continue
        if row.get("type") != "cfbenchmarks_value":
            continue
        message = row["msg"]
        values.append(float(json.loads(message["data"])["value"]))
        latencies.append(row["client_received_at_ms"] - int(message["received_at"]))
        window = message.get("last_60s_windowed_average_15min")
        if not window or ticker is None:
            continue
        observed_count = int(window["window_size"])
        remaining = 60 - observed_count
        final_average = float(window["value"])
        history = values[-301:]
        changes = [right - left for left, right in zip(history, history[1:])]
        sigma = statistics.stdev(changes) if len(changes) > 1 else None
        if sigma is None:
            continue
        if remaining:
            expected_total = observed_count * final_average + remaining * values[-1]
            variance_weight = sum(index * index for index in range(1, remaining + 1))
            denominator = max(
                sigma * settlement.SIGMA_INFLATION * math.sqrt(variance_weight), 1e-12
            )
            p_yes = settlement.normal_cdf((expected_total - 60 * strike) / denominator)
        else:
            p_yes = float(final_average >= strike)
        yes_bid = float(ticker.get("yes_bid_dollars") or 0)
        yes_ask = float(ticker.get("yes_ask_dollars") or 1)
        side = "yes" if p_yes >= 0.5 else "no"
        p_win = max(p_yes, 1 - p_yes)
        entry = yes_ask if side == "yes" else 1 - yes_bid
        model_ev = p_win - entry - study.fee(entry) - settlement.EXECUTION_STRESS
        states.append(
            {
                "second": observed_count,
                "side": side,
                "p_win": p_win,
                "entry": entry,
                "spread": yes_ask - yes_bid,
                "model_ev": model_ev,
                "qualifies": p_win >= settlement.MIN_MODEL_PROBABILITY
                and model_ev >= settlement.MIN_MODEL_EV
                and yes_ask - yes_bid <= settlement.MAX_SPREAD,
            }
        )
    return {
        "ticker": meta["ticker"],
        "strike": strike,
        "final_average": final_average,
        "result": None if final_average is None else ("yes" if final_average >= strike else "no"),
        "states": states,
        "latency_median_ms": statistics.median(latencies) if latencies else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    args = parser.parse_args()
    for rows in sections(args.capture):
        result = analyze(rows)
        states = result.pop("states")
        qualifying = [state for state in states if state["qualifies"]]
        print(json.dumps({**result, "seconds": len(states), "qualifying": len(qualifying)}, sort_keys=True))
        if states:
            best = max(states, key=lambda state: state["model_ev"])
            print(
                f"best second={best['second']} side={best['side']} "
                f"p={best['p_win']:.4f} ask={best['entry']:.4f} "
                f"spread={best['spread']:.4f} EV={100*best['model_ev']:+.3f}c"
            )


if __name__ == "__main__":
    main()
