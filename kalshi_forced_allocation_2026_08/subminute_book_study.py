#!/usr/bin/env python3
"""Replay forced BTC/ETH allocation on archived live order-book snapshots."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from forced_allocator_study import OLD_DATA, RECENT_DATA, bootstrap, fee, load_markets


HERE = Path(__file__).resolve().parent
BOOKS = HERE.parent / "kalshi_unbroken_bot" / "state" / "book_capture.jsonl"
OUT = HERE / "out"
OFFSETS = (120, 90, 60, 45, 30, 15)
MAX_SNAPSHOT_AGE = 35
SERIES = ("KXBTC15M", "KXETH15M")


@dataclass(frozen=True)
class Quote:
    ticker: str
    family: str
    close_ts: int
    ts: int
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    result_yes: bool

    @property
    def yes_mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0


@dataclass(frozen=True)
class Trade:
    close_ts: int
    day: str
    policy: str
    family: str
    side: str
    snapshot_seconds_before_close: int
    entry: float
    won: bool
    pnl: float


def best(levels: list) -> float | None:
    values = [float(row[0]) for row in levels if row and 0.0 <= float(row[0]) <= 1.0]
    return max(values) if values else None


def outcomes() -> dict[str, tuple[int, str, bool]]:
    mapped = {}
    for data_dir in (OLD_DATA, RECENT_DATA):
        for family in SERIES:
            for market in load_markets(data_dir, family):
                mapped[market.ticker] = (market.close_ts, family, market.result == "yes")
    return mapped


def load_snapshots() -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    with BOOKS.open() as handle:
        for line in handle:
            row = json.loads(line)
            if any(str(row.get("ticker", "")).startswith(family + "-") for family in SERIES):
                rows[row["ticker"]].append(row)
    for ticker in rows:
        rows[ticker].sort(key=lambda row: int(row["ts"]))
    return rows


def quote_at(
    ticker: str,
    snapshots: list[dict],
    close_ts: int,
    family: str,
    result_yes: bool,
    offset: int,
) -> Quote | None:
    target = close_ts - offset
    eligible = [row for row in snapshots if int(row["ts"]) <= target]
    if not eligible:
        return None
    row = eligible[-1]
    age = target - int(row["ts"])
    if age > MAX_SNAPSHOT_AGE:
        return None
    yes_bid = best(row.get("yes", []))
    no_bid = best(row.get("no", []))
    if yes_bid is None or no_bid is None:
        return None
    yes_ask = 1.0 - no_bid
    no_ask = 1.0 - yes_bid
    if yes_ask < yes_bid or no_ask < no_bid:
        return None
    return Quote(
        ticker,
        family,
        close_ts,
        int(row["ts"]),
        yes_bid,
        yes_ask,
        no_bid,
        no_ask,
        result_yes,
    )


def aligned(offset: int) -> list[tuple[int, Quote | None, Quote | None]]:
    result_map = outcomes()
    snapshots = load_snapshots()
    by_close: dict[int, dict[str, Quote]] = defaultdict(dict)
    for ticker, rows in snapshots.items():
        metadata = result_map.get(ticker)
        if metadata is None:
            continue
        close_ts, family, result_yes = metadata
        quote = quote_at(ticker, rows, close_ts, family, result_yes, offset)
        if quote is not None:
            by_close[close_ts][family] = quote
    return [
        (close_ts, values.get(SERIES[0]), values.get(SERIES[1]))
        for close_ts, values in sorted(by_close.items())
        if values.get(SERIES[0]) or values.get(SERIES[1])
    ]


def actions(quote: Quote, stress: float) -> list[tuple[str, float, float, bool, float]]:
    rows = []
    for side, midpoint, ask, won in (
        ("yes", quote.yes_mid, quote.yes_ask, quote.result_yes),
        ("no", 1.0 - quote.yes_mid, quote.no_ask, not quote.result_yes),
    ):
        fill = min(0.999, ask + stress)
        predicted_ev = midpoint - fill - fee(fill)
        pnl = (1.0 if won else 0.0) - fill - fee(fill)
        rows.append((side, midpoint, fill, won, pnl, predicted_ev))
    return rows


def select(
    close_ts: int,
    btc: Quote | None,
    eth: Quote | None,
    policy: str,
    stress: float,
) -> Trade | None:
    candidates = []
    for quote in (btc, eth):
        if quote is None:
            continue
        for row in actions(quote, stress):
            candidates.append((quote, row))
    if not candidates:
        return None

    if policy == "btc_favorite":
        pool = [item for item in candidates if item[0].family == SERIES[0]] or candidates
        chosen = max(pool, key=lambda item: item[1][1])
    elif policy == "eth_favorite":
        pool = [item for item in candidates if item[0].family == SERIES[1]] or candidates
        chosen = max(pool, key=lambda item: item[1][1])
    elif policy == "strongest_favorite":
        favorites = [item for item in candidates if item[1][1] >= 0.5]
        chosen = max(favorites, key=lambda item: item[1][1])
    elif policy == "market_prior":
        chosen = max(candidates, key=lambda item: item[1][5])
    else:
        raise ValueError(policy)
    quote, row = chosen
    side, _, fill, won, pnl, _ = row
    return Trade(
        close_ts,
        datetime.fromtimestamp(close_ts, timezone.utc).date().isoformat(),
        policy,
        quote.family,
        side,
        close_ts - (quote.ts + 0),
        fill,
        won,
        pnl,
    )


def summarize(trades: list[Trade], windows: int) -> dict:
    if not trades:
        return {"n": 0, "ev": 0.0, "coverage": 0.0}
    daily = defaultdict(float)
    for trade in trades:
        daily[trade.day] += trade.pnl
    proxy = [
        type("Proxy", (), {"day": trade.day, "pnl": trade.pnl})()
        for trade in trades
    ]
    block = bootstrap(proxy, iterations=5000)
    return {
        "n": len(trades),
        "days": len(daily),
        "coverage": len(trades) / windows,
        "win_rate": mean(trade.won for trade in trades),
        "mean_entry": mean(trade.entry for trade in trades),
        "ev": mean(trade.pnl for trade in trades),
        "pnl_per_day": sum(trade.pnl for trade in trades) / len(daily),
        "p_positive": block["p_positive"],
        "p05": block["p05"],
        "family_mix": {
            family: sum(trade.family == family for trade in trades)
            for family in SERIES
        },
    }


def run() -> dict:
    result = {}
    for offset in OFFSETS:
        rows = aligned(offset)
        result[str(offset)] = {"windows": len(rows), "policies": {}}
        for policy in ("btc_favorite", "eth_favorite", "strongest_favorite", "market_prior"):
            result[str(offset)]["policies"][policy] = {}
            for stress in (0.0, 0.01):
                trades = [
                    select(close_ts, btc, eth, policy, stress)
                    for close_ts, btc, eth in rows
                ]
                valid = [trade for trade in trades if trade is not None]
                result[str(offset)]["policies"][policy][f"stress_{stress:.2f}"] = summarize(valid, len(rows))
    return result


def cents(value: float) -> str:
    return f"{100 * value:+.3f}c"


def render(result: dict) -> str:
    lines = [
        "# Sub-Minute Live-Book Forced Allocation",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Source: `{BOOKS}`. Snapshots are accepted only when no more than {MAX_SNAPSHOT_AGE}s old.",
        "",
        "| Seconds left | Policy | N | Raw EV | +1c EV | +1c/day | +1c p05 | WR | Mean entry |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for offset in OFFSETS:
        for policy, stats in result[str(offset)]["policies"].items():
            raw = stats["stress_0.00"]
            stressed = stats["stress_0.01"]
            lines.append(
                f"| {offset} | {policy} | {stressed['n']} | {cents(raw['ev'])} | "
                f"{cents(stressed['ev'])} | ${stressed['pnl_per_day']:+.2f} | "
                f"{cents(stressed['p05'])} | {stressed['win_rate']:.2%} | {stressed['mean_entry']:.2%} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is an operational live-book sample, not an independent long-history validation. It can choose an execution deadline, but it cannot by itself promote a strategy. A positive cell must still survive a locked forward period because the archive covers only about one week.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result = run()
    (OUT / "subminute_book_results.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    report = render(result)
    (HERE / "SUBMINUTE_BOOK_REPORT.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
