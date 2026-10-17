#!/usr/bin/env python3
"""Walk-forward state-value research for Kalshi 15-minute crypto markets.

This study treats each market as a finite-horizon decision problem.  At a
completed minute candle, the available actions are:

1. skip;
2. buy the current favorite and hold to settlement;
3. buy the current favorite and sell at the executable bid after 1, 2, or
   3 completed minutes.

The settlement equation is a regularized logistic model.  The resale equations
are regularized linear models of realized round-trip P&L.  Entries use the worse
of the signal-close ask and next-minute open ask.  Exits use a later close bid.
Taker fees are charged on every executed leg.

All model settings and action thresholds are fixed across expanding ten-day
walk-forward tests.  This is research only; it does not alter the live bot.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import backtest as bt
import math_strategy_search as search


EPS = 1e-9
DECISION_REMAINDERS = (8, 7, 6, 5)
HORIZONS = (1, 2, 3)
HOLD_EDGE_FLOOR = 0.015
SCALP_EDGE_FLOOR = 0.0075


@dataclass(frozen=True)
class StateRow:
    ticker: str
    series: str
    ts: int
    rem: int
    side: str
    favorite_mid: float
    entry: float
    win: bool
    features: tuple[float, ...]
    scalp_pnls: tuple[float | None, ...]
    flips: int
    persistence: int


@dataclass(frozen=True)
class PolicyTrade:
    ticker: str
    series: str
    ts: int
    rem: int
    side: str
    entry: float
    action: str
    predicted_edge: float
    pnl: float
    win: bool
    flips: int


def _favorite_mid_at(market: dict, rem: int, side: str) -> float | None:
    quote = bt.fav_quotes(market["by_rem"].get(rem) or {})
    if not quote:
        return None
    return quote[3] if quote[0] == side else 1 - quote[3]


def _persistence(seq: dict, rem: int, side: str) -> int:
    count = 0
    for earlier_rem in range(rem, 15):
        quote = seq.get(earlier_rem)
        if not quote or quote[0] != side:
            break
        count += 1
    return count


def _safe_change(current: float, prior: float | None) -> float:
    return current - prior if prior is not None else 0.0


def _state_features(
    market: dict,
    seq: dict,
    rem: int,
    side: str,
    favorite_mid: float,
    spread: float,
    flips: int,
    persistence: int,
) -> tuple[float, ...]:
    prior_mids = [
        _favorite_mid_at(market, rem + lag, side)
        for lag in range(1, 6)
    ]
    usable = [favorite_mid] + [value for value in prior_mids if value is not None]
    changes = [usable[index] - usable[index + 1] for index in range(len(usable) - 1)]
    path_volatility = statistics.pstdev(changes) if len(changes) > 1 else 0.0
    drift_1 = _safe_change(favorite_mid, prior_mids[0])
    drift_2 = _safe_change(favorite_mid, prior_mids[1])
    drift_3 = _safe_change(favorite_mid, prior_mids[2])

    candle = market["by_rem"].get(rem) or {}
    try:
        volume = max(float(candle.get("v") or 0), 0.0)
    except (TypeError, ValueError):
        volume = 0.0
    prior_volumes = []
    for prior_rem in range(rem + 1, min(rem + 6, 15)):
        prior = market["by_rem"].get(prior_rem) or {}
        try:
            prior_volumes.append(max(float(prior.get("v") or 0), 0.0))
        except (TypeError, ValueError):
            continue
    average_prior_volume = statistics.mean(prior_volumes) if prior_volumes else volume
    relative_volume = math.log1p(volume) - math.log1p(average_prior_volume)

    clipped = min(max(favorite_mid, 0.005), 0.995)
    logit_mid = math.log(clipped / (1 - clipped))
    hour = time.gmtime(market["close_ts"]).tm_hour
    angle = 2 * math.pi * hour / 24
    asset_flags = tuple(
        1.0 if market["series"] == series else 0.0
        for series in bt.SERIES[:-1]
    )
    return (
        logit_mid,
        logit_mid * logit_mid,
        drift_1,
        drift_2,
        drift_3,
        path_volatility,
        min(flips, 4) / 4,
        min(persistence, 8) / 8,
        spread,
        relative_volume,
        math.sin(angle),
        math.cos(angle),
        *asset_flags,
    )


def build_rows(markets: list[dict], rem: int) -> list[StateRow]:
    rows = []
    for market in markets:
        seq = search._sequence(market)
        quote = seq.get(rem)
        if not quote:
            continue
        side, favorite_bid, favorite_ask, favorite_mid = quote
        spread = favorite_ask - favorite_bid
        if not 0.70 <= favorite_mid < 0.99 or spread > 0.02 + EPS:
            continue
        entry = search._entry(market, rem, side, favorite_ask)
        if entry is None or entry > 0.99:
            continue
        flips, observed, _ = search._path_state(seq, rem)
        if observed < 4:
            continue
        persistence = _persistence(seq, rem, side)
        scalp_pnls = []
        for horizon in HORIZONS:
            exit_rem = rem - 1 - horizon
            if exit_rem < 1:
                scalp_pnls.append(None)
                continue
            exit_quote = search._side_quotes(
                market["by_rem"].get(exit_rem) or {}, side
            )
            scalp_pnls.append(
                search._exit_pnl(entry, exit_quote[0]) if exit_quote else None
            )
        rows.append(
            StateRow(
                ticker=market["ticker"],
                series=market["series"],
                ts=market["close_ts"],
                rem=rem,
                side=side,
                favorite_mid=favorite_mid,
                entry=entry,
                win=market["result"] == side,
                features=_state_features(
                    market, seq, rem, side, favorite_mid, spread,
                    flips, persistence
                ),
                scalp_pnls=tuple(scalp_pnls),
                flips=flips,
                persistence=persistence,
            )
        )
    return sorted(rows, key=lambda row: row.ts)


def _fit_settlement(rows: list[StateRow]):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.08, max_iter=3000, random_state=17),
    )
    model.fit(
        np.asarray([row.features for row in rows]),
        np.asarray([row.win for row in rows], dtype=int),
    )
    return model


def _fit_scalp(rows: list[StateRow], horizon_index: int):
    usable = [
        row for row in rows
        if row.scalp_pnls[horizon_index] is not None
    ]
    model = make_pipeline(StandardScaler(), Ridge(alpha=80.0))
    model.fit(
        np.asarray([row.features for row in usable]),
        np.asarray([row.scalp_pnls[horizon_index] for row in usable]),
    )
    return model


def _evaluate_policy(
    train: list[StateRow],
    test: list[StateRow],
    allow_scalps: bool,
) -> tuple[list[PolicyTrade], dict]:
    settlement = _fit_settlement(train)
    probabilities = settlement.predict_proba(
        np.asarray([row.features for row in test])
    )[:, 1]
    scalp_models = [
        _fit_scalp(train, index) for index, _ in enumerate(HORIZONS)
    ]
    scalp_predictions = [
        model.predict(np.asarray([row.features for row in test]))
        for model in scalp_models
    ]

    trades = []
    for index, (row, probability) in enumerate(zip(test, probabilities)):
        hold_edge = probability - row.entry - bt.fee(row.entry)
        choices = [("hold", hold_edge, search._hold_pnl(row.entry, row.win))]
        if allow_scalps:
            for horizon_index, horizon in enumerate(HORIZONS):
                actual = row.scalp_pnls[horizon_index]
                if actual is None:
                    continue
                choices.append(
                    (
                        f"exit_{horizon}m",
                        float(scalp_predictions[horizon_index][index]),
                        actual,
                    )
                )
        action, predicted_edge, actual_pnl = max(
            choices, key=lambda choice: choice[1]
        )
        edge_floor = HOLD_EDGE_FLOOR if action == "hold" else SCALP_EDGE_FLOOR
        if predicted_edge <= edge_floor:
            continue
        trades.append(
            PolicyTrade(
                ticker=row.ticker,
                series=row.series,
                ts=row.ts,
                rem=row.rem,
                side=row.side,
                entry=row.entry,
                action=action,
                predicted_edge=predicted_edge,
                pnl=actual_pnl,
                win=row.win,
                flips=row.flips,
            )
        )

    outcomes = np.asarray([row.win for row in test], dtype=int)
    market_probabilities = np.asarray([row.favorite_mid for row in test])
    metrics = {
        "brier_model": brier_score_loss(outcomes, probabilities),
        "brier_market": brier_score_loss(outcomes, market_probabilities),
        "logloss_model": log_loss(outcomes, probabilities),
        "logloss_market": log_loss(outcomes, market_probabilities),
    }
    return sorted(trades, key=lambda trade: trade.ts), metrics


def _day_t(trades: list[PolicyTrade]) -> float:
    if len(trades) < 2:
        return float("nan")
    by_day = defaultdict(list)
    for trade in trades:
        by_day[trade.ts // 86400].append(trade.pnl)
    values = [sum(rows) for rows in by_day.values()]
    if len(values) < 2:
        return float("nan")
    deviation = statistics.stdev(values)
    return statistics.mean(values) / (deviation / math.sqrt(len(values))) if deviation else 0


def _max_drawdown(trades: list[PolicyTrade]) -> float:
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda item: item.ts):
        equity += trade.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def report(label: str, trades: list[PolicyTrade]) -> None:
    if not trades:
        print(f"{label:42} no trades")
        return
    duration = max((trades[-1].ts - trades[0].ts) / 86400, 1)
    action_counts = Counter(trade.action for trade in trades)
    print(
        f"{label:42} n={len(trades):5d} "
        f"EV={100*statistics.mean(trade.pnl for trade in trades):+6.3f}c "
        f"total=${sum(trade.pnl for trade in trades):+7.2f} "
        f"tr/d={len(trades)/duration:5.1f} day-t={_day_t(trades):+5.2f} "
        f"DD@1=${_max_drawdown(trades):+6.2f} actions={dict(action_counts)}"
    )


def cap_one_per_interval(trades: list[PolicyTrade]) -> list[PolicyTrade]:
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade.ts].append(trade)
    selected = []
    for rows in grouped.values():
        selected.append(max(rows, key=lambda trade: trade.predicted_edge))
    return sorted(selected, key=lambda trade: trade.ts)


def walk_forward(rows: list[StateRow], allow_scalps: bool) -> list[PolicyTrade]:
    start = min(row.ts for row in rows)
    all_trades = []
    mode = "dynamic" if allow_scalps else "hold-only"
    for train_days in (30, 40, 50, 60):
        train_end = start + train_days * 86400
        test_end = train_end + 10 * 86400
        train = [row for row in rows if row.ts < train_end]
        test = [row for row in rows if train_end <= row.ts < test_end]
        if len(train) < 2000 or not test:
            continue
        trades, metrics = _evaluate_policy(train, test, allow_scalps)
        print(
            f"  train={train_days:2d}d {mode:9} "
            f"Brier model/market={metrics['brier_model']:.5f}/"
            f"{metrics['brier_market']:.5f} "
            f"logloss={metrics['logloss_model']:.5f}/"
            f"{metrics['logloss_market']:.5f}"
        )
        report("    next 10d", trades)
        all_trades.extend(trades)
    return sorted(all_trades, key=lambda trade: trade.ts)


def overlap_report(
    trades: list[PolicyTrade],
    r1_tickers: set[str],
) -> None:
    existing = [trade for trade in trades if trade.ticker in r1_tickers]
    incremental = [trade for trade in trades if trade.ticker not in r1_tickers]
    report("  overlap with existing R1", existing)
    report("  genuinely incremental", incremental)
    report("  incremental cap1/interval", cap_one_per_interval(incremental))
    for state_name, state_rows in (
        ("incremental unbroken", [trade for trade in incremental if trade.flips == 0]),
        ("incremental flipped", [trade for trade in incremental if trade.flips > 0]),
    ):
        report(f"  {state_name}", state_rows)


def main() -> None:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))
    markets.sort(key=lambda market: market["close_ts"])
    r1_tickers = {
        signal["market"]["ticker"]
        for signal in search.base_unbroken(markets, rem_low=6)
    }
    print(f"Loaded {len(markets)} markets; existing-R1 tickers={len(r1_tickers)}")
    print(
        f"Fixed floors: hold={100*HOLD_EDGE_FLOOR:.2f}c, "
        f"scalp={100*SCALP_EDGE_FLOOR:.2f}c"
    )

    for rem in DECISION_REMAINDERS:
        rows = build_rows(markets, rem)
        print(f"\n=== DECISION rem={rem}: rows={len(rows)} ===")
        hold = walk_forward(rows, allow_scalps=False)
        report("HOLD aggregate", hold)
        overlap_report(hold, r1_tickers)

        dynamic = walk_forward(rows, allow_scalps=True)
        report("DYNAMIC aggregate", dynamic)
        overlap_report(dynamic, r1_tickers)


if __name__ == "__main__":
    main()
