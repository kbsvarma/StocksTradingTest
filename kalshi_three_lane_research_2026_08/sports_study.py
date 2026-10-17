#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import DATA, RESULTS, as_float, cluster_bootstrap, dump_json, fee, get_json


FAMILIES = (
    "KXWNBASPREAD",
    "KXWNBATOTAL",
    "KXMLBSPREAD",
    "KXMLBTOTAL",
    "KXWNBAPTS",
    "KXWNBAREB",
    "KXWNBAAST",
    "KXWNBA3PT",
    "KXMLBHIT",
    "KXMLBTB",
    "KXMLBHR",
    "KXMLBKS",
)
OFFSETS_MINUTES = (60, 30, 15, 5)


def parse_ts(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def scheduled_start_ts(market: dict[str, Any]) -> int:
    # Kalshi sports occurrence_datetime is the expected game end. Current WNBA
    # and MLB schedules independently verify a three-hour start-to-occurrence offset.
    return parse_ts(market["occurrence_datetime"]) - 3 * 3600


def market_identity(market: dict[str, Any]) -> str:
    custom = market.get("custom_strike") or {}
    for key in ("basketball_player", "baseball_player", "basketball_team", "baseball_team"):
        if custom.get(key):
            return f"{key}:{custom[key]}"
    return "event_total"


def fetch_family(series: str) -> list[dict[str, Any]]:
    cache = DATA / f"sports_markets_{series}.json"
    if cache.exists():
        import json

        return json.loads(cache.read_text())
    markets: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"series_ticker": series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/markets", params)
        markets.extend(payload.get("markets") or [])
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break
    dump_json(cache, markets)
    return markets


def fetch_event_candles(series: str, event: str, markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    cache = DATA / "sports_event_candles_pregame" / series / f"{event}.json"
    if cache.exists():
        import json

        return json.loads(cache.read_text())
    start_of_game = scheduled_start_ts(markets[0])
    start_ts = max(min(parse_ts(market["open_time"]) for market in markets), start_of_game - 65 * 60)
    end_ts = min(max(parse_ts(market["close_time"]) for market in markets), start_of_game + 5 * 60)
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cursor_start = start_ts
    while cursor_start < end_ts:
        payload = get_json(
            f"/series/{series}/events/{event}/candlesticks",
            {"start_ts": cursor_start, "end_ts": end_ts, "period_interval": 1},
        )
        for ticker, candles in zip(payload.get("market_tickers") or [], payload.get("market_candlesticks") or []):
            rows[ticker].extend(candle for candle in candles if int(candle["end_period_ts"]) >= cursor_start)
        adjusted_end = int(payload.get("adjusted_end_ts") or end_ts)
        if adjusted_end >= end_ts or adjusted_end <= cursor_start:
            break
        cursor_start = adjusted_end + 1
    rows = {ticker: sorted(candles, key=lambda candle: int(candle["end_period_ts"])) for ticker, candles in rows.items()}
    dump_json(cache, rows)
    return rows


def quote_at_or_after(candles: list[dict[str, Any]], target_ts: int, max_delay: int = 300) -> dict[str, float] | None:
    for candle in candles:
        timestamp = int(candle["end_period_ts"])
        if timestamp < target_ts:
            continue
        if timestamp > target_ts + max_delay:
            return None
        bid = as_float((candle.get("yes_bid") or {}).get("close_dollars"))
        ask = as_float((candle.get("yes_ask") or {}).get("close_dollars"))
        if bid is None or ask is None or ask <= 0 or ask > 1 or bid < 0 or bid > ask:
            continue
        return {
            "timestamp": float(timestamp),
            "yes_bid": bid,
            "yes_ask": ask,
            "volume": as_float(candle.get("volume_fp")) or 0.0,
        }
    return None


def side_quote(quote: dict[str, float], side: str) -> tuple[float, float]:
    if side == "yes":
        return quote["yes_bid"], quote["yes_ask"]
    return 1.0 - quote["yes_ask"], 1.0 - quote["yes_bid"]


def pnl(result: str, side: str, ask: float, stress: float = 0.0) -> float:
    paid = min(1.0, ask + stress)
    return (1.0 if result == side else 0.0) - paid - fee(paid)


def summarize_calibration(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bin_floor = min(95, int(math.floor(row["ask"] * 20) * 5))
        grouped[(row["series"], row["offset_minutes"], bin_floor)].append(row)
    output: list[dict[str, Any]] = []
    for (series, offset, bin_floor), group in sorted(grouped.items()):
        if not group:
            continue
        output.append({
            "series": series,
            "offset_minutes": offset,
            "price_bin": f"{bin_floor:02d}-{bin_floor + 5:02d}",
            "events": len(group),
            "mean_ask": sum(row["ask"] for row in group) / len(group),
            "win_rate": sum(row["won"] for row in group) / len(group),
            "mean_net": sum(row["net"] for row in group) / len(group),
            "mean_net_stressed": sum(row["net_stressed"] for row in group) / len(group),
        })
    return output


def chronological_splits(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    events = sorted({(row["occurrence_ts"], row["event"]) for row in rows})
    if len(events) < 40:
        return {"all": rows}
    first = int(len(events) * 0.50)
    second = int(len(events) * 0.75)
    labels = {
        event: "train" if index < first else "validation" if index < second else "test"
        for index, (_, event) in enumerate(events)
    }
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[labels[row["event"]]].append(row)
    return dict(output)


def process_event(series: str, event: str, markets: list[dict[str, Any]]) -> dict[str, Any]:
    candles_by_ticker = fetch_event_candles(series, event, markets)
    occurrence_ts = parse_ts(markets[0]["occurrence_datetime"])
    start_of_game = scheduled_start_ts(markets[0])
    calibration_rows: list[dict[str, Any]] = []
    arb_rows: list[dict[str, Any]] = []

    for offset in OFFSETS_MINUTES:
        target_ts = start_of_game - offset * 60
        quoted: list[tuple[dict[str, Any], dict[str, float]]] = []
        for market in markets:
            quote = quote_at_or_after(candles_by_ticker.get(market["ticker"], []), target_ts)
            if quote is not None:
                quoted.append((market, quote))

        # A single near-even contract per event avoids treating a strike ladder as independent evidence.
        eligible = [
            (market, quote)
            for market, quote in quoted
            if quote["yes_ask"] - quote["yes_bid"] <= 0.05 and market.get("result") in ("yes", "no")
        ]
        if eligible:
            market, quote = min(
                eligible,
                key=lambda item: (
                    abs(((item[1]["yes_bid"] + item[1]["yes_ask"]) / 2.0) - 0.5),
                    -float(item[0].get("volume_fp") or 0),
                ),
            )
            yes_mid = (quote["yes_bid"] + quote["yes_ask"]) / 2.0
            side = "yes" if yes_mid >= 0.5 else "no"
            bid, ask = side_quote(quote, side)
            result = str(market["result"])
            calibration_rows.append({
                "series": series,
                "event": event,
                "ticker": market["ticker"],
                "occurrence_ts": occurrence_ts,
                "scheduled_start_ts": start_of_game,
                "offset_minutes": offset,
                "side": side,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "won": result == side,
                "net": pnl(result, side, ask),
                "net_stressed": pnl(result, side, ask, 0.01),
            })

        groups: dict[str, list[tuple[dict[str, Any], dict[str, float]]]] = defaultdict(list)
        for market, quote in quoted:
            strike = as_float(market.get("floor_strike"))
            if strike is None:
                continue
            groups[market_identity(market)].append((market, quote))
        for identity, ladder in groups.items():
            ladder.sort(key=lambda item: float(item[0]["floor_strike"]))
            for low_index, (low_market, low_quote) in enumerate(ladder):
                for high_market, high_quote in ladder[low_index + 1 :]:
                    low_yes_ask = low_quote["yes_ask"]
                    high_no_ask = 1.0 - high_quote["yes_bid"]
                    cost = low_yes_ask + high_no_ask + fee(low_yes_ask) + fee(high_no_ask)
                    stressed_cost = (
                        min(0.99, low_yes_ask + 0.01)
                        + min(0.99, high_no_ask + 0.01)
                        + fee(min(0.99, low_yes_ask + 0.01))
                        + fee(min(0.99, high_no_ask + 0.01))
                    )
                    if cost < 1.0:
                        arb_rows.append({
                            "series": series,
                            "event": event,
                            "identity": identity,
                            "occurrence_ts": occurrence_ts,
                            "scheduled_start_ts": start_of_game,
                            "offset_minutes": offset,
                            "low_ticker": low_market["ticker"],
                            "high_ticker": high_market["ticker"],
                            "low_strike": float(low_market["floor_strike"]),
                            "high_strike": float(high_market["floor_strike"]),
                            "cost_after_fees": cost,
                            "profit": 1.0 - cost,
                            "stressed_cost": stressed_cost,
                            "stressed_profit": 1.0 - stressed_cost,
                        })
    return {"calibration": calibration_rows, "arbitrage": arb_rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-per-family", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    jobs: list[tuple[str, str, list[dict[str, Any]]]] = []
    inventory: dict[str, Any] = {}
    for series in FAMILIES:
        markets = fetch_family(series)
        finalized = [
            market
            for market in markets
            if market.get("status") == "finalized"
            and market.get("result") in ("yes", "no")
            and market.get("occurrence_datetime")
        ]
        by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for market in finalized:
            by_event[market["event_ticker"]].append(market)
        selected_events = sorted(
            by_event,
            key=lambda event: parse_ts(by_event[event][0]["occurrence_datetime"]),
            reverse=True,
        )[: args.events_per_family]
        inventory[series] = {
            "market_rows": len(markets),
            "finalized_events_available": len(by_event),
            "events_selected": len(selected_events),
        }
        jobs.extend((series, event, by_event[event]) for event in selected_events)

    calibration: list[dict[str, Any]] = []
    arbitrage: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def run(job: tuple[str, str, list[dict[str, Any]]]) -> tuple[tuple[str, str], dict[str, Any] | None, str | None]:
        series, event, markets = job
        try:
            return (series, event), process_event(series, event, markets), None
        except Exception as exc:  # Keep a complete failure ledger instead of silently shrinking the sample.
            return (series, event), None, f"{type(exc).__name__}: {exc}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for (series, event), result, error in executor.map(run, jobs):
            if error:
                errors.append({"series": series, "event": event, "error": error})
                continue
            calibration.extend(result["calibration"])
            arbitrage.extend(result["arbitrage"])

    family_summaries: list[dict[str, Any]] = []
    for series in FAMILIES:
        family_rows = [row for row in calibration if row["series"] == series]
        for split, rows in chronological_splits(family_rows).items():
            family_summaries.append({
                "series": series,
                "split": split,
                "rows": len(rows),
                "events": len({row["event"] for row in rows}),
                "mean_net": sum(row["net"] for row in rows) / len(rows) if rows else None,
                "mean_net_stressed": sum(row["net_stressed"] for row in rows) / len(rows) if rows else None,
                "bootstrap": cluster_bootstrap(rows, value_key="net_stressed", cluster_key="event") if rows else {},
            })

    # Keep only the best guaranteed pair per event/offset; overlapping pairs are one opportunity.
    best_arbs: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in arbitrage:
        key = (row["series"], row["event"], row["offset_minutes"])
        if key not in best_arbs or row["profit"] > best_arbs[key]["profit"]:
            best_arbs[key] = row

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inventory": inventory,
        "jobs": len(jobs),
        "errors": errors,
        "calibration_rows": calibration,
        "calibration_bins": summarize_calibration(calibration),
        "family_summaries": family_summaries,
        "ordered_strike_arbitrage_all_pairs": arbitrage,
        "ordered_strike_arbitrage_best_per_event_offset": list(best_arbs.values()),
        "ordered_strike_arb_events": len({row["event"] for row in best_arbs.values()}),
        "ordered_strike_arb_stress_positive_events": len({row["event"] for row in best_arbs.values() if row["stressed_profit"] > 0}),
    }
    dump_json(RESULTS / "sports_study.json", output)
    print(f"Events requested: {len(jobs):,}; errors: {len(errors):,}")
    print(f"Independent calibration rows: {len(calibration):,}")
    print(f"Ordered-strike arb events: {output['ordered_strike_arb_events']:,}")
    print(f"Still positive after 1c/leg stress: {output['ordered_strike_arb_stress_positive_events']:,}")


if __name__ == "__main__":
    main()
