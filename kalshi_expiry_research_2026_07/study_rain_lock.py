#!/usr/bin/env python3
"""Deterministic NYC rain lock after a positive Central Park ASOS report."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from research_common import fee, load_jsonl, side_quote, summarize

NEW_YORK = ZoneInfo("America/New_York")


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def first_positive_reports(path: Path) -> dict[str, datetime]:
    first: dict[str, datetime] = {}
    with path.open() as handle:
        for row in csv.DictReader(handle):
            try:
                precipitation = float(row["p01i"])
                valid_utc = datetime.strptime(
                    row["valid"],
                    "%Y-%m-%d %H:%M",
                ).replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if precipitation <= 0:
                continue
            local_date = valid_utc.astimezone(NEW_YORK).date().isoformat()
            first[local_date] = min(first.get(local_date, valid_utc), valid_utc)
    return first


def candle_map(path: Path) -> dict[str, dict[int, dict]]:
    return {
        record["ticker"]: {
            int(candle["t"]): candle for candle in record.get("candles", [])
        }
        for record in load_jsonl(path)
    }


def evaluate_delay(
    markets: list[dict],
    candles: dict[str, dict[int, dict]],
    reports: dict[str, datetime],
    publication_delay_minutes: int,
) -> dict:
    trades = []
    positive_market_days = 0
    source_agreements = 0
    for market in markets:
        if market.get("result") not in ("yes", "no"):
            continue
        close_ts = timestamp(market["close_time"])
        local_date = datetime.fromtimestamp(close_ts, tz=timezone.utc).astimezone(
            NEW_YORK
        ).date().isoformat()
        report_time = reports.get(local_date)
        if report_time is None:
            continue
        positive_market_days += 1
        source_agreements += market["result"] == "yes"
        actionable = int(
            (report_time + timedelta(minutes=publication_delay_minutes)).timestamp()
        )
        market_candles = candles.get(market["ticker"], {})
        signal_candidates = sorted(
            candle_ts
            for candle_ts in market_candles
            if actionable <= candle_ts <= min(close_ts - 60, actionable + 15 * 60)
        )
        if not signal_candidates:
            continue
        signal_ts = signal_candidates[0]
        signal_quote = side_quote(market_candles[signal_ts], "yes", phase="close")
        next_candle = market_candles.get(signal_ts + 60)
        next_quote = (
            side_quote(next_candle, "yes", phase="open") if next_candle else None
        )
        if signal_quote is None or next_quote is None:
            continue
        entry = max(signal_quote[1], next_quote[1])
        spread = signal_quote[1] - signal_quote[0]
        if entry > 0.99 or spread > 0.10:
            continue
        won = market["result"] == "yes"
        pnl = (1.0 - entry if won else -entry) - fee(entry)
        stress_entry = min(0.99, entry + 0.01)
        stress_pnl = (
            (1.0 - stress_entry if won else -stress_entry)
            - fee(stress_entry)
        )
        trades.append(
            {
                "strategy": "nyc_rain_observation_lock",
                "event_ticker": market.get("event_ticker") or market["ticker"],
                "ticker": market["ticker"],
                "date": local_date,
                "report_time_utc": report_time.isoformat(),
                "publication_delay_minutes": publication_delay_minutes,
                "ts": signal_ts + 60,
                "cluster": local_date,
                "side": "yes",
                "entry": entry,
                "spread": spread,
                "win": won,
                "pnl": pnl,
                "stress_entry_plus_1c": stress_entry,
                "stress_pnl_plus_1c": stress_pnl,
            }
        )
    metrics = summarize(trades)
    return {
        "publication_delay_minutes": publication_delay_minutes,
        "positive_source_days": positive_market_days,
        "source_settlement_agreement_rate": (
            source_agreements / positive_market_days
            if positive_market_days
            else 0.0
        ),
        "trades": len(trades),
        "metrics": metrics,
        "plus_1c_total_pnl_per_contract": sum(
            trade["stress_pnl_plus_1c"] for trade in trades
        ),
        "records": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--precip-csv",
        type=Path,
        default=Path("data/nyc_asos_precip.csv"),
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    markets = load_jsonl(args.data_dir / "markets_KXRAINNYC.jsonl")
    candles = candle_map(args.data_dir / "candles_KXRAINNYC_1m.jsonl")
    reports = first_positive_reports(args.precip_csv)
    results = [
        evaluate_delay(markets, candles, reports, delay)
        for delay in (5, 10, 20, 30)
    ]
    output = args.output_dir / "rain_observation_lock.json"
    output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
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
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
