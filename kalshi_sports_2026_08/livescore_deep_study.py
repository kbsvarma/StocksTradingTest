#!/usr/bin/env python3
"""Auditable score-conditioned WTA entry study.

This repairs the original stage-two replay by joining repeated matchups by
date, evaluating every registered score/price cell, and reporting realistic
entry-at-ask economics.  It is research-only and never places orders.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import screen_states as score


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

BANDS = (
    (0.85, 0.90, 0.03),
    (0.90, 0.93, 0.03),
    (0.93, 0.95, 0.03),
    (0.95, 0.97, 0.02),
    (0.97, 0.99, 0.02),
)
STATES = (
    "P1_match_point",
    "P1b_match_point_on_return",
    "P1c_match_point_decider",
    "P2_serving_for_match",
    "P3_break_in_decider",
    "B1_set_and_break",
    "B2_set_and_double_break",
    "B3_set_and_serving_for_match",
    "B4_set_and_on_serve_CONTROL",
)


def norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(
        "".join(c for c in value.lower() if c.isalnum() or c.isspace()).split()
    )


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def parse_ts(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


@dataclass(frozen=True)
class Quote:
    ts: int
    bid: float
    ask: float


@dataclass(frozen=True)
class JoinedMatch:
    event: str
    day: str
    scheduled_ts: int
    winner: int
    player_tickers: tuple[str, str]
    rankings: tuple[int | None, int | None]
    surface: str
    tape: tuple[dict, ...]


@dataclass(frozen=True)
class Trade:
    event: str
    day: str
    ts: int
    state: str
    band: str
    entry: float
    spread: float
    won: bool
    settle_pnl: float
    target_pnl: float
    target_hit: bool
    sets_for: int
    sets_against: int
    games_for: int
    games_against: int
    serving: bool
    rank: int | None
    opponent_rank: int | None
    surface: str


def load_candles() -> tuple[dict[str, list[Quote]], dict[str, str]]:
    candles: dict[str, list[Quote]] = {}
    results: dict[str, str] = {}
    path = os.path.join(DATA, "games_KXWTAMATCH.jsonl")
    for line in open(path):
        row = json.loads(line)
        seq = []
        for candle in row.get("candles", []):
            try:
                bid = float(candle["yes_bid"]["close_dollars"])
                ask = float(candle["yes_ask"]["close_dollars"])
                ts = int(candle["end_period_ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0.0 <= bid <= ask <= 1.0:
                seq.append(Quote(ts, bid, ask))
        if seq:
            candles[row["ticker"]] = sorted(seq, key=lambda q: q.ts)
            results[row["ticker"]] = row["result"]
    return candles, results


def load_events() -> dict[frozenset[str], list[dict]]:
    events: dict[frozenset[str], list[dict]] = defaultdict(list)
    path = os.path.join(DATA, "kalshi_wta_names.jsonl")
    for line in open(path):
        row = json.loads(line)
        key = frozenset((norm(row["name_yes"]), norm(row["name_no"])))
        events[key].append(row)
    return events


def join_matches(
    candles: dict[str, list[Quote]], sources: tuple[str, ...] = ("observed",)
) -> tuple[list[JoinedMatch], dict]:
    events = load_events()
    rows = [json.loads(line) for line in open(os.path.join(DATA, "tape_wta.jsonl"))]
    usable = [
        row
        for row in rows
        if (row.get("meta") or {}).get("point_source") in sources
        and (row.get("match") or {}).get("winner")
        and row.get("tape")
    ]
    joined = []
    ambiguous = 0
    no_pair = 0
    no_candles = 0
    for row in usable:
        match = row["match"]
        players = match["players"]
        p1 = norm(players["p1"].get("name"))
        p2 = norm(players["p2"].get("name"))
        candidates = events.get(frozenset((p1, p2)), [])
        if not candidates:
            no_pair += 1
            continue
        scheduled = parse_ts(match["scheduled_time"])
        ranked = sorted(candidates, key=lambda event: abs(event["close_ts"] - scheduled))
        picked = ranked[0]
        if len(ranked) > 1:
            ambiguous += 1
        # Match delays are common, but a larger gap means this is not the same event.
        if abs(picked["close_ts"] - scheduled) > 3 * 86400:
            no_pair += 1
            continue
        ticker1 = (
            picked["ticker_yes"]
            if norm(picked["name_yes"]) == p1
            else picked["ticker_no"]
        )
        ticker2 = (
            picked["ticker_yes"]
            if norm(picked["name_yes"]) == p2
            else picked["ticker_no"]
        )
        if ticker1 not in candles or ticker2 not in candles:
            no_candles += 1
            continue
        joined.append(
            JoinedMatch(
                event=picked["event"],
                day=datetime.fromtimestamp(picked["close_ts"], timezone.utc).date().isoformat(),
                scheduled_ts=scheduled,
                winner=int(match["winner"]) - 1,
                player_tickers=(ticker1, ticker2),
                rankings=(players["p1"].get("ranking"), players["p2"].get("ranking")),
                surface=str(match.get("surface") or "unknown").lower(),
                tape=tuple(row["tape"]),
            )
        )
    audit = {
        "all_tapes": len(rows),
        "sources": list(sources),
        "source_usable": len(usable),
        "joined": len(joined),
        "ambiguous_pairs_date_resolved": ambiguous,
        "missing_name_pair": no_pair,
        "missing_candles": no_candles,
    }
    return joined, audit


def first_quote_after(seq: list[Quote], ts: int) -> Quote | None:
    """First minute close after the score update, with at most 90s latency."""
    for quote in seq:
        if quote.ts >= ts:
            return quote if quote.ts - ts <= 90 else None
    return None


def target_exit(seq: list[Quote], after_ts: int, won: bool, entry: float) -> tuple[float, bool]:
    """Sell at the first executable 97c bid; otherwise hold to settlement."""
    for quote in seq:
        if quote.ts > after_ts and quote.bid >= 0.97:
            return quote.bid - entry - fee(entry) - fee(quote.bid), True
    settle = (1.0 - entry if won else -entry) - fee(entry)
    return settle, False


def collect_trades(matches: list[JoinedMatch], candles: dict[str, list[Quote]]) -> list[Trade]:
    trades = []
    for match in matches:
        seen: set[tuple[str, str]] = set()
        for tape_row in match.tape:
            sets = tape_row.get("sets") or []
            if not sets or max(sets) >= 2:
                # A final 2-0/2-1 row is outcome information, not an entry signal.
                continue
            ts_raw = tape_row.get("timestamp")
            if not ts_raw:
                continue
            ts = parse_ts(ts_raw)
            for state, player in score.states_for(tape_row).items():
                if state not in STATES:
                    continue
                ticker = match.player_tickers[player]
                quote = first_quote_after(candles[ticker], ts)
                if not quote:
                    continue
                spread = quote.ask - quote.bid
                for lo, hi, max_spread in BANDS:
                    band = f"{int(lo * 100)}-{int(hi * 100)}"
                    key = (state, band)
                    if key in seen:
                        continue
                    if not (lo <= quote.ask < hi and spread <= max_spread):
                        continue
                    seen.add(key)
                    won = player == match.winner
                    opponent = 1 - player
                    current_set = len(tape_row["games"][player]) - 1
                    settle = (1.0 - quote.ask if won else -quote.ask) - fee(quote.ask)
                    target, target_hit = target_exit(
                        candles[ticker], quote.ts, won, quote.ask
                    )
                    trades.append(
                        Trade(
                            event=match.event,
                            day=match.day,
                            ts=quote.ts,
                            state=state,
                            band=band,
                            entry=quote.ask,
                            spread=spread,
                            won=won,
                            settle_pnl=settle,
                            target_pnl=target,
                            target_hit=target_hit,
                            sets_for=int(tape_row["sets"][player]),
                            sets_against=int(tape_row["sets"][opponent]),
                            games_for=int(tape_row["games"][player][current_set]),
                            games_against=int(tape_row["games"][opponent][current_set]),
                            serving=tape_row.get("server") == player + 1,
                            rank=match.rankings[player],
                            opponent_rank=match.rankings[opponent],
                            surface=match.surface,
                        )
                    )
    return trades


def day_t(values: list[Trade], field: str) -> tuple[float, float, int]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in values:
        grouped[trade.day].append(getattr(trade, field))
    # Markets on the same day are not independent. Daily total P&L preserves
    # actual strategy economics and prevents sparse one-trade days from
    # receiving the same weight as full tournament slates.
    daily = [sum(v) for v in grouped.values()]
    if len(daily) < 2 or statistics.stdev(daily) == 0:
        return (999.0 if daily and daily[0] > 0 else 0.0, 0.0, len(daily))
    t_stat = statistics.mean(daily) / (statistics.stdev(daily) / math.sqrt(len(daily)))
    pvalue = student_t_upper_tail(t_stat, len(daily) - 1)
    return t_stat, pvalue, len(daily)


def beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Numerical Recipes continued fraction for the incomplete beta."""
    max_iterations, epsilon, floor = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / max(abs(d), floor) * (1 if d >= 0 else -1)
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / max(abs(d), floor) * (1 if d >= 0 else -1)
        c = 1.0 + aa / c
        c = max(abs(c), floor) * (1 if c >= 0 else -1)
        h *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / (
            (a + m2) * (qap + m2)
        )
        d = 1.0 + aa * d
        d = 1.0 / max(abs(d), floor) * (1 if d >= 0 else -1)
        c = 1.0 + aa / c
        c = max(abs(c), floor) * (1 if c >= 0 else -1)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * beta_continued_fraction(a, b, x) / a
    return 1.0 - front * beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_upper_tail(t_stat: float, degrees: int) -> float:
    if degrees <= 0:
        return 1.0
    x = degrees / (degrees + t_stat * t_stat)
    half_tail = 0.5 * regularized_beta(x, degrees / 2.0, 0.5)
    return half_tail if t_stat >= 0 else 1.0 - half_tail


