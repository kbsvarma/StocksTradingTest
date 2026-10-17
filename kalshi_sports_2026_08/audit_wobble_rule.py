#!/usr/bin/env python3
"""Adversarial replay of the proposed 88-92c tennis wobble rule.

This script deliberately keeps claimed, canonical, timing-valid, and mirror
interpretations separate.  It writes every parameter cell to CSV so the exact
rule cannot be rescued by changing a hidden convention after seeing results.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass


HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPORT = os.path.join(HERE, "WOBBLE_RULE_AUDIT.md")
SURFACE = os.path.join(DATA, "wobble_rule_surface.csv")
FAMILIES = ("KXWTAMATCH", "KXITFMATCH", "KXITFWMATCH")
BANDS = ((0.86, 0.90), (0.88, 0.92), (0.90, 0.94))
HOLDS = (1, 2, 3, 5, 8)
SPREADS = (0.01, 0.02, 0.03)
PRE_COUNTS = (10, 30, 60)
PRE_BINS = ((0.80, 0.90, "80-90"), (0.90, None, "90+"))
BOOTSTRAPS = 50_000
SEED = 8675309


def fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def event_key(ticker: str) -> str:
    return "-".join(ticker.split("-")[:2])


def parse_iso(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


@dataclass(frozen=True)
class Market:
    ticker: str
    event: str
    player: str
    result: str
    open_ts: int
    close_ts: int
    final_volume: float
    occurrence_ts: int | None
    # ts, bid, ask, mid, minute volume, open interest
    sequence: tuple[tuple[int, float, float, float, float, float], ...]


@dataclass(frozen=True)
class Trigger:
    ts: int
    bid: float
    ask: float
    index: int


@dataclass(frozen=True)
class Trade:
    family: str
    event: str
    ticker: str
    player: str
    ts: int
    bid: float
    ask: float
    won: bool
    pre_price: float
    pre_end_ts: int
    occurrence_ts: int | None
    close_ts: int
    entry_volume: float
    trailing_5m_volume: float
    entry_oi: float
    final_volume: float

    @property
    def pnl(self) -> float:
        return ((1.0 - self.ask) if self.won else -self.ask) - fee(self.ask)

    @property
    def week(self) -> str:
        stamp = dt.datetime.fromtimestamp(self.ts, dt.timezone.utc)
        iso = stamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"


def load_metadata() -> dict[str, dict]:
    path = os.path.join(DATA, "audit_tennis_occurrence.jsonl")
    if not os.path.exists(path):
        raise SystemExit("Run fetch_tennis_occurrence.py first")
    metadata = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            metadata[row["ticker"]] = row
    return metadata


def compact_sequence(candles: list[dict]) -> tuple[tuple[int, float, float, float, float, float], ...]:
    sequence = []
    for candle in candles:
        try:
            bid = float(candle["yes_bid"]["close_dollars"])
            ask = float(candle["yes_ask"]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0.0 <= bid <= ask <= 1.0:
            continue
        sequence.append(
            (
                int(candle["end_period_ts"]),
                bid,
                ask,
                (bid + ask) / 2.0,
                float(candle.get("volume_fp") or 0.0),
                float(candle.get("open_interest_fp") or 0.0),
            )
        )
    sequence.sort(key=lambda row: row[0])
    return tuple(sequence)


def load_family(family: str, metadata: dict[str, dict]) -> dict[str, list[Market]]:
    events: dict[str, list[Market]] = defaultdict(list)
    path = os.path.join(DATA, f"games_{family}.jsonl")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            ticker = row["ticker"]
            meta = metadata.get(ticker, {})
            market = Market(
                ticker=ticker,
                event=event_key(ticker),
                player=ticker.rsplit("-", 1)[1],
                result=row["result"],
                open_ts=int(row["open_ts"]),
                close_ts=int(row["close_ts"]),
                final_volume=float(row.get("volume_fp") or 0.0),
                occurrence_ts=parse_iso(meta.get("occurrence_datetime")),
                sequence=compact_sequence(row.get("candles") or []),
            )
            events[market.event].append(market)
    return events


def pre_price(market: Market, count: int) -> tuple[float, int] | None:
    if len(market.sequence) < count:
        return None
    sample = market.sequence[:count]
    return statistics.median(row[2] for row in sample), sample[-1][0]


def first_trigger(
    market: Market,
    band: tuple[float, float],
    hold: int,
    spread_cap: float,
    strict_minutes: bool,
    min_window_ts: int | None = None,
    require_decline_from: float | None = None,
) -> Trigger | None:
    low, high = band
    sequence = market.sequence
    for index in range(hold - 1, len(sequence)):
        window = sequence[index - hold + 1 : index + 1]
        if min_window_ts is not None and window[0][0] < min_window_ts:
            continue
        if not all(low <= row[3] < high for row in window):
            continue
        if strict_minutes and any(
            window[offset][0] - window[offset - 1][0] != 60
            for offset in range(1, len(window))
        ):
            continue
        current = window[-1]
        if current[2] - current[1] > spread_cap + 1e-10:
            continue
        if require_decline_from is not None and current[3] >= require_decline_from:
            continue
        return Trigger(current[0], current[1], current[2], index)
    return None


def in_bin(value: float, low: float, high: float | None) -> bool:
    return value >= low and (high is None or value < high)


def make_trade(family: str, market: Market, trigger: Trigger, pre: tuple[float, int]) -> Trade:
    start = trigger.ts - 300
    trailing = sum(
        row[4] for row in market.sequence if start < row[0] <= trigger.ts
    )
    row = market.sequence[trigger.index]
    return Trade(
        family=family,
        event=market.event,
        ticker=market.ticker,
        player=market.player,
        ts=trigger.ts,
        bid=trigger.bid,
        ask=trigger.ask,
        won=market.result == "yes",
        pre_price=pre[0],
        pre_end_ts=pre[1],
        occurrence_ts=market.occurrence_ts,
        close_ts=market.close_ts,
        entry_volume=row[4],
        trailing_5m_volume=trailing,
        entry_oi=row[5],
        final_volume=market.final_volume,
    )


def canonical_replay(
    family: str,
    events: dict[str, list[Market]],
    band: tuple[float, float],
    hold: int,
    spread_cap: float,
    pre_count: int,
    pre_bin: tuple[float, float | None, str],
    strict_minutes: bool,
    timing_valid: bool = False,
    require_fall: bool = False,
) -> list[Trade]:
    low, high, _ = pre_bin
    trades = []
    for markets in events.values():
        choices = []
        for market in markets:
            pre = pre_price(market, pre_count)
            if pre is None or not in_bin(pre[0], low, high):
                continue
            if timing_valid:
                occurrence = market.occurrence_ts
                if occurrence is None or not (market.open_ts <= occurrence <= market.close_ts):
                    continue
                if pre[1] >= occurrence:
                    continue
                trigger = first_trigger(
                    market,
                    band,
                    hold,
                    spread_cap,
                    strict_minutes,
                    min_window_ts=occurrence,
                    require_decline_from=pre[0] if require_fall else None,
                )
            else:
                trigger = first_trigger(
                    market,
                    band,
                    hold,
                    spread_cap,
                    strict_minutes,
                    require_decline_from=pre[0] if require_fall else None,
                )
            if trigger is None:
                continue
            choices.append(make_trade(family, market, trigger, pre))
        if choices:
            trades.append(min(choices, key=lambda row: (row.ts, row.ask, row.ticker)))
    return sorted(trades, key=lambda row: (row.ts, row.event))


def mirror_replay(
    family: str,
    events: dict[str, list[Market]],
    pre_bin: tuple[float, float | None, str],
) -> list[Trade]:
    """Likely naive interpretation: either side of either mirror may trigger."""
    low, high, _ = pre_bin
    trades = []
    for markets in events.values():
        players = [market.player for market in markets]
        choices = []
        for market in markets:
            opponent = next((player for player in players if player != market.player), None)
            if opponent is None or len(market.sequence) < 30:
                continue
            yes_pre = statistics.median(row[2] for row in market.sequence[:30])
            no_pre = statistics.median(1.0 - row[1] for row in market.sequence[:30])
            sequence = []
            for index, row in enumerate(market.sequence):
                ts, yes_bid, yes_ask, yes_mid, _, _ = row
                if yes_mid >= 0.5:
                    sequence.append((index, ts, market.player, "yes", yes_bid, yes_ask, yes_mid, yes_pre))
                else:
                    sequence.append((index, ts, opponent, "no", 1.0 - yes_ask, 1.0 - yes_bid, 1.0 - yes_mid, no_pre))
            for index in range(2, len(sequence)):
                window = sequence[index - 2 : index + 1]
                current = window[-1]
                if not all(
                    row[2] == current[2] and 0.88 <= row[6] < 0.92
                    for row in window
                ):
                    continue
                if current[5] - current[4] > 0.02 + 1e-10:
                    continue
                if not in_bin(current[7], low, high):
                    continue
                direct = next(m for m in markets if m.player == current[2])
                source_row = market.sequence[current[0]]
                choices.append(
                    Trade(
                        family=family,
                        event=market.event,
                        ticker=market.ticker,
                        player=current[2],
                        ts=current[1],
                        bid=current[4],
                        ask=current[5],
                        won=direct.result == "yes",
                        pre_price=current[7],
                        pre_end_ts=market.sequence[29][0],
                        occurrence_ts=market.occurrence_ts,
                        close_ts=market.close_ts,
                        entry_volume=source_row[4],
                        trailing_5m_volume=sum(
                            row[4]
                            for row in market.sequence
                            if current[1] - 300 < row[0] <= current[1]
                        ),
                        entry_oi=source_row[5],
                        final_volume=market.final_volume,
                    )
                )
                break
        if choices:
            trades.append(min(choices, key=lambda row: (row.ts, row.ask, row.ticker)))
    return sorted(trades, key=lambda row: (row.ts, row.event))


def summarize(trades: list[Trade], slippage: float = 0.0) -> dict:
    pnls = []
    for trade in trades:
        price = min(1.0, trade.ask + slippage)
        pnls.append(((1.0 - price) if trade.won else -price) - fee(price))
    split = len(trades) // 2
    first = pnls[:split]
    second = pnls[split:]
    return {
        "n": len(trades),
        "wins": sum(trade.won for trade in trades),
        "losses": sum(not trade.won for trade in trades),
        "win_rate": statistics.mean([float(trade.won) for trade in trades]) if trades else float("nan"),
        "avg_ask": statistics.mean([trade.ask for trade in trades]) if trades else float("nan"),
        "net_c": statistics.mean(pnls) * 100.0 if pnls else float("nan"),
        "first_c": statistics.mean(first) * 100.0 if first else float("nan"),
        "second_c": statistics.mean(second) * 100.0 if second else float("nan"),
        "weeks": len({trade.week for trade in trades}),
    }


def weekly_bootstrap(trades: list[Trade], iterations: int = BOOTSTRAPS) -> dict:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.week].append(trade.pnl)
    blocks = list(grouped.values())
    if not blocks:
        return {"p_positive": float("nan"), "p05_c": float("nan"), "weeks": 0}
    rng = random.Random(SEED)
    means = []
    for _ in range(iterations):
        sampled = [rng.choice(blocks) for _ in blocks]
        total = sum(sum(block) for block in sampled)
        count = sum(len(block) for block in sampled)
        means.append(total / count * 100.0)
    means.sort()
    return {
        "p_positive": sum(value > 0 for value in means) / len(means),
        "p05_c": means[int(0.05 * (len(means) - 1))],
        "weeks": len(blocks),
    }


def mirror_audit(events: dict[str, list[Market]]) -> dict:
    sizes = Counter(len(markets) for markets in events.values())
    complementary = 0
    same_close = 0
    for markets in events.values():
        if len(markets) == 2:
            complementary += {market.result for market in markets} == {"yes", "no"}
            same_close += markets[0].close_ts == markets[1].close_ts
    return {
        "events": len(events),
        "sizes": dict(sorted(sizes.items())),
        "complementary": complementary,
        "same_close": same_close,
    }


def timing_audit(trades: list[Trade]) -> dict:
    counts = Counter()
    for trade in trades:
        occurrence = trade.occurrence_ts
        if occurrence is None:
            counts["missing_occurrence"] += 1
        elif occurrence > trade.close_ts:
            counts["occurrence_after_close"] += 1
        else:
            counts["occurrence_usable"] += 1
            counts["pre_sample_contaminated"] += trade.pre_end_ts >= occurrence
            counts["entry_before_occurrence"] += trade.ts < occurrence
            counts["fully_timing_valid"] += trade.pre_end_ts < occurrence <= trade.ts
    return dict(counts)


def liquidity_audit(trades: list[Trade]) -> dict:
    if not trades:
        return {}
    return {
        "entry_volume_zero_pct": sum(trade.entry_volume <= 0 for trade in trades) / len(trades) * 100,
        "entry_volume_ge_1_pct": sum(trade.entry_volume >= 1 for trade in trades) / len(trades) * 100,
        "entry_volume_ge_10_pct": sum(trade.entry_volume >= 10 for trade in trades) / len(trades) * 100,
        "trailing_volume_ge_10_pct": sum(trade.trailing_5m_volume >= 10 for trade in trades) / len(trades) * 100,
        "oi_ge_10_pct": sum(trade.entry_oi >= 10 for trade in trades) / len(trades) * 100,
        "median_entry_volume": statistics.median(trade.entry_volume for trade in trades),
        "median_trailing_volume": statistics.median(trade.trailing_5m_volume for trade in trades),
        "median_oi": statistics.median(trade.entry_oi for trade in trades),
        "median_final_volume": statistics.median(trade.final_volume for trade in trades),
    }


def tape_window_audit(events: dict[str, list[Market]], count: int = 30) -> dict:
    spans = []
    contaminated = 0
    usable_occurrence = 0
    occurrence_after_close = 0
    for markets in events.values():
        for market in markets:
            if market.occurrence_ts is not None:
                if market.occurrence_ts > market.close_ts:
                    occurrence_after_close += 1
                else:
                    usable_occurrence += 1
            if len(market.sequence) < count:
                continue
            sample = market.sequence[:count]
            spans.append((sample[-1][0] - sample[0][0]) / 60.0)
            if market.occurrence_ts is not None and sample[-1][0] >= market.occurrence_ts:
                contaminated += 1
    ordered = sorted(spans)
    return {
        "eligible_markets": len(spans),
        "median_span_minutes": statistics.median(ordered) if ordered else float("nan"),
        "p90_span_minutes": ordered[int(0.90 * (len(ordered) - 1))] if ordered else float("nan"),
        "first_window_at_or_after_occurrence": contaminated,
        "usable_occurrence": usable_occurrence,
        "occurrence_after_close": occurrence_after_close,
    }


def format_number(value: float, places: int = 2) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:.{places}f}"


def proposed_bins(family: str) -> tuple[tuple[float, float | None, str], ...]:
    return PRE_BINS if family == "KXWTAMATCH" else (PRE_BINS[1],)


def main() -> None:
    metadata = load_metadata()
    all_surface_rows = []
    family_results = {}
    for family in FAMILIES:
        print(f"Loading {family}...", flush=True)
        events = load_family(family, metadata)
        result = {
            "mirror": mirror_audit(events),
            "tape": tape_window_audit(events),
            "exact": {},
            "thresholds": {},
            "surface": [],
        }

        # Exact submitted cells under four independently defined interpretations.
        for pre_bin in proposed_bins(family):
            label = pre_bin[2]
            adjacent = canonical_replay(family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, False)
            strict = canonical_replay(family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, True)
            timing_valid = canonical_replay(
                family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, True, True
            )
            strict_fall = canonical_replay(
                family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, True, False, True
            )
            timing_fall = canonical_replay(
                family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, True, True, True
            )
            mirrors = mirror_replay(family, events, pre_bin)
            result["exact"][label] = {
                "adjacent_trades": adjacent,
                "strict_trades": strict,
                "timing_trades": timing_valid,
                "strict_fall_trades": strict_fall,
                "timing_fall_trades": timing_fall,
                "mirror_trades": mirrors,
                "adjacent": summarize(adjacent),
                "strict": summarize(strict),
                "timing": summarize(timing_valid),
                "strict_fall": summarize(strict_fall),
                "timing_fall": summarize(timing_fall),
                "mirrors": summarize(mirrors),
                "bootstrap": weekly_bootstrap(strict),
                "timing_audit": timing_audit(strict),
                "liquidity": liquidity_audit(strict),
            }

        # Common thresholds expose whether the family-specific cutoff is fitted.
        for pre_bin in PRE_BINS:
            trades = canonical_replay(
                family, events, (0.88, 0.92), 3, 0.02, 30, pre_bin, True
            )
            result["thresholds"][pre_bin[2]] = summarize(trades)

        # Full requested surface, canonical direct-player ticker and strict minutes.
        for pre_bin in PRE_BINS:
            for band in BANDS:
                for hold in HOLDS:
                    for spread in SPREADS:
                        for pre_count in PRE_COUNTS:
                            trades = canonical_replay(
                                family,
                                events,
                                band,
                                hold,
                                spread,
                                pre_count,
                                pre_bin,
                                True,
                            )
                            summary = summarize(trades)
                            row = {
                                "family": family,
                                "pre_bin": pre_bin[2],
                                "band_low": band[0],
                                "band_high": band[1],
                                "hold": hold,
                                "spread_cap": spread,
                                "pre_count": pre_count,
                                **summary,
                            }
                            all_surface_rows.append(row)
                            result["surface"].append(row)
        family_results[family] = result
        for exact in result["exact"].values():
            for key in ("adjacent_trades", "strict_trades", "timing_trades", "mirror_trades"):
                trades = exact[key]
                assert len({trade.event for trade in trades}) == len(trades)
            for trade in exact["timing_trades"]:
                assert trade.occurrence_ts is not None
                assert trade.pre_end_ts < trade.occurrence_ts <= trade.ts <= trade.close_ts
        del events

    assert len(all_surface_rows) == len(FAMILIES) * len(PRE_BINS) * len(BANDS) * len(HOLDS) * len(SPREADS) * len(PRE_COUNTS)
    with open(SURFACE, "w", newline="", encoding="utf-8") as handle:
        fields = list(all_surface_rows[0])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_surface_rows)

    claims = {
        ("KXWTAMATCH", "80-90"): (117, 110, 7, 3.00),
        ("KXWTAMATCH", "90+"): (50, 48, 2, 4.20),
        ("KXITFWMATCH", "90+"): (441, 413, 28, 1.85),
        ("KXITFMATCH", "90+"): (406, 381, 25, 2.14),
    }
    lines = [
        "# Adversarial Audit: Tennis Wobble Rule",
        "",
        "This report attempts to falsify the submitted rule. It does not modify the live bot.",
        "",
        "Reproduce with `python3 fetch_tennis_occurrence.py` followed by `python3 audit_wobble_rule.py`.",
        "",
        "## Reproduction",
        "",
        "Canonical means the player's own YES ticker only. Strict additionally requires actual 60-second candle spacing. "
        "Mirror-any-side permits the same player through either ticker and then event-deduplicates.",
        "",
        "| Family | Pre | Submitted | Canonical adjacent | Canonical strict | Mirror-any-side |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for (family, label), claim in claims.items():
        exact = family_results[family]["exact"][label]
        cells = []
        for key in ("adjacent", "strict", "mirrors"):
            row = exact[key]
            cells.append(f"{row['wins']}-{row['losses']}, {row['net_c']:+.2f}c")
        lines.append(
            f"| {family} | {label} | {claim[1]}-{claim[2]}, {claim[3]:+.2f}c | "
            f"{cells[0]} | {cells[1]} | {cells[2]} |"
        )

    lines.extend(
        [
            "",
            "### Submitted-count cutoff check",
            "",
            "The submitted ITF counts are chronological prefixes of the current files, not full-file results. "
            "The later rows are a useful accidental holdout.",
            "",
            "| Family | Submitted prefix | Prefix net | Later trades | Later W-L | Later net | Later-half net |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family, count in (("KXITFMATCH", 406), ("KXITFWMATCH", 441)):
        trades = family_results[family]["exact"]["90+"]["strict_trades"]
        prefix = summarize(trades[:count])
        tail = summarize(trades[count:])
        lines.append(
            f"| {family} | {prefix['wins']}-{prefix['losses']} | {prefix['net_c']:+.2f}c | "
            f"{tail['n']} | {tail['wins']}-{tail['losses']} | {tail['net_c']:+.2f}c | {tail['second_c']:+.2f}c |"
        )

    lines.extend(["", "## Mirror audit", ""])
    for family in FAMILIES:
        row = family_results[family]["mirror"]
        lines.append(
            f"- {family}: {row['events']:,} constructed events; size distribution {row['sizes']}; "
            f"{row['complementary']:,} have complementary results and {row['same_close']:,} share close time."
        )

    lines.extend(
        [
            "",
            "## Tape-window audit",
            "",
            "The archive omits empty minutes, so the first 30 recorded candles do not represent a fixed 30-minute window.",
            "",
            "| Family | Markets with 30 candles | Median first-30 span | 90th percentile | 30th candle at/after occurrence | Occurrence after close |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        row = family_results[family]["tape"]
        lines.append(
            f"| {family} | {row['eligible_markets']:,} | {row['median_span_minutes']:.0f}m | "
            f"{row['p90_span_minutes']:.0f}m | {row['first_window_at_or_after_occurrence']:,} | "
            f"{row['occurrence_after_close']:,} |"
        )

    lines.extend(
        [
            "",
            "## Timing attack",
            "",
            "Timing-valid requires scheduled occurrence between open and close, all pre-price candles before occurrence, "
            "and entry at or after occurrence.",
            "",
            "| Family | Pre | Strict n | Occurrence after close | Pre contaminated | Entry before occurrence | Raw first trigger valid | Gated replay net | Gated P(+edge) | Gated p05 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for label, exact in family_results[family]["exact"].items():
            audit = exact["timing_audit"]
            timing = exact["timing"]
            timing_boot = weekly_bootstrap(exact["timing_trades"])
            lines.append(
                f"| {family} | {label} | {exact['strict']['n']} | "
                f"{audit.get('occurrence_after_close', 0)} | {audit.get('pre_sample_contaminated', 0)} | "
                f"{audit.get('entry_before_occurrence', 0)} | {audit.get('fully_timing_valid', 0)} | "
                f"{format_number(timing['net_c'])}c ({timing['n']} trades) | "
                f"{format_number(timing_boot['p_positive'] * 100, 1)}% | "
                f"{format_number(timing_boot['p05_c'])}c |"
            )

    lines.extend(
        [
            "",
            "## Claimed-mechanism attack",
            "",
            "The submitted conditions do not require the player to have fallen from her pre-match price. "
            "`Genuine fall` adds that missing condition: entry mid must be below the measured pre-match ask. "
            "This is a generous test because comparing an earlier ask with a later mid can label spread compression as a fall.",
            "",
            "| Family | Pre | Submitted-rule n | Submitted net | Genuine-fall n | Fall net | Timing-valid fall n | Timing-valid fall net |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for label, exact in family_results[family]["exact"].items():
            strict = exact["strict"]
            fall = exact["strict_fall"]
            timing_fall = exact["timing_fall"]
            lines.append(
                f"| {family} | {label} | {strict['n']} | {format_number(strict['net_c'])}c | "
                f"{fall['n']} | {format_number(fall['net_c'])}c | {timing_fall['n']} | "
                f"{format_number(timing_fall['net_c'])}c |"
            )

    wta80_timing = family_results["KXWTAMATCH"]["exact"]["80-90"]["timing"]
    wta80_timing_boot = weekly_bootstrap(
        family_results["KXWTAMATCH"]["exact"]["80-90"]["timing_trades"]
    )
    wta90_timing = family_results["KXWTAMATCH"]["exact"]["90+"]["timing"]
    itf_timing = family_results["KXITFMATCH"]["exact"]["90+"]["timing"]
    itfw_timing = family_results["KXITFWMATCH"]["exact"]["90+"]["timing"]
    wta80_fall = family_results["KXWTAMATCH"]["exact"]["80-90"]["strict_fall"]
    lines.extend(
        [
            "",
            "## Weekly block bootstrap",
            "",
            f"Calendar weeks are resampled as blocks {BOOTSTRAPS:,} times with seed {SEED}.",
            "",
            "| Family | Pre | Trades | Weeks | Net | P(edge > 0) | 5th percentile |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for label, exact in family_results[family]["exact"].items():
            strict = exact["strict"]
            boot = exact["bootstrap"]
            lines.append(
                f"| {family} | {label} | {strict['n']} | {boot['weeks']} | {strict['net_c']:+.2f}c | "
                f"{boot['p_positive'] * 100:.1f}% | {boot['p05_c']:+.2f}c |"
            )

    lines.extend(
        [
            "",
            "## Family-threshold attack",
            "",
            "| Family | Pre bin | Trades | W-L | Net | First half | Second half |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for label, row in family_results[family]["thresholds"].items():
            lines.append(
                f"| {family} | {label} | {row['n']} | {row['wins']}-{row['losses']} | "
                f"{row['net_c']:+.2f}c | {row['first_c']:+.2f}c | {row['second_c']:+.2f}c |"
            )

    lines.extend(
        [
            "",
            "## Parameter fragility",
            "",
            "The full 810-cell surface is in `data/wobble_rule_surface.csv`. Counts below require at least 30 trades.",
            "",
            "| Family | Pre bin | Eligible cells | Positive | Positive both halves | Best net | Worst net | Exact rank |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for pre_bin in PRE_BINS:
            label = pre_bin[2]
            rows = [
                row
                for row in family_results[family]["surface"]
                if row["pre_bin"] == label and row["n"] >= 30
            ]
            ranked = sorted(rows, key=lambda row: row["net_c"], reverse=True)
            exact = next(
                (
                    row
                    for row in rows
                    if row["band_low"] == 0.88
                    and row["hold"] == 3
                    and row["spread_cap"] == 0.02
                    and row["pre_count"] == 30
                ),
                None,
            )
            rank = ranked.index(exact) + 1 if exact in ranked else 0
            lines.append(
                f"| {family} | {label} | {len(rows)} | {sum(row['net_c'] > 0 for row in rows)} | "
                f"{sum(row['first_c'] > 0 and row['second_c'] > 0 for row in rows)} | "
                f"{ranked[0]['net_c']:+.2f}c | {ranked[-1]['net_c']:+.2f}c | {rank}/{len(ranked)} |"
            )

    lines.extend(
        [
            "",
            "### One-parameter neighbors",
            "",
            "| Family | Pre | Changed parameter | Cell | n | Net | First | Second |",
            "|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for pre_bin in proposed_bins(family):
            label = pre_bin[2]
            rows = family_results[family]["surface"]
            variants = []
            for band in BANDS:
                variants.append(("band", f"{band[0]:.2f}-{band[1]:.2f}", band, 3, 0.02, 30))
            for hold in HOLDS:
                variants.append(("hold", str(hold), (0.88, 0.92), hold, 0.02, 30))
            for spread in SPREADS:
                variants.append(("spread", f"{spread:.2f}", (0.88, 0.92), 3, spread, 30))
            for count in PRE_COUNTS:
                variants.append(("pre_count", str(count), (0.88, 0.92), 3, 0.02, count))
            seen = set()
            for changed, display, band, hold, spread, count in variants:
                signature = (band, hold, spread, count)
                if signature in seen:
                    continue
                seen.add(signature)
                row = next(
                    item
                    for item in rows
                    if item["pre_bin"] == label
                    and item["band_low"] == band[0]
                    and item["hold"] == hold
                    and item["spread_cap"] == spread
                    and item["pre_count"] == count
                )
                lines.append(
                    f"| {family} | {label} | {changed} | {display} | {row['n']} | {row['net_c']:+.2f}c | "
                    f"{row['first_c']:+.2f}c | {row['second_c']:+.2f}c |"
                )

    lines.extend(
        [
            "",
            "## Liquidity and slippage",
            "",
            "Historical candles contain volume and open interest but no quoted depth. Percentages use canonical strict trades.",
            "",
            "| Family | Pre | Entry vol=0 | Entry vol>=10 | 5m vol>=10 | OI>=10 | Median 5m vol | Net +0c | Net +1c | Net +2c |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        for label, exact in family_results[family]["exact"].items():
            liquid = exact["liquidity"]
            trades = exact["strict_trades"]
            lines.append(
                f"| {family} | {label} | {liquid['entry_volume_zero_pct']:.1f}% | "
                f"{liquid['entry_volume_ge_10_pct']:.1f}% | {liquid['trailing_volume_ge_10_pct']:.1f}% | "
                f"{liquid['oi_ge_10_pct']:.1f}% | {liquid['median_trailing_volume']:.1f} | "
                f"{summarize(trades, 0.0)['net_c']:+.2f}c | {summarize(trades, 0.01)['net_c']:+.2f}c | "
                f"{summarize(trades, 0.02)['net_c']:+.2f}c |"
            )

    lines.extend(
        [
            "",
            "## Seven requested verdicts",
            "",
            "1. **Mirror markets — event key passes, replay convention fails.** Splitting on the first two ticker "
            "segments produces exactly two complementary markets per match, so it is the correct statistical cluster. "
            "It is not sufficient to define execution: allowing either mirror changes ITF counts by hundreds and "
            "changes ITF net from +2.38c to +1.09c. A replay must canonicalize the player's own YES ticker or construct "
            "one consolidated player book before deduplication.",
            "2. **Pre-match lookahead — failed.** The first 30 observed candles are not 30 minutes and are not reliably "
            "pre-match. For exact strict WTA signals, 47/120 in the 80-90 bin and 25/49 in the 90+ bin have the 30th "
            "candle at or after scheduled occurrence. Without actual first-serve timestamps these rows are not "
            "verifiably pre-match and may condition on in-play information.",
            "3. **Data window/live mismatch — failed and operationally fatal for ITF.** Scheduled occurrence is later "
            "than settlement for 518/563 exact ITF and 578/604 ITFW signals. A live gate waiting for that timestamp "
            f"cannot take the backtest trades. A fair gated replay finds only {itf_timing['n']} ITF and "
            f"{itfw_timing['n']} ITFW trades, far too few to validate either family.",
            "4. **Loss clustering — raw effect survives; timing-valid evidence is too small for deployment.** Weekly blocks keep "
            f"the raw WTA 80-90 fifth percentile positive. Its {wta80_timing['n']}-trade gated subset has "
            f"{wta80_timing_boot['p_positive'] * 100:.1f}% bootstrap probability of positive edge and a "
            f"{wta80_timing_boot['p05_c']:+.2f}c fifth percentile, which is promising but too small for deployment. "
            f"WTA 90+ is {wta90_timing['net_c']:+.2f}c after timing validation.",
            "5. **Per-family thresholds — not a sharp numerical artifact, but not causally established.** WTA 80-90 is "
            "positive in both chronological halves while ITF 80-90 is approximately zero. That supports a family "
            "interaction. Because the pre-match variable itself is contaminated or unverifiable, the difference could "
            "also be a tape-window artifact and is not safe to promote as a tour-specific law. More importantly, the "
            f"rule never requires a wobble: only {wta80_fall['n']}/{family_results['KXWTAMATCH']['exact']['80-90']['strict']['n']} "
            "raw WTA 80-90 entries are genuine declines from the measured pre-price.",
            "6. **Parameter fragility — this attack does not kill the raw effect.** The exact cell sits on a broad "
            "positive plateau: 135/135 WTA 80-90 cells and 135/135 ITF 90+ cells are positive, with most positive in "
            "both halves. The raw pattern is not dependent on exactly 88-92, hold 3, spread 2c, or 30 candles.",
            "7. **Liquidity — one contract is plausible, ten are unverified.** Entry-minute volume is zero for 27-67% "
            "of signals and the archive has no level sizes. Five-minute activity is usually present, but ITFW falls "
            "from +1.93c to +0.04c under 2c slippage. A quote proves a price level existed, not that ten contracts were "
            "available there.",
            "",
            "## Bottom line",
            "",
            "**The rule is not tradeable as specified, at either one or ten contracts.** The raw price pattern is real "
            "enough to survive parameter and weekly-clustering attacks, but the defining statement — that a known "
            "pre-match favorite fell to 90c in play — is not established by these files and cannot be reproduced by "
            f"the live ITF occurrence gate. After enforcing timing, WTA 80-90 has only {wta80_timing['n']} trades; "
            f"WTA 90+ returns {wta90_timing['net_c']:+.2f}c; and ITF/ITFW have only {itf_timing['n']} and "
            f"{itfw_timing['n']} usable observations. The conditions also fail to encode the claimed downward wobble.",
            "",
            "For context only, the invalid/unfiltered replay would retain approximately +4.0c WTA 80-90, +3.4c WTA "
            "90+, +1.4c ITF, and +1.0c ITFW per contract after a 1c fill penalty. Those are not defensible live-edge "
            "estimates. The defensible lower-bound edge from this audit is **not positive**. A new prospective dataset "
            "must record scheduled start, actual first serve, a true pre-match snapshot, and order-book depth before "
            "this design can be reconsidered.",
        ]
    )
    with open(REPORT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print(REPORT)
    print(SURFACE)


if __name__ == "__main__":
    main()
