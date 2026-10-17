#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import math
from collections import defaultdict
from typing import Any

import numpy as np

from common import RESULTS, as_float, cluster_bootstrap, dump_json, fee
from daily_low_study import CITIES, event_date, fetch_event_candles, load_asos, load_markets, parse_ts, quote_after, side_quote


DECISION_HOURS = (18, 21, 23)


def utc_target(date: dt.date, standard_offset_hours: int, hour: int) -> int:
    local_standard = dt.datetime.combine(date, dt.time(hour=hour), tzinfo=dt.timezone(dt.timedelta(hours=standard_offset_hours)))
    return int(local_standard.timestamp())


def state_at(observations: list[dict[str, Any]], target_ts: int) -> dict[str, Any] | None:
    available = [row for row in observations if int(row["timestamp"]) <= target_ts]
    if not available or target_ts - int(available[-1]["timestamp"]) > 90 * 60:
        return None
    latest = available[-1]
    three_hour_target = target_ts - 3 * 3600
    earlier = min(available, key=lambda row: abs(int(row["timestamp"]) - three_hour_target))
    if abs(int(earlier["timestamp"]) - three_hour_target) > 90 * 60:
        return None
    temperatures = [float(row["temperature_f"]) for row in available]
    current = float(latest["temperature_f"])
    observed_low = min(temperatures)
    dewpoint = as_float(latest.get("dewpoint_f"))
    if dewpoint is None:
        dewpoint = current
    return {
        "target_ts": target_ts,
        "current_f": current,
        "observed_low_f": observed_low,
        "dewpoint_f": dewpoint,
        "trend_3h_f": current - float(earlier["temperature_f"]),
        "clear": 1.0 if str(latest.get("sky") or "") in ("CLR", "SKC") else 0.0,
        "final_low_f": min(float(row["temperature_f"]) for row in observations),
    }


def features(state: dict[str, Any]) -> list[float]:
    return [
        1.0,
        state["current_f"] - state["observed_low_f"],
        state["current_f"] - state["dewpoint_f"],
        state["trend_3h_f"],
        state["clear"],
    ]


def fit_model(rows: list[dict[str, Any]]) -> tuple[np.ndarray, float]:
    matrix = np.asarray([features(row) for row in rows], dtype=float)
    target = np.asarray([row["observed_low_f"] - row["final_low_f"] for row in rows], dtype=float)
    coefficients, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    residuals = target - matrix @ coefficients
    sigma = max(1.0, float(np.std(residuals, ddof=min(len(coefficients), max(1, len(residuals) - 1)))))
    return coefficients, sigma


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def interval_probability(lower: float, upper: float, mean: float, sigma: float, cap: float) -> float:
    denominator = normal_cdf((cap - mean) / sigma)
    if denominator <= 1e-9:
        return 0.0
    numerator = normal_cdf((min(upper, cap) - mean) / sigma) - normal_cdf((lower - mean) / sigma)
    return max(0.0, min(1.0, numerator / denominator))


def market_probability(market: dict[str, Any], mean: float, sigma: float, observed_low: float) -> float:
    floor = as_float(market.get("floor_strike"))
    cap = as_float(market.get("cap_strike"))
    support_cap = observed_low + 0.5
    if market.get("strike_type") == "less" and cap is not None:
        return interval_probability(-1000.0, cap - 0.5, mean, sigma, support_cap)
    if market.get("strike_type") == "greater" and floor is not None:
        return interval_probability(floor + 0.5, 1000.0, mean, sigma, support_cap)
    if market.get("strike_type") == "between" and floor is not None and cap is not None:
        return interval_probability(floor - 0.5, cap + 0.5, mean, sigma, support_cap)
    return 0.0


