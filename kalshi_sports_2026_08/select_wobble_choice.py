#!/usr/bin/env python3
"""Select the least-fragile wobble-rule variant using score-confirmed WTA play.

Research only. The parameter choice uses the first 60% of joined matches; the
last 40% is reported only after selection. ITF families are excluded because
their archived occurrence timestamps are predominantly later than settlement.
"""

from __future__ import annotations

import csv
import os
import random
import statistics
from collections import defaultdict

import audit_wobble_rule as wobble
import livescore_deep_study as live


HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "data", "wobble_choice_scorecard.csv")
REPORT = os.path.join(HERE, "WOBBLE_CHOICE_SELECTION.md")
SLIPPAGE = 0.01
TRAIN_FRACTION = 0.60
BOOTSTRAPS = 50_000
SEED = 20260806


def score_boundaries() -> dict[str, tuple[int, int]]:
    candles, _ = live.load_candles()
    matches, _ = live.join_matches(candles, ("observed",))
    boundaries = {}
    for match in matches:
        timestamps = [
            live.parse_ts(row["timestamp"])
            for row in match.tape
            if row.get("timestamp")
        ]
        if timestamps:
            boundaries[match.event] = (match.scheduled_ts, min(timestamps))
    return boundaries


def replay(
    events: dict[str, list[wobble.Market]],
    boundaries: dict[str, tuple[int, int]],
    band: tuple[float, float],
    hold: int,
    spread_cap: float,
    pre_count: int,
    pre_bin: tuple[float, float | None, str],
    require_fall: bool,
) -> list[wobble.Trade]:
    low, high, _ = pre_bin
    trades = []
    for event, markets in events.items():
        boundary = boundaries.get(event)
        if boundary is None:
            continue
        scheduled_ts, first_score_ts = boundary
        choices = []
        for market in markets:
            pre = wobble.pre_price(market, pre_count)
            if pre is None or not wobble.in_bin(pre[0], low, high):
                continue
            # This is conservative: pre-price must finish before both scheduled
            # play (with a five-minute buffer) and the first observed score.
            if pre[1] >= min(scheduled_ts - 300, first_score_ts):
                continue
            trigger = wobble.first_trigger(
                market,
                band,
                hold,
                spread_cap,
                strict_minutes=True,
                min_window_ts=first_score_ts,
                require_decline_from=pre[0] if require_fall else None,
            )
            if trigger is not None:
                choices.append(wobble.make_trade("KXWTAMATCH", market, trigger, pre))
        if choices:
            trades.append(min(choices, key=lambda row: (row.ts, row.ask, row.ticker)))
    trades.sort(key=lambda row: (row.ts, row.event))
    assert len(trades) == len({trade.event for trade in trades})
    return trades


def pnl(trade: wobble.Trade) -> float:
    price = min(1.0, trade.ask + SLIPPAGE)
    return ((1.0 - price) if trade.won else -price) - wobble.fee(price)


def summarize(trades: list[wobble.Trade]) -> dict[str, float | int]:
    values = [pnl(trade) for trade in trades]
    breakevens = []
    for trade in trades:
        price = min(1.0, trade.ask + SLIPPAGE)
        breakevens.append(price + wobble.fee(price))
    return {
        "n": len(trades),
        "wins": sum(trade.won for trade in trades),
        "losses": sum(not trade.won for trade in trades),
        "net_c": statistics.mean(values) * 100 if values else float("nan"),
        "breakeven": statistics.mean(breakevens) if breakevens else float("nan"),
    }


def wilson_lower(wins: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return float("nan")
    observed = wins / total
    denominator = 1 + z * z / total
    center = observed + z * z / (2 * total)
    radius = z * ((observed * (1 - observed) / total + z * z / (4 * total * total)) ** 0.5)
    return (center - radius) / denominator


def weekly_bootstrap(trades: list[wobble.Trade]) -> dict[str, float | int]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.week].append(pnl(trade))
    blocks = list(grouped.values())
    if not blocks:
        return {"weeks": 0, "p_positive": float("nan"), "p05_c": float("nan")}
    rng = random.Random(SEED)
    means = []
    for _ in range(BOOTSTRAPS):
        sampled = [rng.choice(blocks) for _ in blocks]
        means.append(sum(map(sum, sampled)) / sum(map(len, sampled)) * 100)
    means.sort()
    return {
        "weeks": len(blocks),
        "p_positive": sum(value > 0 for value in means) / len(means),
        "p05_c": means[int(0.05 * (len(means) - 1))],
    }


