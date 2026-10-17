#!/usr/bin/env python3
"""Read-only scan of open Kalshi markets for fee-positive structural trades."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research_common import fee, load_jsonl

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def get_json(path: str, params: dict[str, object], retries: int = 6) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "kalshi-expiry-research/1.0"},
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def paged(path: str, key: str, limit: int) -> list[dict]:
    output = []
    cursor = None
    while True:
        params: dict[str, object] = {"status": "open", "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = get_json(path, params)
        page = response.get(key, [])
        output.extend(page)
        cursor = response.get("cursor")
        if not cursor or not page:
            break
        time.sleep(0.1)
    return output


def quote(market: dict) -> tuple[float, float] | None:
    try:
        bid = float(market["yes_bid_dollars"])
        ask = float(market["yes_ask_dollars"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= bid <= ask <= 1:
        return None
    return bid, ask


def main() -> None:
    inventory = [
        row
        for row in load_jsonl(Path("data/inventory.jsonl"))
        if not row.get("error")
    ]
    candidates = list(
        {
            row["ticker"]: row
            for row in inventory
            if row["ticker"] == "KXCS2MAP"
            or (
                row.get("sample_strike_type") == "greater"
                and row.get("median_market_volume", 0) >= 100
            )
        }.values()
    )
    markets = []
    series_by_event: dict[str, str] = {}
    event_meta: dict[str, dict] = {}
    for index, row in enumerate(candidates, start=1):
        response = get_json(
            "/markets",
            {
                "series_ticker": row["ticker"],
                "status": "open",
                "limit": 1000,
            },
        )
        for market in response.get("markets", []):
            if market.get("mve_collection_ticker"):
                continue
            markets.append(market)
            if market.get("event_ticker"):
                series_by_event[market["event_ticker"]] = row["ticker"]
        event_response = get_json(
            "/events",
            {
                "series_ticker": row["ticker"],
                "status": "open",
                "limit": 200,
            },
        )
        for event in event_response.get("events", []):
            event_meta[event["event_ticker"]] = event
        if index % 25 == 0:
            print(f"scanned {index}/{len(candidates)} recurring series", flush=True)
        time.sleep(0.25)
    markets = list({market["ticker"]: market for market in markets}.values())
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        if market.get("event_ticker"):
            by_event[market["event_ticker"]].append(market)

    opportunities = []
    for event_ticker, event_markets in by_event.items():
        series = series_by_event.get(event_ticker)

        meta = event_meta.get(event_ticker, {})
        if meta.get("mutually_exclusive") and len(event_markets) >= 2:
            quotes = [quote(market) for market in event_markets]
            if all(quotes):
                entries = [1.0 - item[0] for item in quotes]
                if any(entry > 0.99 for entry in entries):
                    entries = []
            if all(quotes) and entries:
                guaranteed_payout = len(entries) - 1.0
                profit = (
                    guaranteed_payout
                    - sum(entries)
                    - sum(fee(entry) for entry in entries)
                )
                stress_entries = [entry + 0.01 for entry in entries]
                stress = (
                    (
                        guaranteed_payout
                        - sum(stress_entries)
                        - sum(fee(entry) for entry in stress_entries)
                    )
                    if all(entry <= 0.99 for entry in stress_entries)
                    else -1.0
                )
                if profit > 0:
                    opportunities.append(
                        {
                            "type": "mutually_exclusive_no_basket",
                            "series_ticker": series,
                            "event_ticker": event_ticker,
                            "tickers": [
                                market["ticker"] for market in event_markets
                            ],
                            "entries": entries,
                            "guaranteed_payout": guaranteed_payout,
                            "locked_profit": profit,
                            "stress_profit_plus_1c_each_leg": stress,
                        }
                    )

        if series == "KXCS2MAP" and len(event_markets) == 2:
            quotes = [quote(market) for market in event_markets]
            if all(quotes):
                entries = [item[1] for item in quotes]
                if any(entry > 0.99 for entry in entries):
                    continue
                profit = 1.0 - sum(entries) - sum(fee(entry) for entry in entries)
                stress_entries = [entry + 0.01 for entry in entries]
                stress = (
                    (
                        1.0
                        - sum(stress_entries)
                        - sum(fee(entry) for entry in stress_entries)
                    )
                    if all(entry <= 0.99 for entry in stress_entries)
                    else -1.0
                )
                if profit > 0:
                    opportunities.append(
                        {
                            "type": "cs2_complement",
                            "event_ticker": event_ticker,
                            "tickers": [market["ticker"] for market in event_markets],
                            "entries": entries,
                            "locked_profit": profit,
                            "stress_profit_plus_1c_each_leg": stress,
                        }
                    )

        thresholds = [
            market
            for market in event_markets
            if market.get("strike_type") == "greater"
            and market.get("floor_strike") is not None
            and quote(market) is not None
        ]
        thresholds.sort(key=lambda market: float(market["floor_strike"]))
        for lower, higher in zip(thresholds, thresholds[1:]):
            if float(lower["floor_strike"]) >= float(higher["floor_strike"]):
                continue
            lower_bid, lower_ask = quote(lower)
            higher_bid, _ = quote(higher)
            entries = [lower_ask, 1.0 - higher_bid]
            if any(entry > 0.99 for entry in entries):
                continue
            profit = 1.0 - sum(entries) - sum(fee(entry) for entry in entries)
            stress_entries = [entry + 0.01 for entry in entries]
            stress = (
                (
                    1.0
                    - sum(stress_entries)
                    - sum(fee(entry) for entry in stress_entries)
                )
                if all(entry <= 0.99 for entry in stress_entries)
                else -1.0
            )
            if profit > 0:
                opportunities.append(
                    {
                        "type": "threshold_monotonic",
                        "series_ticker": series,
                        "event_ticker": event_ticker,
                        "tickers": [lower["ticker"], higher["ticker"]],
                        "strikes": [
                            lower["floor_strike"],
                            higher["floor_strike"],
                        ],
                        "entries": entries,
                        "locked_profit": profit,
                        "stress_profit_plus_1c_each_leg": stress,
                    }
                )

    output = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "screened_series": len(candidates),
        "open_events": len(by_event),
        "open_markets": len(markets),
        "opportunities": sorted(
            opportunities,
            key=lambda item: -item["locked_profit"],
        ),
    }
    path = Path("results/current_structural_scan.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "run_utc": output["run_utc"],
                "screened_series": output["screened_series"],
                "open_events": output["open_events"],
                "open_markets": output["open_markets"],
                "opportunity_count": len(opportunities),
                "positive_after_plus_1c_each_leg": sum(
                    item["stress_profit_plus_1c_each_leg"] > 0
                    for item in opportunities
                ),
                "top_opportunities": output["opportunities"][:20],
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
