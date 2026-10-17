#!/usr/bin/env python3
"""Search for causal, fee-positive structural arbitrage in expiry markets."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_common import fee, load_jsonl, number


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def utc_day(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")


def candle_map(path: Path, legacy: bool = False) -> dict[str, dict[int, dict]]:
    output = {}
    for record in load_jsonl(path):
        candles = {}
        for candle in record.get("candles", []):
            if legacy:
                candles[int(candle["t"])] = {
                    "bid": number(candle.get("b")),
                    "ask": number(candle.get("a")),
                }
            else:
                candles[int(candle["t"])] = {
                    "bid": number(candle.get("bc")),
                    "ask": number(candle.get("ac")),
                    "bid_open": number(candle.get("bo")),
                    "ask_open": number(candle.get("ao")),
                }
        output[record["ticker"]] = candles
    return output


def valid_quote(quote: dict | None, maximum_spread: float) -> bool:
    return bool(
        quote
        and quote["bid"] is not None
        and quote["ask"] is not None
        and 0.0 <= quote["bid"] <= quote["ask"] <= 1.0
        and quote["ask"] - quote["bid"] <= maximum_spread + 1e-9
    )


def cs2_pair_arbitrage(data_dir: Path, minimum_profit: float) -> dict:
    markets = load_jsonl(data_dir / "markets_KXCS2MAP.jsonl")
    candles = candle_map(data_dir / "candles_KXCS2MAP_1m.jsonl")
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        if market.get("event_ticker"):
            by_event[market["event_ticker"]].append(market)

    opportunities = []
    eligible_events = 0
    for event, event_markets in by_event.items():
        valid = [
            market
            for market in event_markets
            if market.get("result") in ("yes", "no")
            and market["ticker"] in candles
        ]
        if len(valid) != 2 or sum(market["result"] == "yes" for market in valid) != 1:
            continue
        eligible_events += 1
        first_ts = max(timestamp(market["open_time"]) for market in valid) + 20 * 60
        common_times = sorted(
            set(candles[valid[0]["ticker"]]).intersection(
                candles[valid[1]["ticker"]]
            )
        )
        for prior_ts in common_times:
            current_ts = prior_ts + 60
            if prior_ts < first_ts or current_ts not in common_times:
                continue
            prior_quotes = [
                candles[market["ticker"]].get(prior_ts) for market in valid
            ]
            current_quotes = [
                candles[market["ticker"]].get(current_ts) for market in valid
            ]
            if not all(valid_quote(quote, 0.04) for quote in prior_quotes):
                continue
            if not all(valid_quote(quote, 0.04) for quote in current_quotes):
                continue
            entries = [
                max(prior["ask"], current["ask"])
                for prior, current in zip(prior_quotes, current_quotes)
            ]
            locked_profit = 1.0 - sum(entries) - sum(fee(entry) for entry in entries)
            stress_profit = (
                1.0
                - sum(min(0.99, entry + 0.01) for entry in entries)
                - sum(fee(min(0.99, entry + 0.01)) for entry in entries)
            )
            if locked_profit < minimum_profit:
                continue
            opportunities.append(
                {
                    "strategy": "cs2_complement_arbitrage",
                    "event_ticker": event,
                    "tickers": [market["ticker"] for market in valid],
                    "ts": current_ts,
                    "cluster": utc_day(current_ts),
                    "entries": entries,
                    "locked_profit": locked_profit,
                    "stress_profit_plus_1c_each_leg": stress_profit,
                }
            )
            break
    return structural_summary(
        "cs2_complement_arbitrage",
        eligible_events,
        opportunities,
    )


def threshold_arbitrage(
    timely_dir: Path,
    series: tuple[str, ...],
    minimum_profit: float,
) -> dict:
    markets: list[dict] = []
    candles: dict[str, dict[int, dict]] = {}
    for ticker in series:
        markets.extend(load_jsonl(timely_dir / f"markets_{ticker}.jsonl"))
        candles.update(candle_map(timely_dir / f"candles_{ticker}.jsonl", legacy=True))
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        if (
            market.get("event_ticker")
            and market.get("strike_type") == "greater"
            and market.get("floor_strike") is not None
        ):
            by_event[market["event_ticker"]].append(market)

    opportunities = []
    for event, event_markets in by_event.items():
        ordered = sorted(event_markets, key=lambda market: market["floor_strike"])
        if len(ordered) < 2:
            continue
        close_ts = max(timestamp(market["close_time"]) for market in ordered)
        for current_ts in range(close_ts - 12 * 3600, close_ts - 3600, 3600):
            prior_ts = current_ts - 3600
            choices = []
            for lower, higher in zip(ordered, ordered[1:]):
                lower_prior = candles.get(lower["ticker"], {}).get(prior_ts)
                higher_prior = candles.get(higher["ticker"], {}).get(prior_ts)
                lower_now = candles.get(lower["ticker"], {}).get(current_ts)
                higher_now = candles.get(higher["ticker"], {}).get(current_ts)
                quotes = (lower_prior, higher_prior, lower_now, higher_now)
                if not all(valid_quote(quote, 0.04) for quote in quotes):
                    continue
                yes_lower = max(lower_prior["ask"], lower_now["ask"])
                no_higher = max(
                    1.0 - higher_prior["bid"],
                    1.0 - higher_now["bid"],
                )
                locked_profit = (
                    1.0
                    - yes_lower
                    - no_higher
                    - fee(yes_lower)
                    - fee(no_higher)
                )
                choices.append(
                    (
                        locked_profit,
                        lower,
                        higher,
                        yes_lower,
                        no_higher,
                    )
                )
            if not choices:
                continue
            profit, lower, higher, yes_lower, no_higher = max(
                choices,
                key=lambda choice: choice[0],
            )
            if profit < minimum_profit:
                continue
            stress_entries = (
                min(0.99, yes_lower + 0.01),
                min(0.99, no_higher + 0.01),
            )
            stress_profit = (
                1.0
                - sum(stress_entries)
                - sum(fee(entry) for entry in stress_entries)
            )
            opportunities.append(
                {
                    "strategy": "threshold_monotonic_arbitrage",
                    "event_ticker": event,
                    "tickers": [lower["ticker"], higher["ticker"]],
                    "ts": current_ts,
                    "cluster": utc_day(current_ts),
                    "entries": [yes_lower, no_higher],
                    "locked_profit": profit,
                    "stress_profit_plus_1c_each_leg": stress_profit,
                    "lower_strike": lower["floor_strike"],
                    "higher_strike": higher["floor_strike"],
                }
            )
            break
    return structural_summary(
        "threshold_monotonic_arbitrage",
        len(by_event),
        opportunities,
    )


def structural_summary(
    strategy: str,
    eligible_events: int,
    opportunities: list[dict],
) -> dict:
    profits = [item["locked_profit"] for item in opportunities]
    stress = [
        item["stress_profit_plus_1c_each_leg"] for item in opportunities
    ]
    days = {item["cluster"] for item in opportunities}
    return {
        "strategy": strategy,
        "eligible_events": eligible_events,
        "opportunities": len(opportunities),
        "opportunity_rate": (
            len(opportunities) / eligible_events if eligible_events else 0.0
        ),
        "active_days": len(days),
        "average_locked_profit_cents": (
            100.0 * sum(profits) / len(profits) if profits else 0.0
        ),
        "minimum_locked_profit_cents": (
            100.0 * min(profits) if profits else 0.0
        ),
        "plus_1c_each_leg_survivors": sum(value > 0 for value in stress),
        "average_stress_profit_cents": (
            100.0 * sum(stress) / len(stress) if stress else 0.0
        ),
        "records": opportunities,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--timely-dir",
        type=Path,
        default=Path("../kalshi_timely_2026_07/data"),
    )
    parser.add_argument("--output", type=Path, default=Path("results/structural.json"))
    parser.add_argument("--minimum-profit-cents", type=float, default=0.5)
    args = parser.parse_args()
    minimum_profit = args.minimum_profit_cents / 100.0
    results = [
        cs2_pair_arbitrage(args.data_dir, minimum_profit),
        threshold_arbitrage(
            args.timely_dir,
            ("KXWTI", "KXBRENTD", "KXGOLDD"),
            minimum_profit,
        ),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            [
                {key: value for key, value in result.items() if key != "records"}
                for result in results
            ],
            indent=2,
            sort_keys=True,
        )
    )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
