#!/usr/bin/env python3
"""Read-only, all-open-market scan for guaranteed-payoff inequalities."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from common import fee


BASE = "https://api.elections.kalshi.com/trade-api/v2"


def request_json(path: str, params: dict, retries: int = 7) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            response = subprocess.run(
                [
                    "curl",
                    "--fail-with-body",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "30",
                    "--user-agent",
                    "universe-arbitrage-audit/1.0",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=35,
            )
            return json.loads(response.stdout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def paged(
    path: str,
    key: str,
    *,
    limit: int,
    extra_params: Optional[dict] = None,
) -> list[dict]:
    rows = []
    cursor = None
    while True:
        params = {"status": "open", "limit": limit, **(extra_params or {})}
        if cursor:
            params["cursor"] = cursor
        response = request_json(path, params)
        page = response.get(key, [])
        rows.extend(page)
        cursor = response.get("cursor")
        if not cursor or not page:
            return rows
        time.sleep(0.12)


def quote(market: dict, side: str) -> tuple[float, float, float | None] | None:
    try:
        yes_bid = float(market["yes_bid_dollars"])
        yes_ask = float(market["yes_ask_dollars"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= yes_bid <= yes_ask <= 1.0:
        return None
    if side == "yes":
        size = market.get("yes_ask_size_fp")
        size_value = float(size) if size is not None else None
        if yes_ask <= 0.0 or size_value is None or size_value < 1.0:
            return None
        return yes_bid, yes_ask, size_value
    size = market.get("yes_bid_size_fp")
    size_value = float(size) if size is not None else None
    no_ask = 1.0 - yes_bid
    if no_ask <= 0.0 or size_value is None or size_value < 1.0:
        return None
    return 1.0 - yes_ask, no_ask, size_value


def threshold_scope(market: dict) -> Optional[str]:
    """Return a machine-verifiable identity for the measured object.

    Numeric strike fields alone are insufficient: sports events contain
    thresholds for both teams, and rain events contain one zero strike per
    city. A shared non-empty custom strike is the strongest public metadata
    evidence that two contracts threshold the same underlying object.
    """
    custom_strike = market.get("custom_strike")
    if not custom_strike:
        return None
    return json.dumps(custom_strike, sort_keys=True, separators=(",", ":"))


def basket_profit(entries: list[float], guaranteed_payout: float) -> float:
    return guaranteed_payout - sum(entries) - sum(fee(entry) for entry in entries)


def stressed(entries: list[float], guaranteed_payout: float) -> float:
    stressed_entries = [entry + 0.01 for entry in entries]
    if any(entry > 0.99 for entry in stressed_entries):
        return -1.0
    return basket_profit(stressed_entries, guaranteed_payout)


def main() -> None:
    events = paged(
        "/events",
        "events",
        limit=200,
        extra_params={"with_nested_markets": "true"},
    )
    event_meta = {event["event_ticker"]: event for event in events}
    by_event: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        for market in event.get("markets", []):
            if market.get("event_ticker") and not market.get("mve_collection_ticker"):
                by_event[market["event_ticker"]].append(market)

    opportunities = []
    for event_ticker, event_markets in by_event.items():
        meta = event_meta.get(event_ticker, {})
        if meta.get("mutually_exclusive") and len(event_markets) >= 2:
            legs = [(market, quote(market, "no")) for market in event_markets]
            if all(item[1] is not None for item in legs):
                entries = [item[1][1] for item in legs]
                guaranteed = len(entries) - 1.0
                profit = basket_profit(entries, guaranteed)
                if profit > 0:
                    opportunities.append(
                        {
                            "type": "mutually_exclusive_no_basket",
                            "event_ticker": event_ticker,
                            "series_ticker": meta.get("series_ticker"),
                            "legs": [
                                {
                                    "ticker": market["ticker"],
                                    "side": "no",
                                    "entry": leg[1],
                                    "displayed_size": leg[2],
                                }
                                for market, leg in legs
                            ],
                            "guaranteed_payout": guaranteed,
                            "profit_per_basket": profit,
                            "stress_profit_plus_1c_each_leg": stressed(entries, guaranteed),
                        }
                    )

        thresholds_by_scope: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for market in event_markets:
            strike_type = market.get("strike_type")
            scope = threshold_scope(market)
            if (
                strike_type in ("greater", "greater_or_equal")
                and market.get("floor_strike") is not None
                and scope is not None
            ):
                thresholds_by_scope[(strike_type, scope)].append(market)

        for (strike_type, scope), thresholds in thresholds_by_scope.items():
            thresholds.sort(key=lambda market: float(market["floor_strike"]))
            for lower, higher in zip(thresholds, thresholds[1:]):
                lower_strike = float(lower["floor_strike"])
                higher_strike = float(higher["floor_strike"])
                if lower_strike >= higher_strike:
                    continue
                lower_yes = quote(lower, "yes")
                higher_no = quote(higher, "no")
                if lower_yes is None or higher_no is None:
                    continue
                entries = [lower_yes[1], higher_no[1]]
                profit = basket_profit(entries, 1.0)
                if profit > 0:
                    opportunities.append(
                        {
                            "type": "threshold_monotonic",
                            "event_ticker": event_ticker,
                            "series_ticker": meta.get("series_ticker"),
                            "strike_type": strike_type,
                            "scope": scope,
                            "strikes": [lower["floor_strike"], higher["floor_strike"]],
                            "legs": [
                                {
                                    "ticker": lower["ticker"],
                                    "side": "yes",
                                    "entry": lower_yes[1],
                                    "displayed_size": lower_yes[2],
                                },
                                {
                                    "ticker": higher["ticker"],
                                    "side": "no",
                                    "entry": higher_no[1],
                                    "displayed_size": higher_no[2],
                                },
                            ],
                            "guaranteed_payout": 1.0,
                            "profit_per_basket": profit,
                            "stress_profit_plus_1c_each_leg": stressed(entries, 1.0),
                        }
                    )

    opportunities.sort(key=lambda row: -row["profit_per_basket"])
    payload = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "open_markets": sum(len(markets) for markets in by_event.values()),
        "open_events": len(by_event),
        "event_metadata": len(event_meta),
        "opportunities": opportunities,
        "positive_after_plus_1c_each_leg": sum(
            row["stress_profit_plus_1c_each_leg"] > 0 for row in opportunities
        ),
    }
    path = Path("results/current_structural_scan_all.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({**payload, "opportunities": opportunities[:20]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
