#!/usr/bin/env python3
"""Quantify unseen-loss risk for candidate samples containing no losses."""

from __future__ import annotations

import json
import math
from pathlib import Path


RESULTS = Path(__file__).resolve().parent / "results"


def implied_entry_from_all_win_pnl(mean_pnl: float) -> float:
    # mean_pnl = 1 - p - 0.07*p*(1-p)
    a, b, c = 0.07, -1.07, 1.0 - mean_pnl
    return (-b - math.sqrt((b * b) - (4.0 * a * c))) / (2.0 * a)


def summarize(name: str, n: int, mean_cents: float) -> dict:
    entry = implied_entry_from_all_win_pnl(mean_cents / 100.0)
    fee = 0.07 * entry * (1.0 - entry)
    breakeven = entry + fee
    return {
        "candidate": name,
        "all_win_sample_n": n,
        "implied_mean_entry": entry,
        "breakeven_win_probability": breakeven,
        # Exact one-sided 95% Clopper-Pearson lower bound for n successes.
        "one_sided_95pct_win_rate_lower_bound": 0.05 ** (1.0 / n),
        # Beta(1,1) prior, Beta(n+1,1) posterior after n wins and no losses.
        "uniform_prior_posterior_probability_edge_positive": 1.0
        - (breakeven ** (n + 1)),
        "wins_needed_to_offset_one_loss_at_mean_economics": entry
        / (1.0 - entry - fee),
    }


def main() -> None:
    sports = json.loads((RESULTS / "sports_liveness_audit.json").read_text())
    wnba = next(
        row["metrics"]
        for row in sports["rules"]
        if row["family"] == "KXWNBAGAME" and row["minimum_volume_15m"] == 200.0
    )

    fixed = json.loads((RESULTS / "fixed_expiry_surface_nested.json").read_text())
    gas = next(
        row["metrics"]
        for row in fixed
        if row["family"] == "gas_daily"
        and row["mechanism"] == "stable"
        and row["config"]
        == {"band": [0.93, 0.99], "hold": 1, "hours": 8, "spread": 0.04}
    )
    zec = next(
        row["metrics"]
        for row in fixed
        if row["family"] == "zec_15m"
        and row["mechanism"] == "unbroken_favorite"
        and row["config"]
        == {"band": [0.95, 0.99], "observations": 3, "spread": 0.01, "window": [4, 12]}
    )

    payload = {
        "method": {
            "fee": "0.07*p*(1-p)",
            "confidence_bound": "exact one-sided Clopper-Pearson",
            "posterior": "Beta(1,1) prior updated by n wins and zero losses",
        },
        "candidates": [
            summarize("gas_93_99_final_8h", gas["n"], gas["mean_cents"]),
            summarize("wnba_97_99_hold_5", wnba["n"], wnba["mean_cents"]),
            summarize("zec_95_99", zec["n"], zec["mean_cents"]),
        ],
    }
    path = RESULTS / "zero_loss_uncertainty.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