def trade_pnl(result: str, side: str, ask: float) -> float:
    return (1.0 if result == side else 0.0) - ask - fee(ask)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-per-city", type=int, default=60)
    args = parser.parse_args()

    model_reports: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}

    for city in CITIES:
        markets = load_markets(city)
        finalized = [
            market
            for market in markets
            if market.get("status") == "finalized" and market.get("result") in ("yes", "no")
        ]
        by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for market in finalized:
            by_event[market["event_ticker"]].append(market)
        events = sorted(by_event, key=event_date) [-args.events_per_city :]
        if len(events) < 20:
            coverage[city.series] = {"events": len(events), "reason": "insufficient_events"}
            continue
        dates = [dt.date.fromisoformat(event_date(event)) for event in events]
        observations = load_asos(city, min(dates) - dt.timedelta(days=1), max(dates) + dt.timedelta(days=2))
        observations_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in observations:
            observations_by_date[observation["standard_date"]].append(observation)
        states: dict[tuple[str, int], dict[str, Any]] = {}
        for event in events:
            date = dt.date.fromisoformat(event_date(event))
            day_rows = observations_by_date.get(date.isoformat(), [])
            for hour in DECISION_HOURS:
                state = state_at(day_rows, utc_target(date, city.standard_offset_hours, hour))
                if state:
                    states[(event, hour)] = state
        split_a = int(len(events) * 0.50)
        split_b = int(len(events) * 0.75)
        split_label = {
            event: "train" if index < split_a else "validation" if index < split_b else "test"
            for index, event in enumerate(events)
        }
        coverage[city.series] = {"events": len(events), "states": len(states)}

        for hour in DECISION_HOURS:
            train_states = [states[(event, hour)] for event in events[:split_a] if (event, hour) in states]
            if len(train_states) < 15:
                model_reports.append({"series": city.series, "hour": hour, "reason": "insufficient_train_states"})
                continue
            coefficients, sigma = fit_model(train_states)
            model_reports.append({
                "series": city.series,
                "hour": hour,
                "train_rows": len(train_states),
                "coefficients": coefficients.tolist(),
                "residual_sigma_f": sigma,
            })
            for event in events[split_a:]:
                state = states.get((event, hour))
                if not state:
                    continue
                predicted_drop = max(0.0, min(15.0, float(np.dot(np.asarray(features(state)), coefficients))))
                mean = state["observed_low_f"] - predicted_drop
                candles = fetch_event_candles(city, event, by_event[event])
                candidates: list[dict[str, Any]] = []
                for market in by_event[event]:
                    quote = quote_after(candles.get(market["ticker"], []), int(state["target_ts"]) - 60)
                    if quote is None:
                        continue
                    probability_yes = market_probability(market, mean, sigma, state["observed_low_f"])
                    for side, probability in (("yes", probability_yes), ("no", 1.0 - probability_yes)):
                        bid, ask = side_quote(quote, side)
                        paid_stressed = min(1.0, ask + 0.01)
                        stressed_edge = probability - paid_stressed - fee(paid_stressed)
                        if ask > 0.95 or ask - bid > 0.05 or stressed_edge < 0.03:
                            continue
                        candidates.append({
                            "city": city.name,
                            "series": city.series,
                            "event": event,
                            "date": event_date(event),
                            "split": split_label[event],
                            "hour": hour,
                            "ticker": market["ticker"],
                            "side": side,
                            "ask": ask,
                            "spread": ask - bid,
                            "p_model": probability,
                            "stressed_model_edge": stressed_edge,
                            "predicted_final_low_f": mean,
                            "observed_low_f": state["observed_low_f"],
                            "actual_final_low_f": state["final_low_f"],
                            "won": market["result"] == side,
                            "net": trade_pnl(market["result"], side, ask),
                            "net_stressed": trade_pnl(market["result"], side, paid_stressed),
                        })
                if candidates:
                    trades.append(max(candidates, key=lambda row: (row["stressed_model_edge"], -row["ask"])))

    summaries: list[dict[str, Any]] = []
    for split in ("validation", "test"):
        for hour in DECISION_HOURS:
            subset = [row for row in trades if row["split"] == split and row["hour"] == hour]
            summaries.append({
                "split": split,
                "hour": hour,
                "trades": len(subset),
                "wins": sum(row["won"] for row in subset),
                "mean_net": sum(row["net"] for row in subset) / len(subset) if subset else None,
                "mean_net_stressed": sum(row["net_stressed"] for row in subset) / len(subset) if subset else None,
                "bootstrap": cluster_bootstrap(subset, value_key="net_stressed", cluster_key="date") if subset else {},
            })
    output = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": coverage,
        "models": model_reports,
        "trades": trades,
        "summaries": summaries,
    }
    dump_json(RESULTS / "daily_low_continuation.json", output)
    print(f"Continuation trades: {len(trades):,}")
    for summary in summaries:
        print(summary["split"], summary["hour"], summary["trades"], summary["mean_net_stressed"])


if __name__ == "__main__":
    main()
