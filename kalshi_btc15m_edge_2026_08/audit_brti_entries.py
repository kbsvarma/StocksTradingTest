#!/usr/bin/env python3
"""Audit the exploratory p>=80%, edge>=1c BRTI lane against public prints."""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
import urllib.parse

import execution_audit
import full_brti_minute_replay as replay
import settlement_average_study as settlement
import study


CACHE = os.path.join(study.ROOT, "data", "brti_entry_prints.jsonl")


def candidate_rows() -> list[replay.Row]:
    _, _, _, rows = replay.build_rows(
        study.load_markets(), settlement.BrtiTape.load(replay.FULL_TAPE)
    )
    return [row for row in rows if row.p_win >= 0.80 and row.expected_ev >= 0.01]


def pull(rows: list[replay.Row]) -> list[dict]:
    cached = {
        row["ticker"]: row for row in study.read_jsonl(CACHE)
    } if os.path.exists(CACHE) else {}
    for index, signal in enumerate(rows, start=1):
        if signal.ticker in cached:
            continue
        decision_ts = signal.close_ts - 60
        query = urllib.parse.urlencode(
            {
                "ticker": signal.ticker,
                "min_ts": decision_ts,
                "max_ts": decision_ts + 2,
                "limit": 1000,
                "is_block_trade": "false",
            }
        )
        response = execution_audit.get_json(f"{execution_audit.BASE}?{query}")
        prints = []
        for row in response.get("trades") or []:
            timestamp = execution_audit.trade_timestamp(row["created_time"])
            if decision_ts <= timestamp <= decision_ts + 2:
                prints.append(
                    {
                        "ts": timestamp,
                        "side": row.get("taker_side"),
                        "book_side": row.get("taker_book_side"),
                        "yes": float(row["yes_price_dollars"]),
                        "no": float(row["no_price_dollars"]),
                        "count": float(row["count_fp"]),
                    }
                )
        cached[signal.ticker] = {
            "ticker": signal.ticker,
            "decision_ts": decision_ts,
            "side": signal.side,
            "modeled_entry": signal.entry,
            "prints": sorted(prints, key=lambda row: row["ts"]),
            "truncated": bool(response.get("cursor")),
        }
        if index == 1 or index % 25 == 0 or index == len(rows):
            print(f"pulled {index}/{len(rows)}", flush=True)
        time.sleep(0.06)

    descriptor, temporary = tempfile.mkstemp(prefix="brti_entry_prints", dir=os.path.dirname(CACHE))
    try:
        with os.fdopen(descriptor, "w") as handle:
            for signal in rows:
                handle.write(json.dumps(cached[signal.ticker], separators=(",", ":")) + "\n")
        os.replace(temporary, CACHE)
    except BaseException:
        os.unlink(temporary)
        raise
    return [cached[row.ticker] for row in rows]


def main() -> None:
    rows = candidate_rows()
    records = pull(rows)
    by_ticker = {row.ticker: row for row in rows}
    actual = []
    slippage = []
    delays = []
    misses = truncated = 0
    for record in records:
        signal = by_ticker[record["ticker"]]
        truncated += int(record["truncated"])
        matching = [
            row
            for row in record["prints"]
            if row["side"] == signal.side and row["book_side"] == "ask"
        ]
        if not matching:
            misses += 1
            continue
        first = matching[0]
        entry = first[signal.side]
        slippage.append(entry - signal.entry)
        delays.append(first["ts"] - record["decision_ts"])
        actual.append(
            study.Trade(
                "S2_actual_print",
                signal.ticker,
                signal.close_ts,
                signal.side,
                entry,
                study.settlement_pnl(entry, signal.result == signal.side),
                1,
            )
        )

    modeled = {
        row.ticker: study.Trade(
            "S2_candle", row.ticker, row.close_ts, row.side, row.entry, row.pnl, 1
        )
        for row in rows
    }
    print(
        f"signals={len(rows)} matched={len(actual)} misses={misses} "
        f"truncated={truncated}"
    )
    if delays:
        print(
            f"delay median={statistics.median(delays):.3f}s "
            f"p90={sorted(delays)[int(0.90*(len(delays)-1))]:.3f}s"
        )
    if slippage:
        ordered = sorted(slippage)
        print(
            f"actual-minus-candle mean={100*statistics.mean(slippage):+.3f}c "
            f"median={100*statistics.median(slippage):+.3f}c "
            f"p90={100*ordered[int(0.90*(len(ordered)-1))]:+.3f}c"
        )
    matched_modeled = [modeled[row.ticker] for row in actual]
    print(study.summarize("matched candle", matched_modeled))
    print(study.summarize("matched actual print", actual))


if __name__ == "__main__":
    main()
