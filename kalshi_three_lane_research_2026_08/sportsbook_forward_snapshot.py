#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import DATA, RESULTS, as_float, dump_json, fee, get_json


SPORTS = {
    "baseball_mlb": {
        "series": ("KXMLBSPREAD", "KXMLBTOTAL", "KXMLBHIT", "KXMLBTB", "KXMLBHR", "KXMLBKS"),
        "props": ("batter_hits", "batter_total_bases", "batter_home_runs", "pitcher_strikeouts"),
    },
    "basketball_wnba": {
        "series": ("KXWNBASPREAD", "KXWNBATOTAL", "KXWNBAPTS", "KXWNBAREB", "KXWNBAAST", "KXWNBA3PT"),
        "props": ("player_points", "player_rebounds", "player_assists", "player_threes"),
    },
}
TEAM_CODES = {
    "baseball_mlb": {
        "Arizona Diamondbacks": "AZ", "Athletics": "ATH", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
        "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN",
        "Cleveland Guardians": "CLE", "Colorado Rockies": "COL", "Detroit Tigers": "DET", "Houston Astros": "HOU",
        "Kansas City Royals": "KC", "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
        "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM", "New York Yankees": "NYY",
        "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD", "San Francisco Giants": "SF",
        "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
        "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
    },
    "basketball_wnba": {
        "Atlanta Dream": "ATL", "Chicago Sky": "CHI", "Connecticut Sun": "CON", "Dallas Wings": "DAL",
        "Golden State Valkyries": "GS", "Indiana Fever": "IND", "Las Vegas Aces": "LV", "Los Angeles Sparks": "LA",
        "Minnesota Lynx": "MIN", "New York Liberty": "NY", "Phoenix Mercury": "PHX", "Portland Fire": "PDX",
        "Seattle Storm": "SEA", "Toronto Tempo": "TOR", "Washington Mystics": "WSH",
    },
}


def load_api_key() -> str:
    if os.environ.get("ODDS_API_KEY"):
        return os.environ["ODDS_API_KEY"]
    candidates = (
        Path("/Users/varmakammili/Documents/GitHub/mlb-arb-bot/.env"),
        Path("/Users/varmakammili/Documents/GitHub/Polymarket-Sports/.env"),
    )
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("ODDS_API_KEY not found")


def odds_get(path: str, key: str, params: dict[str, Any]) -> tuple[Any, dict[str, str]]:
    query = {**params, "apiKey": key}
    url = "https://api.the-odds-api.com/v4" + path + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-three-lane-research/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response), {
            "requests_remaining": response.headers.get("x-requests-remaining", ""),
            "requests_used": response.headers.get("x-requests-used", ""),
            "requests_last": response.headers.get("x-requests-last", ""),
        }


