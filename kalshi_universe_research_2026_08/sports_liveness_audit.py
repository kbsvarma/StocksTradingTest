#!/usr/bin/env python3
"""Replay sports lock lanes with the live liveness and causal fill rules."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from common import cluster_bootstrap, contract_pnl, summarize, utc_day


FAMILIES = {
    "KXWTAMATCH": 10,
    "KXWNBAGAME": 5,
    "KXWCGAME": 5,
    "KXATPMATCH": 10,
    "KXITFMATCH": 10,
    "KXITFWMATCH": 10,
    "KXATPCHALLENGERMATCH": 10,
    "KXWTACHALLENGERMATCH": 10,
    "KXUFCFIGHT": 5,
    "KXMLBGAME": 5,
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def event_key(ticker: str) -> str:
    return ticker.rsplit("-", 1)[0]


def side_quote(candle: dict, side: str, phase: str) -> tuple[float, float, float] | None:
    suffix = "close_dollars" if phase == "close" else "open_dollars"
    try:
        yes_bid = float(candle["yes_bid"][suffix])
        yes_ask = float(candle["yes_ask"][suffix])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 <= yes_bid <= yes_ask <= 1.0:
        return None
    bid, ask = (yes_bid, yes_ask) if side == "yes" else (1.0 - yes_ask, 1.0 - yes_bid)
    return bid, ask, (bid + ask) / 2.0


def favorite(candle: dict, phase: str = "close") -> tuple[str, float, float, float] | None:
    yes = side_quote(candle, "yes", phase)
    if yes is None:
        return None
    return ("yes", *yes) if yes[2] >= 0.5 else ("no", *side_quote(candle, "no", phase))


def market_candidate(
    row: dict,
    *,
    hold: int,
    minimum_volume_15m: float,
    band: tuple[float, float] = (0.97, 0.99),
) -> dict | None:
    candles = sorted(row.get("candles", []), key=lambda candle: int(candle["end_period_ts"]))
    by_ts = {int(candle["end_period_ts"]): candle for candle in candles}
    for signal in candles:
        signal_ts = int(signal["end_period_ts"])
        current = favorite(signal)
        if current is None:
            continue
        side, bid, ask, midpoint = current
        if not band[0] <= midpoint < band[1] or ask - bid > 0.020001 or ask > 0.99:
            continue
        history = []
        valid = True
        for offset in range(hold - 1, -1, -1):
            prior = by_ts.get(signal_ts - offset * 60)
            prior_favorite = favorite(prior) if prior else None
            if (
                prior_favorite is None
                or prior_favorite[0] != side
                or prior_favorite[3] < band[0]
            ):
                valid = False
                break
            history.append(prior_favorite)
        if not valid:
            continue
        volume_15m = sum(
            float(candle.get("volume_fp") or 0.0)
            for timestamp, candle in by_ts.items()
            if signal_ts - 15 * 60 < timestamp <= signal_ts
        )
        if volume_15m < minimum_volume_15m:
            continue
        execution = by_ts.get(signal_ts + 60)
        execution_quote = side_quote(execution, side, "open") if execution else None
        if execution_quote is None:
            continue
        entry = max(ask, execution_quote[1])
        if entry > 0.99:
            continue
        won = (row["result"] == "yes") == (side == "yes")
        return {
            "event": event_key(row["ticker"]),
            "ticker": row["ticker"],
            "timestamp": signal_ts + 60,
            "cluster": utc_day(signal_ts),
            "side": side,
            "entry": entry,
            "volume_15m": volume_15m,
            "won": won,
            "pnl": contract_pnl(entry, won),
            "pnl_stress_1c": contract_pnl(min(0.999, entry + 0.01), won),
        }
    return None


def replay(path: Path, hold: int, minimum_volume_15m: float) -> list[dict]:
    candidates: dict[str, list[dict]] = defaultdict(list)
    for row in load_jsonl(path):
        if row.get("result") not in ("yes", "no"):
            continue
        trade = market_candidate(
            row,
            hold=hold,
            minimum_volume_15m=minimum_volume_15m,
        )
        if trade:
            candidates[trade["event"]].append(trade)
    return sorted(
        (min(rows, key=lambda trade: (trade["timestamp"], trade["entry"], trade["ticker"])) for rows in candidates.values()),
        key=lambda trade: (trade["timestamp"], trade["ticker"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("../kalshi_sports_2026_08/data"))
    parser.add_argument("--output", type=Path, default=Path("results/sports_liveness_audit.json"))
    args = parser.parse_args()
    rows = []
    for family, hold in FAMILIES.items():
        path = args.data_dir / f"games_{family}.jsonl"
        if not path.exists():
            continue
        for volume_gate in (0.0, 200.0):
            trades = replay(path, hold, volume_gate)
            metrics = summarize(trades)
            if volume_gate == 200.0 and trades:
                metrics["bootstrap"] = cluster_bootstrap(trades, iterations=10_000)
            rows.append(
                {
                    "family": family,
                    "hold": hold,
                    "minimum_volume_15m": volume_gate,
                    "metrics": metrics,
                }
            )
    payload = {"rules": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
