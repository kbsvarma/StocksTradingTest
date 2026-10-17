#!/usr/bin/env python3
"""Independent-underlying state-value study for 15-minute crypto markets.

The core variable is the standardized distance from the live perpetual index
to the Kalshi strike:

    z = signed(index - strike) / (strike * sigma_1m * sqrt(minutes_remaining))

The sign is from the current Kalshi favorite's perspective.  A regularized
logistic equation combines z, recent index drift, and Kalshi's own market
probability.  Strict expanding walk-forward tests compare that equation with a
market-price-only calibration on the exact same rows and executable entries.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import backtest as bt
import math_strategy_search as search
import stabilized_flip_study as stabilized
import state_value_study as state


PERP_ROOT = (
    "/Users/varmakammili/Documents/GitHub/StocksTradingTest/"
    "kalshi_perps_2026_07/data"
)
PERP_BY_SERIES = {
    "KXBTC15M": "KXBTCPERP",
    "KXETH15M": "KXETHPERP",
    "KXSOL15M": "KXSOLPERP",
    "KXXRP15M": "KXXRPPERP",
}
EDGE_FLOOR = 0.015


@dataclass(frozen=True)
class PerpRow:
    ticker: str
    series: str
    ts: int
    rem: int
    side: str
    favorite_mid: float
    entry: float
    win: bool
    market_features: tuple[float, ...]
    perp_features: tuple[float, ...]
    z: float


def load_perp(series: str) -> dict[int, float]:
    ticker = PERP_BY_SERIES[series]
    path = os.path.join(PERP_ROOT, f"perp_candles_{ticker}.jsonl")
    mids = {}
    with open(path) as handle:
        for line in handle:
            record = json.loads(line)
            for candle in record.get("c") or []:
                try:
                    bid = float(candle["b"])
                    ask = float(candle["a"])
                    timestamp = int(candle["t"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 < bid <= ask:
                    mids[timestamp] = (bid + ask) / 2
    return mids


def load_strikes(series: str) -> dict[str, float]:
    path = os.path.join(
        os.path.dirname(__file__), "data", f"markets_{series}.jsonl"
    )
    strikes = {}
    with open(path) as handle:
        for line in handle:
            market = json.loads(line)
            if market.get("floor_strike") is not None:
                strikes[market["ticker"]] = float(market["floor_strike"])
    return strikes


def mid_at(mids: dict[int, float], timestamp: int, tolerance: int = 180):
    for delay in range(0, tolerance + 1, 60):
        value = mids.get(timestamp - delay)
        if value is not None:
            return value
    return None


def robust_sigma(mids: dict[int, float], timestamp: int) -> float | None:
    values = [
        mids[sample]
        for sample in range(timestamp - 900, timestamp + 1, 60)
        if sample in mids
    ]
    returns = [
        (later - earlier) / earlier
        for earlier, later in zip(values, values[1:])
        if earlier > 0
    ]
    if len(returns) < 8:
        return None
    center = statistics.median(returns)
    mad = statistics.median(abs(value - center) for value in returns)
    sigma = 1.4826 * mad
    return sigma if sigma > 1e-8 else None


def build_rows(markets: list[dict], rem: int) -> list[PerpRow]:
    perps = {series: load_perp(series) for series in PERP_BY_SERIES}
    strikes = {series: load_strikes(series) for series in PERP_BY_SERIES}
    rows = []
    for market in markets:
        series = market["series"]
        strike = strikes[series].get(market["ticker"])
        if not strike:
            continue
        quote = bt.fav_quotes(market["by_rem"].get(rem) or {})
        if not quote:
            continue
        side, favorite_bid, favorite_ask, favorite_mid = quote
        if not 0.55 <= favorite_mid < 0.99:
            continue
        if favorite_ask - favorite_bid > 0.02 + search.EPS:
            continue
        entry = search._entry(market, rem, side, favorite_ask)
        if entry is None or entry > 0.99:
            continue

        decision_ts = market["close_ts"] - rem * 60
        perp_mid = mid_at(perps[series], decision_ts)
        perp_mid_3 = mid_at(perps[series], decision_ts - 180)
        sigma = robust_sigma(perps[series], decision_ts)
        if perp_mid is None or perp_mid_3 is None or sigma is None:
            continue
        scale = 10 ** round(math.log10(strike / perp_mid))
        index = perp_mid * scale
        index_3 = perp_mid_3 * scale
        direction = 1 if side == "yes" else -1
        signed_margin = direction * (index - strike) / strike
        z = signed_margin / (sigma * math.sqrt(rem))
        signed_drift_3 = direction * (index - index_3) / strike / sigma
        clipped = min(max(favorite_mid, 0.005), 0.995)
        logit_mid = math.log(clipped / (1 - clipped))
        market_features = (logit_mid, logit_mid * logit_mid)
        perp_features = (
            logit_mid,
            logit_mid * logit_mid,
            max(min(z, 8), -8),
            math.copysign(min(z * z, 64), z),
            max(min(signed_drift_3, 8), -8),
            max(min(sigma * 10_000, 20), 0),
        )
        rows.append(
            PerpRow(
                ticker=market["ticker"],
                series=series,
                ts=market["close_ts"],
                rem=rem,
                side=side,
                favorite_mid=favorite_mid,
                entry=entry,
                win=market["result"] == side,
                market_features=market_features,
                perp_features=perp_features,
                z=z,
            )
        )
    return sorted(rows, key=lambda row: row.ts)


def fit(rows: list[PerpRow], use_perp: bool):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.08, max_iter=3000, random_state=23),
    )
    model.fit(
        np.asarray([
            row.perp_features if use_perp else row.market_features
            for row in rows
        ]),
        np.asarray([row.win for row in rows], dtype=int),
    )
    return model


def evaluate(model, rows: list[PerpRow], use_perp: bool):
    probabilities = model.predict_proba(
        np.asarray([
            row.perp_features if use_perp else row.market_features
            for row in rows
        ])
    )[:, 1]
    trades = []
    for row, probability in zip(rows, probabilities):
        predicted_edge = probability - row.entry - bt.fee(row.entry)
        if predicted_edge <= EDGE_FLOOR:
            continue
        trades.append(
            state.PolicyTrade(
                ticker=row.ticker,
                series=row.series,
                ts=row.ts,
                rem=row.rem,
                side=row.side,
                entry=row.entry,
                action="hold",
                predicted_edge=predicted_edge,
                pnl=search._hold_pnl(row.entry, row.win),
                win=row.win,
                flips=0,
            )
        )
    outcomes = np.asarray([row.win for row in rows], dtype=int)
    probabilities_market = np.asarray([row.favorite_mid for row in rows])
    return trades, {
        "brier_model": brier_score_loss(outcomes, probabilities),
        "brier_market": brier_score_loss(outcomes, probabilities_market),
        "logloss_model": log_loss(outcomes, probabilities),
        "logloss_market": log_loss(outcomes, probabilities_market),
    }


def main() -> None:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))
    markets.sort(key=lambda market: market["close_ts"])
    deployed = {
        signal["market"]["ticker"]
        for signal in search.base_unbroken(markets, rem_low=6)
    }
    deployed.update(
        trade["ticker"] for trade in stabilized.run(markets, 5, 0.93, 0.95)
    )

    for rem in (7, 6, 5):
        rows = build_rows(markets, rem)
        start = min(row.ts for row in rows)
        end = max(row.ts for row in rows)
        print(
            f"\n=== rem={rem}, aligned rows={len(rows)}, "
            f"span={(end-start)/86400:.1f}d ==="
        )
        aggregates = {False: [], True: []}
        for train_days in (21, 28, 35):
            train_end = start + train_days * 86400
            test_end = train_end + 7 * 86400
            train = [row for row in rows if row.ts < train_end]
            test = [row for row in rows if train_end <= row.ts < test_end]
            if len(train) < 1500 or not test:
                continue
            print(f"\nfold train={train_days}d test={len(test)}")
            for use_perp in (False, True):
                label = "market+perp" if use_perp else "market only"
                model = fit(train, use_perp)
                trades, metrics = evaluate(model, test, use_perp)
                incremental = [
                    trade for trade in trades if trade.ticker not in deployed
                ]
                print(
                    f"  {label:11} Brier={metrics['brier_model']:.5f}/"
                    f"{metrics['brier_market']:.5f} "
                    f"logloss={metrics['logloss_model']:.5f}/"
                    f"{metrics['logloss_market']:.5f}"
                )
                state.report("    all selected", trades)
                state.report("    incremental", incremental)
                aggregates[use_perp].extend(incremental)
        print("\naggregate incremental")
        state.report("  market only", aggregates[False])
        state.report("  market+perp", aggregates[True])
        state.report(
            "  perp cap1/interval",
            state.cap_one_per_interval(aggregates[True]),
        )


if __name__ == "__main__":
    main()