def no_vig_rows(sport: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    metadata: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in events:
        for bookmaker in event.get("bookmakers") or []:
            for market in bookmaker.get("markets") or []:
                outcomes = market.get("outcomes") or []
                by_description: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for outcome in outcomes:
                    by_description[str(outcome.get("description") or "")].append(outcome)
                for description, paired in by_description.items():
                    if len(paired) != 2 or len({str(outcome.get("name") or "") for outcome in paired}) != 2:
                        continue
                    implied = [1.0 / float(outcome["price"]) for outcome in paired if float(outcome.get("price") or 0) > 1.0]
                    if len(implied) != 2 or sum(implied) <= 0:
                        continue
                    for outcome, raw_probability in zip(paired, implied):
                        key = (
                            sport,
                            event["id"],
                            market["key"],
                            str(outcome.get("name") or ""),
                            description,
                            as_float(outcome.get("point")),
                        )
                        grouped[key].append(raw_probability / sum(implied))
                        metadata[key] = {
                            "sport": sport,
                            "event_id": event["id"],
                            "commence_time": event["commence_time"],
                            "home_team": event["home_team"],
                            "away_team": event["away_team"],
                            "market": market["key"],
                            "name": str(outcome.get("name") or ""),
                            "description": description,
                            "point": as_float(outcome.get("point")),
                        }
    return [
        {
            **metadata[key],
            "bookmakers": len(probabilities),
            "consensus_no_vig": sum(probabilities) / len(probabilities),
            "bookmaker_min": min(probabilities),
            "bookmaker_max": max(probabilities),
        }
        for key, probabilities in grouped.items()
    ]


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def current_kalshi_markets(series: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/markets", params)
        rows.extend(payload.get("markets") or [])
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            return rows


def candidate_match(market: dict[str, Any], consensus: dict[str, Any]) -> bool:
    occurrence = market.get("occurrence_datetime")
    if not occurrence:
        return False
    kalshi_time = dt.datetime.fromisoformat(occurrence.replace("Z", "+00:00")) - dt.timedelta(hours=3)
    book_time = dt.datetime.fromisoformat(consensus["commence_time"].replace("Z", "+00:00"))
    if abs((kalshi_time - book_time).total_seconds()) > 15 * 60:
        return False
    codes = TEAM_CODES.get(consensus["sport"], {})
    home_code = codes.get(consensus["home_team"])
    away_code = codes.get(consensus["away_team"])
    event_token = str(market.get("event_ticker") or "").split("-")[-1]
    if not home_code or not away_code or home_code not in event_token or away_code not in event_token:
        return False
    floor = as_float(market.get("floor_strike"))
    point = consensus.get("point")
    if floor is None or point is None:
        return False
    title = normalize(str(market.get("title") or ""))
    description = normalize(str(consensus.get("description") or ""))
    series = str(market["ticker"]).split("-")[0]
    if series.endswith("TOTAL"):
        return consensus["market"] == "totals" and consensus["name"] == "Over" and abs(point - floor) < 0.01
    if series.endswith("SPREAD"):
        team_location = normalize(" ".join(consensus["name"].split()[:-1]))
        return consensus["market"] == "spreads" and team_location in title and abs(point + floor) < 0.01
    prop_map = {
        "KXWNBAPTS": "player_points",
        "KXWNBAREB": "player_rebounds",
        "KXWNBAAST": "player_assists",
        "KXWNBA3PT": "player_threes",
        "KXMLBHIT": "batter_hits",
        "KXMLBTB": "batter_total_bases",
        "KXMLBHR": "batter_home_runs",
        "KXMLBKS": "pitcher_strikeouts",
    }
    return (
        consensus["market"] == prop_map.get(series)
        and consensus["name"] == "Over"
        and description
        and description in title
        and abs(point - floor) < 0.01
    )


def main() -> None:
    key = load_api_key()
    raw_cache = DATA / f"sportsbook_raw_{dt.datetime.now(dt.timezone.utc).date().isoformat()}.json"
    if raw_cache.exists() and time.time() - raw_cache.stat().st_mtime < 120:
        cached = json.loads(raw_cache.read_text())
        raw_events = cached["events"]
        quota = cached["quota"]
    else:
        raw_events: dict[str, list[dict[str, Any]]] = {}
        quota: list[dict[str, str]] = []
        for sport in SPORTS:
            events, headers = odds_get(
                f"/sports/{sport}/odds/",
                key,
                {"regions": "us", "markets": "spreads,totals", "oddsFormat": "decimal", "dateFormat": "iso"},
            )
            raw_events[sport] = events
            quota.append(headers)
            # One timestamped event per sport is enough to verify prop overlap without
            # burning credits across every game before the forward ledger is running.
            if events:
                try:
                    prop_event, headers = odds_get(
                        f"/sports/{sport}/events/{events[0]['id']}/odds",
                        key,
                        {
                            "regions": "us",
                            "markets": ",".join(SPORTS[sport]["props"]),
                            "oddsFormat": "decimal",
                            "dateFormat": "iso",
                        },
                    )
                    raw_events[sport].append(prop_event)
                    quota.append(headers)
                except urllib.error.HTTPError as exc:
                    quota.append({"prop_error": f"HTTP {exc.code}", "sport": sport})
        dump_json(raw_cache, {"events": raw_events, "quota": quota})

    consensus = [row for sport, events in raw_events.items() for row in no_vig_rows(sport, events)]
    kalshi: list[dict[str, Any]] = []
    for config in SPORTS.values():
        for series in config["series"]:
            kalshi.extend(current_kalshi_markets(series))
    comparisons: list[dict[str, Any]] = []
    for market in kalshi:
        ask = as_float(market.get("yes_ask_dollars"))
        bid = as_float(market.get("yes_bid_dollars"))
        no_ask = as_float(market.get("no_ask_dollars"))
        no_bid = as_float(market.get("no_bid_dollars"))
        if ask is None or bid is None or no_ask is None or no_bid is None or ask <= 0 or ask > 1 or no_ask <= 0 or no_ask > 1:
            continue
        for row in consensus:
            if not candidate_match(market, row):
                continue
            fair = float(row["consensus_no_vig"])
            yes_stressed = fair - min(1.0, ask + 0.01) - fee(min(1.0, ask + 0.01))
            no_stressed = (1.0 - fair) - min(1.0, no_ask + 0.01) - fee(min(1.0, no_ask + 0.01))
            comparisons.append({
                "ticker": market["ticker"],
                "series": market["ticker"].split("-")[0],
                "yes_bid": bid,
                "yes_ask": ask,
                "spread": ask - bid,
                "yes_ask_size": as_float(market.get("yes_ask_size_fp")),
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_spread": no_ask - no_bid,
                "consensus_no_vig": fair,
                "bookmakers": row["bookmakers"],
                "raw_edge_after_fee": fair - ask - fee(ask),
                "one_cent_stressed_edge": max(yes_stressed, no_stressed),
                "yes_one_cent_stressed_edge": yes_stressed,
                "no_one_cent_stressed_edge": no_stressed,
                "best_side": "yes" if yes_stressed >= no_stressed else "no",
                "sportsbook": row,
            })
    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "quota": quota,
        "sportsbook_events": {sport: len(events) for sport, events in raw_events.items()},
        "consensus_rows": len(consensus),
        "consensus": consensus,
        "kalshi_open_markets": len(kalshi),
        "matched_contracts": len(comparisons),
        "comparisons": sorted(comparisons, key=lambda row: row["one_cent_stressed_edge"], reverse=True),
    }
    dump_json(RESULTS / "sportsbook_forward_snapshot.json", output)
    print(f"Consensus outcomes: {len(consensus):,}")
    print(f"Kalshi open contracts checked: {len(kalshi):,}")
    print(f"Exact line/time matches: {len(comparisons):,}")
    if comparisons:
        best = max(comparisons, key=lambda row: row["one_cent_stressed_edge"])
        print("Best stressed discrepancy:", best["ticker"], f"{best['one_cent_stressed_edge']:.4f}")


if __name__ == "__main__":
    main()
