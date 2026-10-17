#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import io
import json
import time
import urllib.parse
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from common import DATA, RESULTS, as_float, cluster_bootstrap, dump_json, fee, get_json


@dataclass(frozen=True)
class City:
    name: str
    series: str
    asos_station: str
    standard_offset_hours: int


CITIES = (
    City("New York", "KXLOWTNYC", "NYC", -5),
    City("Philadelphia", "KXLOWTPHIL", "PHL", -5),
    City("Austin", "KXLOWTAUS", "AUS", -6),
    City("Denver", "KXLOWTDEN", "DEN", -7),
    City("Boston", "KXLOWTBOS", "BOS", -5),
    City("Miami", "KXLOWTMIA", "MIA", -5),
    City("Chicago", "KXLOWTCHI", "MDW", -6),
    City("Los Angeles", "KXLOWTLAX", "LAX", -8),
)


def parse_ts(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def load_markets(city: City) -> list[dict[str, Any]]:
    cache = DATA / f"low_markets_{city.series}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    markets: list[dict[str, Any]] = []
    cursor = ""
    while True:
        params: dict[str, Any] = {"series_ticker": city.series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = get_json("/markets", params)
        markets.extend(payload.get("markets") or [])
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break
    dump_json(cache, markets)
    return markets


def load_asos(city: City, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    cache = DATA / f"asos_{city.asos_station}_{start}_{end}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    params = {
        "station": city.asos_station,
        "data": ["tmpf", "dwpf", "skyc1"],
        "year1": start.year,
        "month1": start.month,
        "day1": start.day,
        "year2": end.year,
        "month2": end.month,
        "day2": end.day,
        "tz": "UTC",
        "format": "comma",
        "latlon": "no",
        "missing": "M",
        "trace": "T",
        "direct": "no",
        "report_type": [1, 2],
    }
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers={"User-Agent": "kalshi-three-lane-research/1.0"})
    text = ""
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode()
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 7:
                raise
            time.sleep(min(60.0, 2.0 * (2**attempt)))
    csv_text = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        temperature = as_float(row.get("tmpf"))
        if temperature is None:
            continue
        valid = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        local_standard = valid + dt.timedelta(hours=city.standard_offset_hours)
        rows.append({
            "timestamp": int(valid.timestamp()),
            "standard_date": local_standard.date().isoformat(),
            "standard_hour": local_standard.hour + local_standard.minute / 60.0,
            "temperature_f": temperature,
            "dewpoint_f": as_float(row.get("dwpf")),
            "sky": row.get("skyc1") or "",
        })
    dump_json(cache, rows)
    return rows


def fetch_event_candles(city: City, event: str, markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    cache = DATA / "low_event_candles_windowed" / city.series / f"{event}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    close_ts = min(parse_ts(market["close_time"]) for market in markets)
    start_ts = max(min(parse_ts(market["open_time"]) for market in markets), close_ts - 24 * 3600)
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cursor_start = start_ts
    while cursor_start < close_ts:
        payload = get_json(
            f"/series/{city.series}/events/{event}/candlesticks",
            {"start_ts": cursor_start, "end_ts": close_ts, "period_interval": 1},
        )
        for ticker, candles in zip(payload.get("market_tickers") or [], payload.get("market_candlesticks") or []):
            rows[ticker].extend(candle for candle in candles if int(candle["end_period_ts"]) >= cursor_start)
        adjusted_end = int(payload.get("adjusted_end_ts") or close_ts)
        if adjusted_end >= close_ts or adjusted_end <= cursor_start:
            break
        cursor_start = adjusted_end + 1
    rows = {ticker: sorted(candles, key=lambda candle: int(candle["end_period_ts"])) for ticker, candles in rows.items()}
    dump_json(cache, rows)
    return rows


def event_date(event: str) -> str:
    token = event.split("-")[1]
    parsed = dt.datetime.strptime(token, "%y%b%d")
    return parsed.date().isoformat()


def quote_after(candles: list[dict[str, Any]], target_ts: int, max_delay: int = 900) -> dict[str, float] | None:
    for candle in candles:
        timestamp = int(candle["end_period_ts"])
        if timestamp < target_ts + 60:
            continue
        if timestamp > target_ts + max_delay:
            return None
        bid = as_float((candle.get("yes_bid") or {}).get("close_dollars"))
        ask = as_float((candle.get("yes_ask") or {}).get("close_dollars"))
        if bid is None or ask is None or ask <= 0 or ask > 1 or bid < 0 or bid > ask:
            continue
        return {"timestamp": float(timestamp), "yes_bid": bid, "yes_ask": ask}
    return None


def side_quote(quote: dict[str, float], side: str) -> tuple[float, float]:
    if side == "yes":
        return quote["yes_bid"], quote["yes_ask"]
    return 1.0 - quote["yes_ask"], 1.0 - quote["yes_bid"]


def locked_side(market: dict[str, Any], observed_low: float, safety_buffer_f: int) -> str | None:
    strike_type = market.get("strike_type")
    floor = as_float(market.get("floor_strike"))
    cap = as_float(market.get("cap_strike"))
    if strike_type == "less" and cap is not None:
        # Rule is minimum < cap. Extra buffer protects against METAR/CLI rounding differences.
        if observed_low <= cap - 1.0 - safety_buffer_f:
            return "yes"
    elif strike_type == "greater" and floor is not None:
        # Rule is minimum > floor, so an observed print at/below floor guarantees NO.
        if observed_low <= floor - safety_buffer_f:
            return "no"
    elif strike_type == "between" and floor is not None:
        # Once the minimum is below the bucket, it can never climb back into it.
        if observed_low <= floor - 1.0 - safety_buffer_f:
            return "no"
    return None


def trade_pnl(result: str, side: str, ask: float, stress: float = 0.0) -> float:
    paid = min(1.0, ask + stress)
    return (1.0 if result == side else 0.0) - paid - fee(paid)


def first_lock(
    city: City,
    event: str,
    markets: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    candles_by_ticker: dict[str, list[dict[str, Any]]],
    safety_buffer_f: int,
) -> dict[str, Any] | None:
    low = float("inf")
    for observation in observations:
        low = min(low, float(observation["temperature_f"]))
        candidates: list[dict[str, Any]] = []
        for market in markets:
            if market.get("result") not in ("yes", "no"):
                continue
            side = locked_side(market, low, safety_buffer_f)
            if side is None:
                continue
            quote = quote_after(candles_by_ticker.get(market["ticker"], []), int(observation["timestamp"]))
            if quote is None:
                continue
            bid, ask = side_quote(quote, side)
            candidates.append({
                "city": city.name,
                "series": city.series,
                "event": event,
                "date": event_date(event),
                "ticker": market["ticker"],
                "side": side,
                "signal_ts": int(observation["timestamp"]),
                "entry_ts": int(quote["timestamp"]),
                "standard_hour": observation["standard_hour"],
                "observed_low_f": low,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "result": market["result"],
                "won": market["result"] == side,
                "net": trade_pnl(market["result"], side, ask),
                "net_stressed": trade_pnl(market["result"], side, ask, 0.01),
                "event_volume": sum(float(item.get("volume_fp") or 0) for item in markets),
                "safety_buffer_f": safety_buffer_f,
            })
        if candidates:
            # One city-day position: take the cheapest already-locked outcome, not every strike.
            return min(candidates, key=lambda row: (row["ask"], row["spread"], row["ticker"]))
    return None


def late_favorites(
    city: City,
    event: str,
    markets: list[dict[str, Any]],
    candles_by_ticker: dict[str, list[dict[str, Any]]],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    close_ts = min(parse_ts(market["close_time"]) for market in markets)
    output: list[dict[str, Any]] = []
    for hours in (6, 3, 1):
        target = close_ts - hours * 3600
        observed = [float(row["temperature_f"]) for row in observations if int(row["timestamp"]) <= target]
        observed_low = min(observed) if observed else None
        candidates: list[dict[str, Any]] = []
        for market in markets:
            if market.get("result") not in ("yes", "no"):
                continue
            quote = quote_after(candles_by_ticker.get(market["ticker"], []), target - 60)
            if quote is None:
                continue
            yes_mid = (quote["yes_bid"] + quote["yes_ask"]) / 2.0
            side = "yes" if yes_mid >= 0.5 else "no"
            bid, ask = side_quote(quote, side)
            candidates.append({
                "city": city.name,
                "series": city.series,
                "event": event,
                "date": event_date(event),
                "ticker": market["ticker"],
                "hours_before_close": hours,
                "side": side,
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "won": market["result"] == side,
                "observed_low_f": observed_low,
                "deterministic_lock_zero_buffer": observed_low is not None and locked_side(market, observed_low, 0) == side,
                "deterministic_lock_one_degree_buffer": observed_low is not None and locked_side(market, observed_low, 1) == side,
                "net": trade_pnl(market["result"], side, ask),
                "net_stressed": trade_pnl(market["result"], side, ask, 0.01),
            })
        if candidates:
            output.append(max(candidates, key=lambda row: ((row["bid"] + row["ask"]) / 2.0, -row["ask"])))
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-per-city", type=int, default=60)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    city_payloads: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[City, str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    for city in CITIES:
        markets = load_markets(city)
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
        events = sorted(by_event, key=event_date, reverse=True)[: args.events_per_city]
        if not events:
            city_payloads[city.series] = {"city": city.name, "events": 0}
            continue
        dates = [dt.date.fromisoformat(event_date(event)) for event in events]
        observations = load_asos(city, min(dates) - dt.timedelta(days=1), max(dates) + dt.timedelta(days=2))
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in observations:
            by_date[observation["standard_date"]].append(observation)
        city_payloads[city.series] = {
            "city": city.name,
            "events": len(events),
            "market_rows": len(finalized),
            "observation_rows": len(observations),
        }
        jobs.extend((city, event, by_event[event], by_date.get(event_date(event), [])) for event in events)

    locks_zero: list[dict[str, Any]] = []
    locks_buffered: list[dict[str, Any]] = []
    favorites: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def run(job: tuple[City, str, list[dict[str, Any]], list[dict[str, Any]]]) -> tuple[str, dict[str, Any] | None, str | None]:
        city, event, markets, observations = job
        try:
            candles = fetch_event_candles(city, event, markets)
            return event, {
                "zero": first_lock(city, event, markets, observations, candles, 0),
                "buffered": first_lock(city, event, markets, observations, candles, 1),
                "favorites": late_favorites(city, event, markets, candles, observations),
            }, None
        except Exception as exc:
            return event, None, f"{type(exc).__name__}: {exc}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for event, result, error in executor.map(run, jobs):
            if error:
                errors.append({"event": event, "error": error})
                continue
            if result["zero"]:
                locks_zero.append(result["zero"])
            if result["buffered"]:
                locks_buffered.append(result["buffered"])
            favorites.extend(result["favorites"])

    summaries: list[dict[str, Any]] = []
    for label, rows in (("zero_buffer_lock", locks_zero), ("one_degree_buffer_lock", locks_buffered)):
        for series in [city.series for city in CITIES]:
            subset = [row for row in rows if row["series"] == series]
            if not subset:
                continue
            summaries.append({
                "strategy": label,
                "series": series,
                "trades": len(subset),
                "wins": sum(row["won"] for row in subset),
                "mean_ask": sum(row["ask"] for row in subset) / len(subset),
                "mean_spread": sum(row["spread"] for row in subset) / len(subset),
                "mean_net": sum(row["net"] for row in subset) / len(subset),
                "mean_net_stressed": sum(row["net_stressed"] for row in subset) / len(subset),
                "bootstrap": cluster_bootstrap(subset, value_key="net_stressed", cluster_key="date"),
            })
    for hours in (6, 3, 1):
        subset = [row for row in favorites if row["hours_before_close"] == hours]
        summaries.append({
            "strategy": "market_favorite",
            "hours_before_close": hours,
            "trades": len(subset),
            "wins": sum(row["won"] for row in subset),
            "mean_ask": sum(row["ask"] for row in subset) / len(subset) if subset else None,
            "mean_net": sum(row["net"] for row in subset) / len(subset) if subset else None,
            "mean_net_stressed": sum(row["net_stressed"] for row in subset) / len(subset) if subset else None,
            "bootstrap": cluster_bootstrap(subset, value_key="net_stressed", cluster_key="date") if subset else {},
        })

    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "city_inventory": city_payloads,
        "jobs": len(jobs),
        "errors": errors,
        "zero_buffer_locks": locks_zero,
        "one_degree_buffer_locks": locks_buffered,
        "late_favorites": favorites,
        "summaries": summaries,
    }
    dump_json(RESULTS / "daily_low_study.json", output)
    print(f"City-days requested: {len(jobs):,}; errors: {len(errors):,}")
    print(f"Zero-buffer deterministic entries: {len(locks_zero):,}")
    print(f"One-degree-buffer entries: {len(locks_buffered):,}")
    print(f"Buffered lock mismatches: {sum(not row['won'] for row in locks_buffered):,}")


if __name__ == "__main__":
    main()
