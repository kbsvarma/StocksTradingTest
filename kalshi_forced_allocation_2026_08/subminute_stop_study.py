#!/usr/bin/env python3
"""Test whether live-book stops rescue an every-window favorite allocation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import subminute_book_study as book
from forced_allocator_study import bootstrap, fee


HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OFFSETS = (120, 90, 60, 30)
POLICIES = ("btc_favorite", "eth_favorite", "strongest_favorite")
STOP_DISTANCES = (None, 0.05, 0.10, 0.15, 0.20)


def chosen_quote(btc, eth, policy):
    quotes = [quote for quote in (btc, eth) if quote is not None]
    if not quotes:
        return None, None
    if policy == "btc_favorite":
        quote = next((item for item in quotes if item.family == book.SERIES[0]), quotes[0])
    elif policy == "eth_favorite":
        quote = next((item for item in quotes if item.family == book.SERIES[1]), quotes[0])
    elif policy == "strongest_favorite":
        quote = max(quotes, key=lambda item: max(item.yes_mid, 1.0 - item.yes_mid))
    else:
        raise ValueError(policy)
    return quote, "yes" if quote.yes_mid >= 0.5 else "no"


def side_bid(snapshot: dict, side: str) -> float | None:
    return book.best(snapshot.get(side, []))


def replay(rows, snapshots, policy: str, stop_distance: float | None, entry_stress: float):
    trades = []
    for close_ts, btc, eth in rows:
        quote, side = chosen_quote(btc, eth, policy)
        if quote is None:
            continue
        ask = quote.yes_ask if side == "yes" else quote.no_ask
        entry = min(0.999, ask + entry_stress)
        won = quote.result_yes == (side == "yes")
        pnl = (1.0 if won else 0.0) - entry - fee(entry)
        stopped = False
        exit_price = None
        if stop_distance is not None:
            threshold = entry - stop_distance
            for snapshot in snapshots.get(quote.ticker, []):
                timestamp = int(snapshot["ts"])
                if timestamp <= quote.ts or timestamp >= close_ts:
                    continue
                bid = side_bid(snapshot, side)
                if bid is not None and bid <= threshold + 1e-9:
                    exit_price = max(0.001, bid - 0.01)
                    pnl = exit_price - entry - fee(entry) - fee(exit_price)
                    stopped = True
                    break
        trades.append(
            {
                "day": datetime.fromtimestamp(close_ts, timezone.utc).date().isoformat(),
                "pnl": pnl,
                "won": won,
                "stopped": stopped,
                "stop_would_have_won": stopped and won,
                "entry": entry,
                "exit": exit_price,
            }
        )
    return trades, len(rows)


def summarize(trades, windows):
    if not trades:
        return {"n": 0, "ev": 0.0}
    proxy = [type("Proxy", (), {"day": row["day"], "pnl": row["pnl"]})() for row in trades]
    block = bootstrap(proxy, iterations=5000)
    days = len({row["day"] for row in trades})
    stopped = [row for row in trades if row["stopped"]]
    return {
        "n": len(trades),
        "coverage": len(trades) / windows,
        "ev": mean(row["pnl"] for row in trades),
        "pnl_per_day": sum(row["pnl"] for row in trades) / days,
        "win_rate": mean(row["won"] for row in trades),
        "mean_entry": mean(row["entry"] for row in trades),
        "stop_rate": len(stopped) / len(trades),
        "stopped_winner_rate": (
            sum(row["stop_would_have_won"] for row in stopped) / len(stopped)
            if stopped else 0.0
        ),
        "p_positive": block["p_positive"],
        "p05": block["p05"],
    }


def run():
    result = {}
    snapshots = book.load_snapshots()
    for offset in OFFSETS:
        rows = book.aligned(offset)
        result[str(offset)] = {}
        for policy in POLICIES:
            result[str(offset)][policy] = {}
            for distance in STOP_DISTANCES:
                key = "none" if distance is None else f"{distance:.2f}"
                trades, windows = replay(rows, snapshots, policy, distance, 0.0)
                result[str(offset)][policy][key] = summarize(trades, windows)
    return result


def cents(value):
    return f"{100 * value:+.3f}c"


def render(result):
    lines = [
        "# Sub-Minute Stop-Loss Study",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Entries use the archived executable ask. Stops trigger on the next archived book snapshot and assume an additional 1c adverse exit. Every window still receives an entry.",
        "",
        "| Seconds left | Policy | Stop | N | EV | PnL/day | p05 | Stop rate | Stopped winners |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for offset in OFFSETS:
        for policy in POLICIES:
            for key, row in result[str(offset)][policy].items():
                lines.append(
                    f"| {offset} | {policy} | {key} | {row['n']} | {cents(row['ev'])} | "
                    f"${row['pnl_per_day']:+.2f} | {cents(row['p05'])} | {row['stop_rate']:.1%} | "
                    f"{row['stopped_winner_rate']:.1%} |"
                )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "A stop is useful only if it improves average PnL and the day-block 5th percentile without predominantly selling eventual winners. Snapshot cadence is about 31 seconds, so these are conservative operational replays, not tick-perfect stop fills.",
        "",
    ])
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    result = run()
    (OUT / "subminute_stop_results.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    report = render(result)
    (HERE / "SUBMINUTE_STOP_REPORT.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
