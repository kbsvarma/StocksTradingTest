#!/usr/bin/env python3
"""Summarize the current recurring Kalshi universe without strategy claims."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load(path: Path) -> list[dict]:
    raw = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    by_ticker: dict[str, dict] = {}
    for row in raw:
        ticker = row["ticker"] if row["ticker"].startswith("KX") else f"KX{row['ticker']}"
        row = {**row, "ticker": ticker}
        previous = by_ticker.get(ticker)
        if previous is None or (
            "error" not in previous,
            int(previous.get("recent_settled_events") or 0),
        ) < (
            "error" not in row,
            int(row.get("recent_settled_events") or 0),
        ):
            by_ticker[ticker] = row
    return list(by_ticker.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/inventory_summary.json"))
    args = parser.parse_args()
    rows = load(args.inventory)
    usable = [row for row in rows if "error" not in row]
    repeatable = [row for row in usable if row.get("recent_settled_events", 0) >= 20]
    liquid = [
        row
        for row in repeatable
        if float(row.get("median_market_volume") or 0.0) >= 100.0
    ]
    payload = {
        "series_total": len(rows),
        "series_query_errors": len(rows) - len(usable),
        "categories": Counter(row.get("category") for row in rows),
        "frequencies": Counter(row.get("frequency") for row in rows),
        "series_with_20_recent_events": len(repeatable),
        "liquid_repeatable_series": sorted(
            liquid,
            key=lambda row: (
                -float(row.get("median_market_volume") or 0.0),
                -int(row.get("recent_settled_events") or 0),
            ),
        ),
        "repeatable_by_category": Counter(row.get("category") for row in repeatable),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
