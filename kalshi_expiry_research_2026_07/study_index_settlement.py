#!/usr/bin/env python3
"""Externally calibrated final-minute settlement lock for daily index markets."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from scipy import stats

from research_common import fee, favorite_quote, load_jsonl, side_quote, summarize

NEW_YORK = ZoneInfo("America/New_York")
REMAINING_MINUTES = (10, 5, 3, 2)
Z_BUCKETS = (0.0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, float("inf"))


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def wilson_lower(wins: int, total: int, z: float = 1.645) -> float:
    if total == 0:
        return 0.0
    probability = wins / total
    denominator = 1.0 + z * z / total
    center = (probability + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            probability * (1.0 - probability) / total
            + z * z / (4.0 * total * total)
        )
        / denominator
    )
    return center - margin


def minute_features(path: Path) -> list[dict]:
    frame = pd.read_parquet(path, columns=["close"])
    index = frame.index
    if index.tz is None:
        index = index.tz_localize("UTC")
    frame.index = index.tz_convert(NEW_YORK)
    frame = frame.sort_index()
    features = []
    previous_close: float | None = None
    for local_date, day_frame in frame.groupby(frame.index.date):
        if local_date.weekday() >= 5:
            continue
        close_rows = day_frame[day_frame.index.time == time(15, 59)]
        if close_rows.empty:
            continue
        final_close = float(close_rows.iloc[-1]["close"])
        if previous_close is not None and previous_close > 0:
            for remaining in REMAINING_MINUTES:
                signal_time = time(15, 59 - remaining)
                signal_rows = day_frame[day_frame.index.time == signal_time]
                if signal_rows.empty:
                    continue
                signal_timestamp = signal_rows.index[-1]
                current_close = float(signal_rows.iloc[-1]["close"])
                recent = day_frame.loc[
                    (day_frame.index <= signal_timestamp)
                    & (day_frame.index >= signal_timestamp - pd.Timedelta(minutes=60)),
                    "close",
                ]
                returns = recent.astype(float).apply(math.log).diff().dropna()
                if len(returns) < 30:
                    continue
                sigma = float(returns.std(ddof=1))
                if sigma <= 0:
                    continue
                delta = math.log(current_close / previous_close)
                z_score = abs(delta) / (sigma * math.sqrt(remaining))
                predicted_up = delta > 0
                final_up = final_close > previous_close
                features.append(
                    {
                        "date": local_date.isoformat(),
                        "remaining": remaining,
                        "z": z_score,
                        "predicted_side": "yes" if predicted_up else "no",
                        "correct": predicted_up == final_up,
                        "current_close": current_close,
                        "previous_close": previous_close,
                        "final_close": final_close,
                    }
                )
        previous_close = final_close
    return features


def load_feature_history(futures_dir: Path, root: str) -> list[dict]:
    features = []
    for path in sorted(futures_dir.glob(f"{root}_ohlcv-1m_*.parquet")):
        features.extend(minute_features(path))
    return features


def bucket_label(z_score: float) -> str:
    for low, high in zip(Z_BUCKETS, Z_BUCKETS[1:]):
        if low <= z_score < high:
            return f"{low:g}-{high:g}"
    raise RuntimeError("unreachable")


def calibration_table(features: list[dict]) -> dict[tuple[int, str], dict]:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for feature in features:
        year = int(feature["date"][:4])
        if year >= 2026:
            continue
        grouped[
            (feature["remaining"], bucket_label(feature["z"]))
        ].append(feature)
    output = {}
    for key, rows in grouped.items():
        wins = sum(row["correct"] for row in rows)
        output[key] = {
            "n": len(rows),
            "wins": wins,
            "probability": wins / len(rows),
            "wilson_lower_90": wilson_lower(wins, len(rows)),
        }
    return output


def kalshi_candles(path: Path) -> dict[str, dict[int, dict]]:
    return {
        record["ticker"]: {
            int(candle["t"]): candle for candle in record.get("candles", [])
        }
        for record in load_jsonl(path)
    }


def evaluate_series(
    series: str,
    root: str,
    data_dir: Path,
    feature_history: list[dict],
    calibration: dict[tuple[int, str], dict],
    minimum_z: float,
    minimum_edge: float,
) -> tuple[list[dict], list[dict]]:
    features = {
        (feature["date"], feature["remaining"]): feature
        for feature in feature_history
        if int(feature["date"][:4]) == 2026
    }
    markets = load_jsonl(data_dir / f"markets_{series}.jsonl")
    candles = kalshi_candles(data_dir / f"candles_{series}_1m.jsonl")
    trades = []
    audits = []
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        close_ts = timestamp(market["close_time"])
        local_date = datetime.fromtimestamp(close_ts, tz=timezone.utc).astimezone(
            NEW_YORK
        ).date().isoformat()
        for remaining in REMAINING_MINUTES:
            feature = features.get((local_date, remaining))
            if not feature or feature["z"] < minimum_z:
                continue
            bucket = bucket_label(feature["z"])
            calibration_row = calibration.get((remaining, bucket))
            if not calibration_row or calibration_row["n"] < 200:
                continue
            side = feature["predicted_side"]
            signal_ts = close_ts - remaining * 60
            signal_candle = candles.get(market["ticker"], {}).get(signal_ts)
            signal_quote = (
                side_quote(signal_candle, side, phase="close")
                if signal_candle
                else None
            )
            next_candle = candles.get(market["ticker"], {}).get(signal_ts + 60)
            next_quote = (
                side_quote(next_candle, side, phase="open")
                if next_candle
                else None
            )
            if signal_quote is None or next_quote is None:
                continue
            spread = signal_quote[1] - signal_quote[0]
            entry = max(signal_quote[1], next_quote[1])
            model_floor = calibration_row["wilson_lower_90"]
            break_even = entry + fee(entry)
            edge_floor = model_floor - break_even
            audit = {
                "series": series,
                "root": root,
                "ticker": market["ticker"],
                "date": local_date,
                "remaining": remaining,
                "z": feature["z"],
                "side": side,
                "entry": entry,
                "spread": spread,
                "calibration_n": calibration_row["n"],
                "calibration_probability": calibration_row["probability"],
                "model_floor": model_floor,
                "break_even": break_even,
                "edge_floor": edge_floor,
                "settlement_agreement": (
                    (market["result"] == "yes")
                    == (feature["final_close"] > feature["previous_close"])
                ),
            }
            audits.append(audit)
            if spread > 0.03 or edge_floor < minimum_edge or entry > 0.99:
                continue
            won = market["result"] == side
            trade = {
                **audit,
                "strategy": "index_external_settlement_lock",
                "event_ticker": market.get("event_ticker") or market["ticker"],
                "ts": signal_ts + 60,
                "cluster": local_date,
                "win": won,
                "pnl": (1.0 - entry if won else -entry) - fee(entry),
            }
            trades.append(trade)
            break
    return trades, audits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--futures-dir",
        type=Path,
        default=Path("../event_edge_research/data"),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--minimum-z", type=float, default=2.0)
    parser.add_argument("--minimum-edge-cents", type=float, default=1.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    all_trades = []
    all_audits = []
    for series, root in (("KXINXDUD", "ES"), ("KXNASDAQDUD", "NQ")):
        features = load_feature_history(args.futures_dir, root)
        calibration = calibration_table(features)
        trades, audits = evaluate_series(
            series,
            root,
            args.data_dir,
            features,
            calibration,
            args.minimum_z,
            args.minimum_edge_cents / 100.0,
        )
        all_trades.extend(trades)
        all_audits.extend(audits)
        results.append(
            {
                "series": series,
                "historical_features": len(features),
                "calibration": {
                    f"{remaining}:{bucket}": row
                    for (remaining, bucket), row in calibration.items()
                },
                "audited_overlap_signals": len(audits),
                "settlement_agreement_rate": (
                    sum(audit["settlement_agreement"] for audit in audits) / len(audits)
                    if audits
                    else 0.0
                ),
                "trades": len(trades),
                "maximum_edge_floor_cents": (
                    100.0 * max(audit["edge_floor"] for audit in audits)
                    if audits
                    else 0.0
                ),
                "metrics": summarize(trades),
            }
        )

    trade_path = args.output_dir / "trades_index_external_lock.jsonl"
    with trade_path.open("w") as handle:
        for trade in sorted(all_trades, key=lambda item: item["ts"]):
            handle.write(json.dumps(trade, sort_keys=True) + "\n")
    audit_path = args.output_dir / "audits_index_external_lock.jsonl"
    with audit_path.open("w") as handle:
        for audit in sorted(all_audits, key=lambda item: (item["date"], item["series"])):
            handle.write(json.dumps(audit, sort_keys=True) + "\n")
    result_path = args.output_dir / "index_external_lock.json"
    result_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            [
                {
                    "series": result["series"],
                    "historical_features": result["historical_features"],
                    "audited_overlap_signals": result["audited_overlap_signals"],
                    "settlement_agreement_rate": result["settlement_agreement_rate"],
                    "trades": result["trades"],
                    "maximum_edge_floor_cents": result["maximum_edge_floor_cents"],
                    "metrics": result["metrics"],
                }
                for result in results
            ],
            indent=2,
            sort_keys=True,
        )
    )
    print(f"wrote {result_path}")


if __name__ == "__main__":
    main()
