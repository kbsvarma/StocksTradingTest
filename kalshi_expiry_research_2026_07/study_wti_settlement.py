#!/usr/bin/env python3
"""Empirical CL-futures settlement model against WTI Kalshi ladders."""

from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from research_common import fee, load_jsonl, side_quote, summarize

NEW_YORK = ZoneInfo("America/New_York")
REMAINING_MINUTES = (10, 5, 3, 2)


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def load_cl(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=["close", "instrument_id"]).sort_index()
    index = frame.index
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame.index = index.tz_convert(NEW_YORK)
    return frame


def settlement_features(frame: pd.DataFrame) -> list[dict]:
    output = []
    for local_date, day_frame in frame.groupby(frame.index.date):
        if local_date.weekday() >= 5:
            continue
        final_rows = day_frame[day_frame.index.time == time(14, 29)]
        if final_rows.empty:
            continue
        final_close = float(final_rows.iloc[-1]["close"])
        final_instrument = int(final_rows.iloc[-1]["instrument_id"])
        for remaining in REMAINING_MINUTES:
            signal_rows = day_frame[
                day_frame.index.time == time(14, 29 - remaining)
            ]
            if signal_rows.empty:
                continue
            signal_timestamp = signal_rows.index[-1]
            if int(signal_rows.iloc[-1]["instrument_id"]) != final_instrument:
                continue
            current = float(signal_rows.iloc[-1]["close"])
            recent = day_frame.loc[
                (day_frame.index <= signal_timestamp)
                & (day_frame.index >= signal_timestamp - pd.Timedelta(minutes=60)),
                "close",
            ].astype(float)
            differences = recent.diff().dropna()
            if len(differences) < 30:
                continue
            sigma = float(differences.std(ddof=1))
            if sigma <= 0:
                continue
            scale = sigma * math.sqrt(remaining)
            output.append(
                {
                    "date": local_date.isoformat(),
                    "remaining": remaining,
                    "current": current,
                    "final": final_close,
                    "sigma": sigma,
                    "standardized_residual": (final_close - current) / scale,
                }
            )
    return output


def empirical_probability_above(
    sorted_residuals: list[float],
    current: float,
    strike: float,
    sigma: float,
    remaining: int,
) -> tuple[float, float]:
    threshold = (strike - current) / (sigma * math.sqrt(remaining))
    index = bisect.bisect_right(sorted_residuals, threshold)
    count = len(sorted_residuals)
    probability = (count - index + 1) / (count + 2)
    standard_error = math.sqrt(probability * (1.0 - probability) / (count + 2))
    conservative = max(0.0, probability - 1.645 * standard_error - 0.02)
    return probability, conservative