def bh_adjust(pvalues: list[float]) -> list[float]:
    n = len(pvalues)
    order = sorted(range(n), key=pvalues.__getitem__)
    adjusted = [1.0] * n
    running = 1.0
    for rank_index in range(n - 1, -1, -1):
        original_index = order[rank_index]
        rank = rank_index + 1
        running = min(running, pvalues[original_index] * n / rank)
        adjusted[original_index] = min(1.0, running)
    return adjusted


def summarize(trades: list[Trade]) -> list[dict]:
    grouped: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.state, trade.band)].append(trade)
    rows = []
    for (state, band), values in sorted(grouped.items()):
        values.sort(key=lambda trade: trade.ts)
        cut = max(1, int(len(values) * 2 / 3))
        ins, oos = values[:cut], values[cut:]
        t_stat, pvalue, days = day_t(values, "settle_pnl")
        rows.append(
            {
                "state": state,
                "band": band,
                "n": len(values),
                "wins": sum(trade.won for trade in values),
                "days": days,
                "avg_entry_c": statistics.mean(t.entry for t in values) * 100,
                "settle_c": statistics.mean(t.settle_pnl for t in values) * 100,
                "is_c": statistics.mean(t.settle_pnl for t in ins) * 100,
                "oos_c": statistics.mean(t.settle_pnl for t in oos) * 100 if oos else 0.0,
                "target97_c": statistics.mean(t.target_pnl for t in values) * 100,
                "target_hit_pct": statistics.mean(t.target_hit for t in values) * 100,
                "day_t": t_stat,
                "pvalue": pvalue,
            }
        )
    adjusted = bh_adjust([row["pvalue"] for row in rows])
    for row, qvalue in zip(rows, adjusted):
        row["qvalue"] = qvalue
        row["passes"] = (
            row["n"] >= 100
            and row["settle_c"] > 0
            and row["is_c"] > 0
            and row["oos_c"] > 0
            and row["day_t"] >= 2
            and qvalue <= 0.10
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        default="observed,mixed",
        help="comma-separated point_source values (default: observed,mixed)",
    )
    args = parser.parse_args()
    sources = tuple(value.strip() for value in args.sources.split(",") if value.strip())
    candles, _ = load_candles()
    matches, audit = join_matches(candles, sources)
    trades = collect_trades(matches, candles)
    rows = summarize(trades)
    print("JOIN AUDIT", json.dumps(audit, sort_keys=True))
    print(f"priced state-band entries: {len(trades)} | cells: {len(rows)}\n")
    header = (
        f"{'state':<33} {'band':>6} {'n':>4} {'W-L':>8} {'entry':>7} "
        f"{'settle':>8} {'IS':>7} {'OOS':>7} {'t(day)':>7} {'q':>7} "
        f"{'97exit':>8} {'hit%':>6} {'PASS':>5}"
    )
    print(header)
    for row in rows:
        print(
            f"{row['state']:<33} {row['band']:>6} {row['n']:>4} "
            f"{row['wins']:>3}-{row['n']-row['wins']:<3} "
            f"{row['avg_entry_c']:>6.1f}c {row['settle_c']:>+7.2f} "
            f"{row['is_c']:>+6.2f} {row['oos_c']:>+6.2f} "
            f"{row['day_t']:>+7.2f} {row['qvalue']:>7.3f} "
            f"{row['target97_c']:>+7.2f} {row['target_hit_pct']:>5.1f} "
            f"{str(row['passes']):>5}"
        )
    output = {"audit": audit, "trade_count": len(trades), "cells": rows}
    with open(os.path.join(DATA, "livescore_deep_study.json"), "w") as handle:
        json.dump(output, handle, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
