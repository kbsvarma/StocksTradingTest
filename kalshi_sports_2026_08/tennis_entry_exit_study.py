#!/usr/bin/env python3
"""Replay early WTA favorite entries and fixed stop-loss variants.

Research-only. Uses the first timestamped live score, enters the market
favorite at the next minute close, and holds or applies a fixed bid stop.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict

import livescore_deep_study as core


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tennis_entry_exit_study.json")
STOP_LEVELS = (None, 0.05, 0.10, 0.15, 0.20)


def first_timestamped_row(match):
    rows = [row for row in match.tape if row.get("timestamp")]
    return min(rows, key=lambda row: core.parse_ts(row["timestamp"])) if rows else None


def quote_after(sequence, timestamp):
    for index, quote in enumerate(sequence):
        if quote.ts >= timestamp:
            return (index, quote) if quote.ts - timestamp <= 90 else None
    return None


def candidate(match, candles):
    row = first_timestamped_row(match)
    if not row or tuple(row.get("sets") or ()) != (0, 0):
        return None
    timestamp = core.parse_ts(row["timestamp"])
    choices = []
    for player in (0, 1):
        sequence = candles[match.player_tickers[player]]
        found = quote_after(sequence, timestamp)
        if not found:
            continue
        index, quote = found
        if quote.ask - quote.bid <= 0.03:
            choices.append((quote.bid + quote.ask, player, index, quote, sequence))
    if not choices:
        return None
    _, player, index, quote, sequence = max(choices)
    opponent = 1 - player
    current_set = len(row["games"][player]) - 1
    games_played = (
        row["games"][player][current_set] + row["games"][opponent][current_set]
    )
    if games_played > 4 or not (0.80 <= quote.ask < 0.90):
        return None
    return player, index, quote, sequence


def simulate(match, entry, stop):
    player, index, quote, sequence = entry
    if stop is not None:
        for later in sequence[index + 1 :]:
            if later.bid <= quote.ask - stop:
                return (
                    later.bid
                    - quote.ask
                    - core.fee(quote.ask)
                    - core.fee(later.bid),
                    "stop",
                )
    won = player == match.winner
    pnl = (1.0 - quote.ask if won else -quote.ask) - core.fee(quote.ask)
    return pnl, "settlement"


def summarize(matches, candles, stop):
    rows = []
    for match in matches:
        entry = candidate(match, candles)
        if not entry:
            continue
        pnl, exit_type = simulate(match, entry, stop)
        rows.append(
            {
                "event": match.event,
                "day": match.day,
                "ts": match.scheduled_ts,
                "entry": entry[2].ask,
                "won": entry[0] == match.winner,
                "pnl": pnl,
                "exit": exit_type,
            }
        )
    rows.sort(key=lambda row: row["ts"])
    cut = max(1, int(len(rows) * 2 / 3))
    daily = defaultdict(float)
    for row in rows:
        daily[row["day"]] += row["pnl"]
    daily_values = list(daily.values())
    if len(daily_values) > 1 and statistics.stdev(daily_values):
        t_stat = statistics.mean(daily_values) / (
            statistics.stdev(daily_values) / len(daily_values) ** 0.5
        )
        pvalue = core.student_t_upper_tail(t_stat, len(daily_values) - 1)
    else:
        t_stat, pvalue = 0.0, 1.0
    return {
        "n": len(rows),
        "wins": sum(row["won"] for row in rows),
        "losses": sum(not row["won"] for row in rows),
        "days": len(daily_values),
        "avg_entry_c": statistics.mean(row["entry"] for row in rows) * 100,
        "net_c": statistics.mean(row["pnl"] for row in rows) * 100,
        "is_c": statistics.mean(row["pnl"] for row in rows[:cut]) * 100,
        "oos_c": statistics.mean(row["pnl"] for row in rows[cut:]) * 100,
        "stops": sum(row["exit"] == "stop" for row in rows),
        "day_t": t_stat,
        "pvalue": pvalue,
    }


def main():
    candles, _ = core.load_candles()
    output = {}
    for label, sources in (
        ("observed", ("observed",)),
        ("mixed", ("mixed",)),
        ("combined", ("observed", "mixed")),
    ):
        matches, audit = core.join_matches(candles, sources)
        matches = sorted({match.event: match for match in matches}.values(), key=lambda m: m.scheduled_ts)
        output[label] = {"audit": audit, "variants": {}}
        for stop in STOP_LEVELS:
            name = "hold" if stop is None else f"stop_{int(stop * 100)}c"
            output[label]["variants"][name] = summarize(matches, candles, stop)
    with open(OUT, "w") as handle:
        json.dump(output, handle, indent=2)
    print(
        f"{'source':<10} {'variant':<10} {'n':>4} {'W-L':>8} "
        f"{'entry':>7} {'net':>8} {'IS':>7} {'OOS':>7} {'day-t':>7}"
    )
    for source, section in output.items():
        for variant, row in section["variants"].items():
            print(
                f"{source:<10} {variant:<10} {row['n']:>4} "
                f"{row['wins']:>3}-{row['losses']:<3} {row['avg_entry_c']:>6.1f}c "
                f"{row['net_c']:>+7.2f} {row['is_c']:>+6.2f} "
                f"{row['oos_c']:>+6.2f} {row['day_t']:>+7.2f}"
            )


if __name__ == "__main__":
    main()