def candle_map(path: Path) -> dict[str, dict[int, dict]]:
    return {
        record["ticker"]: {
            int(candle["t"]): candle for candle in record.get("candles", [])
        }
        for record in load_jsonl(path)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cl-data",
        type=Path,
        default=Path("../spx_spread_bot/backtests/futures_intraday/CL_1m.parquet"),
    )
    parser.add_argument(
        "--kalshi-data",
        type=Path,
        default=Path("data/wti_overlap"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--minimum-edge-cents", type=float, default=2.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    features = settlement_features(load_cl(args.cl_data))
    calibration: dict[int, list[float]] = {}
    for remaining in REMAINING_MINUTES:
        calibration[remaining] = sorted(
            feature["standardized_residual"]
            for feature in features
            if feature["remaining"] == remaining
            and int(feature["date"][:4]) < 2026
        )
    overlap_features = {
        (feature["date"], feature["remaining"]): feature
        for feature in features
        if int(feature["date"][:4]) == 2026
    }
    markets = load_jsonl(args.kalshi_data / "markets_KXWTI.jsonl")
    candles = candle_map(args.kalshi_data / "candles_KXWTI_1m.jsonl")
    by_event: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        if market.get("event_ticker"):
            by_event[market["event_ticker"]].append(market)

    trades = []
    event_audits = []
    market_agreements = []
    for event, event_markets in sorted(by_event.items()):
        close_ts = max(timestamp(market["close_time"]) for market in event_markets)
        local_date = datetime.fromtimestamp(close_ts, tz=timezone.utc).astimezone(
            NEW_YORK
        ).date().isoformat()
        final_feature = overlap_features.get((local_date, REMAINING_MINUTES[-1]))
        if final_feature:
            for market in event_markets:
                market_agreements.append(
                    (final_feature["final"] > float(market["floor_strike"]))
                    == (market["result"] == "yes")
                )

        best_audit = None
        for remaining in REMAINING_MINUTES:
            feature = overlap_features.get((local_date, remaining))
            residuals = calibration[remaining]
            if not feature or len(residuals) < 1000:
                continue
            candidates = []
            signal_ts = close_ts - remaining * 60
            for market in event_markets:
                if market.get("result") not in ("yes", "no"):
                    continue
                p_yes, p_yes_floor = empirical_probability_above(
                    residuals,
                    feature["current"],
                    float(market["floor_strike"]),
                    feature["sigma"],
                    remaining,
                )
                side = "yes" if p_yes >= 0.5 else "no"
                probability = p_yes if side == "yes" else 1.0 - p_yes
                probability_floor = (
                    p_yes_floor
                    if side == "yes"
                    else max(0.0, probability - 0.02)
                )
                signal_candle = candles.get(market["ticker"], {}).get(signal_ts)
                next_candle = candles.get(market["ticker"], {}).get(signal_ts + 60)
                signal_quote = (
                    side_quote(signal_candle, side, phase="close")
                    if signal_candle
                    else None
                )
                next_quote = (
                    side_quote(next_candle, side, phase="open")
                    if next_candle
                    else None
                )
                if signal_quote is None or next_quote is None:
                    continue
                entry = max(signal_quote[1], next_quote[1])
                spread = signal_quote[1] - signal_quote[0]
                break_even = entry + fee(entry)
                candidates.append(
                    {
                        "event_ticker": event,
                        "ticker": market["ticker"],
                        "date": local_date,
                        "remaining": remaining,
                        "side": side,
                        "current_cl": feature["current"],
                        "strike": market["floor_strike"],
                        "sigma": feature["sigma"],
                        "model_probability": probability,
                        "model_probability_floor": probability_floor,
                        "entry": entry,
                        "spread": spread,
                        "break_even": break_even,
                        "edge_floor": probability_floor - break_even,
                        "calibration_n": len(residuals),
                    }
                )
            if not candidates:
                continue
            best = max(candidates, key=lambda item: item["edge_floor"])
            if best_audit is None or best["edge_floor"] > best_audit["edge_floor"]:
                best_audit = best
            if (
                best["edge_floor"] < args.minimum_edge_cents / 100.0
                or best["spread"] > 0.03
                or best["entry"] > 0.99
            ):
                continue
            market = next(
                item for item in event_markets if item["ticker"] == best["ticker"]
            )
            won = market["result"] == best["side"]
            trades.append(
                {
                    **best,
                    "strategy": "wti_external_settlement_lock",
                    "ts": signal_ts + 60,
                    "cluster": local_date,
                    "win": won,
                    "pnl": (
                        (1.0 - best["entry"] if won else -best["entry"])
                        - fee(best["entry"])
                    ),
                }
            )
            break
        if best_audit:
            event_audits.append(best_audit)

    result = {
        "historical_feature_count": len(features),
        "calibration_counts": {
            str(remaining): len(values)
            for remaining, values in calibration.items()
        },
        "overlap_events": len(by_event),
        "market_settlement_proxy_agreement": (
            sum(market_agreements) / len(market_agreements)
            if market_agreements
            else 0.0
        ),
        "best_event_edge_floors_cents": [
            100.0 * audit["edge_floor"] for audit in event_audits
        ],
        "trade_metrics": summarize(trades),
        "trades": trades,
    }
    output = args.output_dir / "wti_external_settlement_lock.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
