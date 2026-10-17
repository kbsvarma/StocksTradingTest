#!/usr/bin/env python3
"""Post-prereg diagnostic: narrower bands and genuinely late entry windows.

This is exploratory because the primary holdout has already been summarized. A
positive result here would require a new forward sample before promotion.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from study_band_stop import (
    HOLDOUT_DATA,
    OLD_DATA,
    SERIES,
    STANDARD,
    Config,
    aggregate,
    bh_qvalues,
    load_markets,
    one_sided_t_pvalue,
    replay,
    split_old,
)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
BANDS = (
    (0.50, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.75),
    (0.55, 0.65),
    (0.60, 0.70),
)
REM_WINDOWS = (
    (1, 3),
    (2, 4),
    (3, 5),
    (4, 6),
    (5, 7),
    (6, 8),
    (7, 9),
    (9, 12),
    (3, 12),
)
STOP_DISTANCES = (0.05, 0.08, 0.10, 0.12)
STOP_ANCHORS = ("band_low", "entry")
EXIT_POLICIES = ("hard_stop_hold", "fixed_take_10", "two_phase")


def configs() -> list[Config]:
    return [
        Config(low, high, min_rem, max_rem, 0.02, stop, anchor, policy)
        for low, high in BANDS
        for min_rem, max_rem in REM_WINDOWS
        for stop in STOP_DISTANCES
        for anchor in STOP_ANCHORS
        for policy in EXIT_POLICIES
    ]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    all_results = []
    candidates = configs()
    for family in SERIES:
        print(f"== {family}", flush=True)
        discovery, validation = split_old(load_markets(OLD_DATA, family))
        holdout = load_markets(HOLDOUT_DATA, family)
        rows = []
        pvalues = {}
        for index, config in enumerate(candidates, start=1):
            trades = replay(discovery, config, STANDARD)
            stats = aggregate(trades)
            pvalue = one_sided_t_pvalue(trades)
            pvalues[config.key] = pvalue
            rows.append({**asdict(config), "key": config.key, "discovery": stats, "pvalue": pvalue})
            if index % 250 == 0:
                print(f"  discovery {index}/{len(candidates)}", flush=True)
        qvalues = bh_qvalues(pvalues)
        for row in rows:
            row["qvalue"] = qvalues[row["key"]]

        ranked = sorted(rows, key=lambda row: row["discovery"]["mean"], reverse=True)
        finalists = ranked[:10]
        for row in finalists:
            config = Config(
                row["band_low"],
                row["band_high"],
                row["min_remaining"],
                row["max_remaining"],
                row["spread_cap"],
                row["stop_distance"],
                row["stop_anchor"],
                row["exit_policy"],
            )
            row["validation"] = aggregate(replay(validation, config, STANDARD))
            row["holdout"] = aggregate(replay(holdout, config, STANDARD))
        all_results.append({"family": family, "top": finalists})

        flat_rows = []
        for row in ranked:
            flat_rows.append(
                {
                    **{key: value for key, value in row.items() if key not in {"discovery"}},
                    **{f"discovery_{key}": value for key, value in row["discovery"].items()},
                }
            )
        with (OUT / f"exploratory_surface_{family}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0].keys()))
            writer.writeheader()
            writer.writerows(flat_rows)

    (OUT / "exploratory_top.json").write_text(json.dumps(all_results, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Exploratory Narrow-Band / Late-Window Diagnostic",
        "",
        "This was run after the primary preregistered holdout verdict and cannot promote a rule live.",
        "It tests 1,512 cells per family with a 2-cent spread cap and standard execution stress.",
        "",
    ]
    for result in all_results:
        lines.extend([f"## {result['family']}", ""])
        lines.append("| Rank | Rule | Discovery | q | Validation | Recent holdout |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for rank, row in enumerate(result["top"], start=1):
            rule = (
                f"{row['band_low']:.0%}-{row['band_high']:.0%}, "
                f"rem {row['min_remaining']}-{row['max_remaining']}, "
                f"stop {row['stop_distance']:.0%}/{row['stop_anchor']}, {row['exit_policy']}"
            )
            lines.append(
                f"| {rank} | {rule} | {100 * row['discovery']['mean']:+.3f}c "
                f"(n={row['discovery']['n']}) | {row['qvalue']:.3g} | "
                f"{100 * row['validation']['mean']:+.3f}c (n={row['validation']['n']}) | "
                f"{100 * row['holdout']['mean']:+.3f}c (n={row['holdout']['n']}) |"
            )
        lines.append("")
    (ROOT / "EXPLORATORY_REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {ROOT / 'EXPLORATORY_REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