def immediate_neighbors(row: dict, rows: list[dict]) -> list[dict]:
    dimensions = ("band_index", "hold_index", "spread_index", "pre_index")
    neighbors = [row]
    for other in rows:
        if other["pre_bin"] != row["pre_bin"] or other["require_fall"] != row["require_fall"]:
            continue
        differences = [name for name in dimensions if other[name] != row[name]]
        if len(differences) != 1:
            continue
        name = differences[0]
        if abs(other[name] - row[name]) == 1:
            neighbors.append(other)
    return neighbors


def format_cell(row: dict) -> str:
    return (
        f"pre={row['pre_bin']}, band={row['band_low']:.2f}-{row['band_high']:.2f}, "
        f"hold={row['hold']}, spread<={row['spread_cap']:.2f}, first={row['pre_count']}"
    )


def main() -> None:
    metadata = wobble.load_metadata()
    events = wobble.load_family("KXWTAMATCH", metadata)
    boundaries = score_boundaries()
    ordered_starts = sorted(first_score for _, first_score in boundaries.values())
    cutoff = ordered_starts[int(TRAIN_FRACTION * len(ordered_starts))]
    rows = []

    for require_fall in (True, False):
        for pre_bin in wobble.PRE_BINS:
            for band_index, band in enumerate(wobble.BANDS):
                for hold_index, hold in enumerate(wobble.HOLDS):
                    for spread_index, spread in enumerate(wobble.SPREADS):
                        for pre_index, pre_count in enumerate(wobble.PRE_COUNTS):
                            trades = replay(
                                events,
                                boundaries,
                                band,
                                hold,
                                spread,
                                pre_count,
                                pre_bin,
                                require_fall,
                            )
                            train = [trade for trade in trades if trade.ts < cutoff]
                            test = [trade for trade in trades if trade.ts >= cutoff]
                            full_summary = summarize(trades)
                            train_summary = summarize(train)
                            test_summary = summarize(test)
                            rows.append(
                                {
                                    "require_fall": require_fall,
                                    "pre_bin": pre_bin[2],
                                    "band_index": band_index,
                                    "band_low": band[0],
                                    "band_high": band[1],
                                    "hold_index": hold_index,
                                    "hold": hold,
                                    "spread_index": spread_index,
                                    "spread_cap": spread,
                                    "pre_index": pre_index,
                                    "pre_count": pre_count,
                                    "full_n": full_summary["n"],
                                    "full_wins": full_summary["wins"],
                                    "full_losses": full_summary["losses"],
                                    "full_net_c": full_summary["net_c"],
                                    "full_breakeven": full_summary["breakeven"],
                                    "full_wilson_lower": wilson_lower(
                                        int(full_summary["wins"]), int(full_summary["n"])
                                    ),
                                    "train_n": train_summary["n"],
                                    "train_net_c": train_summary["net_c"],
                                    "test_n": test_summary["n"],
                                    "test_net_c": test_summary["net_c"],
                                    "trades": trades,
                                }
                            )

    for row in rows:
        neighbors = [
            item
            for item in immediate_neighbors(row, rows)
            if item["train_n"] >= 10
        ]
        row["neighbor_count"] = len(neighbors)
        row["neighbor_train_floor_c"] = (
            min(item["train_net_c"] for item in neighbors) if neighbors else float("nan")
        )
        row["neighbor_train_median_c"] = (
            statistics.median(item["train_net_c"] for item in neighbors)
            if neighbors
            else float("nan")
        )

    # Selection sees training results only. A candidate needs activity and a
    # positive local plateau; test results are opened only for the chosen row.
    def choose(require_fall: bool) -> dict | None:
        eligible = [
            row
            for row in rows
            if row["require_fall"] == require_fall
            and row["train_n"] >= 15
            and row["neighbor_count"] >= 5
            and row["neighbor_train_floor_c"] > 0
        ]
        return max(
            eligible,
            key=lambda row: (
                row["neighbor_train_floor_c"],
                row["neighbor_train_median_c"],
                row["train_n"],
            ),
            default=None,
        )

    winners = {True: choose(True), False: choose(False)}
    for row in winners.values():
        if row is not None:
            row["bootstrap"] = weekly_bootstrap(row["trades"])

    serializable = [
        {key: value for key, value in row.items() if key not in {"trades", "bootstrap"}}
        for row in rows
    ]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(serializable[0]))
        writer.writeheader()
        writer.writerows(serializable)

    lines = [
        "# Wobble Choice Selection",
        "",
        "This is a research-only selection replay. No live bot was changed.",
        "",
        "## Protocol",
        "",
        f"- {len(boundaries)} WTA matches have timestamped observed score tapes.",
        "- Pre-price must finish at least five minutes before scheduled play.",
        "- The hold window may begin only after the first observed live score.",
        "- Economics include exact Kalshi entry fees and a 1c fill penalty.",
        "- The first 60% of matches select the cell; the final 40% tests it.",
        "- Selection maximizes the worst training result across immediate neighboring cells, not the cell's own peak.",
        "",
        "## Selected Cells",
        "",
        "| Interpretation | Cell | Train | Test | Full | Wilson lower / breakeven | Weekly P(+edge) | Weekly p05 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for require_fall, label in ((True, "Genuine downward wobble"), (False, "Literal submitted conditions")):
        row = winners[require_fall]
        if row is None:
            lines.append(f"| {label} | No training-qualified cell | — | — | — | — | — | — |")
            continue
        boot = row["bootstrap"]
        lines.append(
            f"| {label} | {format_cell(row)} | {row['train_n']} @ {row['train_net_c']:+.2f}c | "
            f"{row['test_n']} @ {row['test_net_c']:+.2f}c | "
            f"{row['full_wins']}-{row['full_losses']} @ {row['full_net_c']:+.2f}c | "
            f"{row['full_wilson_lower'] * 100:.1f}% / {row['full_breakeven'] * 100:.1f}% | "
            f"{boot['p_positive'] * 100:.1f}% | {boot['p05_c']:+.2f}c |"
        )

    primary = winners[True]
    literal = winners[False]
    lines.extend(["", "## Decision", ""])
    if primary is None:
        lines.append("**No genuine-wobble configuration qualifies even on training data.**")
    else:
        boot = primary["bootstrap"]
        passed = (
            primary["test_n"] >= 10
            and primary["test_net_c"] > 0
            and primary["full_n"] >= 30
            and boot["p05_c"] > 0
            and primary["full_wilson_lower"] > primary["full_breakeven"]
        )
        lines.append(
            "**The genuine-wobble winner {} the research gate.**".format("passes" if passed else "fails")
        )
    if literal is not None:
        literal_passed = (
            literal["test_n"] >= 10
            and literal["test_net_c"] > 0
            and literal["full_n"] >= 30
            and literal["bootstrap"]["p05_c"] > 0
            and literal["full_wilson_lower"] > literal["full_breakeven"]
        )
        lines.append(
            "The literal-rule winner {} the deployment gate. It is reported separately because it may consist of "
            "players rising into the band and cannot validate the claimed overreaction mechanism.".format(
                "passes" if literal_passed else "fails"
            )
        )
    lines.extend(
        [
            "ITF and ITFW are not eligible for selection: their archived occurrence timestamps are usually later than settlement, "
            "and no historical score-confirmed start tape was available for this audit.",
            "",
            f"Full {len(rows)}-cell scorecard: `data/{os.path.basename(OUTPUT)}`.",
        ]
    )
    with open(REPORT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(REPORT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
