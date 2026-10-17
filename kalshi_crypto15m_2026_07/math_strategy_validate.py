#!/usr/bin/env python3
"""Stress validation for candidates from math_strategy_search.py."""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict

import numpy as np
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import backtest as bt
import math_strategy_search as search


def load_markets() -> list[dict]:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))
    return sorted(markets, key=lambda market: market["close_ts"])


def bootstrap_days(trades: list[search.Trade], iterations: int = 20_000) -> dict:
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade.ts // 86400].append(trade.pnl)
    days = list(grouped)
    rng = random.Random(20260728)
    means = []
    for _ in range(iterations):
        pnl = count = 0
        for day in (rng.choice(days) for _ in days):
            pnl += sum(grouped[day])
            count += len(grouped[day])
        means.append(100 * pnl / count)
    means.sort()
    quantile = lambda probability: means[int(probability * (len(means) - 1))]
    return {
        "lo80": quantile(0.10),
        "hi80": quantile(0.90),
        "lo95": quantile(0.025),
        "hi95": quantile(0.975),
        "p_positive": sum(value > 0 for value in means) / len(means),
        "days": len(days),
    }


def segment(label: str, trades: list[search.Trade]) -> None:
    if not trades:
        print(f"{label:26} no trades")
        return
    mean = statistics.mean(trade.pnl for trade in trades)
    day_t = search._cluster_t(trades, lambda trade: trade.ts // 86400)
    p_value = 1 - norm.cdf(day_t) if not math.isnan(day_t) else float("nan")
    print(
        f"{label:26} n={len(trades):4d} "
        f"WR={sum(trade.win for trade in trades)/len(trades):6.2%} "
        f"entry={statistics.mean(trade.entry for trade in trades):.3f} "
        f"EV={100*mean:+6.3f}c day-t={day_t:+5.2f} p={p_value:.4f} "
        f"total=${sum(trade.pnl for trade in trades):+.2f}"
    )


def cap_per_interval(trades: list[search.Trade], cap: int) -> list[search.Trade]:
    priority = {series: index for index, series in enumerate(bt.SERIES)}
    grouped = defaultdict(list)
    for trade in trades:
        grouped[trade.ts].append(trade)
    selected = []
    for rows in grouped.values():
        rows.sort(key=lambda trade: priority[trade.series])
        selected.extend(rows[:cap])
    return sorted(selected, key=lambda trade: trade.ts)


def stable_candidate_validation(markets: list[dict]) -> None:
    print("=== Stable mid-band candidate: 75-90c, flat<=1c/2m, rem6-8 ===")
    trades = search.stable_midband(markets, 0.75, 0.90, 0.01, 6)
    research = [trade for trade in trades if not trade.oos]
    holdout = [trade for trade in trades if trade.oos]
    holdout_times = sorted({trade.ts for trade in holdout})
    split = holdout_times[len(holdout_times) // 2]
    holdout_1 = [trade for trade in holdout if trade.ts < split]
    holdout_2 = [trade for trade in holdout if trade.ts >= split]

    segment("research", research)
    segment("holdout all", holdout)
    segment("holdout first half", holdout_1)
    segment("holdout final half", holdout_2)
    print("\nBy asset")
    for series in bt.SERIES:
        segment(series, [trade for trade in trades if trade.series == series])
    print("\nBy decision minute")
    for rem in (6, 7, 8):
        segment(f"rem={rem}", [trade for trade in trades if trade.rem == rem])
    print("\nBy entry-price band")
    for low, high in ((0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 1.0)):
        segment(
            f"{low:.2f}-{high:.2f}",
            [trade for trade in trades if low <= trade.entry < high],
        )

    print("\nConcentration")
    segment("all positions", trades)
    segment("cap 2 / interval", cap_per_interval(trades, 2))
    segment("cap 1 / interval", cap_per_interval(trades, 1))
    counts = Counter(trade.ts for trade in trades)
    print(
        f"independent intervals={len(counts)}; "
        f"multi-position intervals={sum(count > 1 for count in counts.values())}; "
        f"maximum simultaneous={max(counts.values())}"
    )

    print("\nDay-cluster bootstrap")
    for label, part in (("all", trades), ("holdout", holdout), ("final half", holdout_2)):
        result = bootstrap_days(part)
        print(
            f"{label:12} days={result['days']:2d} "
            f"80%CI=[{result['lo80']:+.3f},{result['hi80']:+.3f}]c "
            f"95%CI=[{result['lo95']:+.3f},{result['hi95']:+.3f}]c "
            f"P(EV>0)={result['p_positive']:.1%}"
        )

    print("\nEntry-slippage sensitivity")
    for slippage_cents in (0.0, 0.10, 0.25, 0.50, 1.00, 1.50):
        adjusted = statistics.mean(trade.pnl - slippage_cents / 100 for trade in trades)
        adjusted_oos = statistics.mean(
            trade.pnl - slippage_cents / 100 for trade in holdout
        )
        print(
            f"+{slippage_cents:4.2f}c: all={100*adjusted:+.3f}c "
            f"holdout={100*adjusted_oos:+.3f}c"
        )

    print("\nParameter neighborhood (not for re-selection)")
    for flat in (0.005, 0.01, 0.02):
        for rem_low in (4, 6):
            rows = search.stable_midband(markets, 0.75, 0.90, flat, rem_low)
            segment(f"flat={flat:.3f} rem={rem_low}", rows)

    by_day = defaultdict(float)
    for trade in trades:
        by_day[trade.ts // 86400] += trade.pnl
    positive_days = sum(value > 0 for value in by_day.values())
    print(
        f"\npositive trading days={positive_days}/{len(by_day)}; "
        f"worst day=${min(by_day.values()):+.2f}; "
        f"best day=${max(by_day.values()):+.2f}"
    )


def _fit_model(rows: list[dict], cross_asset: bool):
    x = []
    for row in rows:
        features = row["features"] if cross_asset else [row["features"][0]]
        x.append(features)
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.25, max_iter=2000, random_state=7),
    )
    model.fit(np.asarray(x), np.asarray([row["yes"] for row in rows], dtype=int))
    return model


def _predict(model, rows: list[dict], cross_asset: bool) -> np.ndarray:
    x = [
        row["features"] if cross_asset else [row["features"][0]]
        for row in rows
    ]
    return model.predict_proba(np.asarray(x))[:, 1]


def _model_trades(rows: list[dict], probabilities: np.ndarray, margin: float):
    trades = []
    for row, probability in zip(rows, probabilities):
        yes_edge = probability - row["yes_ask"] - bt.fee(row["yes_ask"])
        no_edge = 1 - probability - row["no_ask"] - bt.fee(row["no_ask"])
        if max(yes_edge, no_edge) <= margin:
            continue
        side = "yes" if yes_edge >= no_edge else "no"
        entry = row["yes_ask"] if side == "yes" else row["no_ask"]
        if entry > 0.99:
            continue
        win = row["yes"] == (side == "yes")
        trades.append(
            search.Trade(
                row["ticker"], row["series"], row["ts"], entry,
                search._hold_pnl(entry, win), win, row["rem"], side
            )
        )
    return sorted(trades, key=lambda trade: trade.ts)


def cross_asset_validation(markets: list[dict]) -> None:
    print("\n=== Cross-asset residual equation at rem=6 ===")
    rows = search.cross_asset_rows(markets, 6)
    research = [row for row in rows if row["ts"] < search.OOS_TS]
    post = [row for row in rows if row["ts"] >= search.OOS_TS]
    post_times = sorted({row["ts"] for row in post})
    split = post_times[len(post_times) // 2]
    validation = [row for row in post if row["ts"] < split]
    final = [row for row in post if row["ts"] >= split]
    margins = (0.0, 0.005, 0.01, 0.015, 0.02)

    for name, cross_asset in (("own-price calibration", False),
                              ("own + cross-asset", True)):
        model = _fit_model(research, cross_asset)
        validation_probability = _predict(model, validation, cross_asset)
        final_probability = _predict(model, final, cross_asset)
        selected = None
        print(f"\n{name}")
        for margin in margins:
            trades = _model_trades(validation, validation_probability, margin)
            if len(trades) < 100:
                continue
            score = sum(trade.pnl for trade in trades)
            print(
                f" validation margin={margin:.3f}: n={len(trades):4d} "
                f"EV={search._mean_cents(trades):+.3f}c total=${score:+.2f}"
            )
            if selected is None or score > selected[0]:
                selected = (score, margin)
        if selected is None:
            print(" no valid threshold")
            continue
        margin = selected[1]
        final_trades = _model_trades(final, final_probability, margin)
        segment(f"FINAL margin={margin:.3f}", final_trades)
        result = bootstrap_days(final_trades)
        print(
            f" final bootstrap 95%CI=[{result['lo95']:+.3f},"
            f"{result['hi95']:+.3f}]c P(EV>0)={result['p_positive']:.1%}"
        )
        y_final = np.asarray([row["yes"] for row in final], dtype=int)
        mids = np.asarray([row["mid"] for row in final])
        print(
            f" predictive Brier={brier_score_loss(y_final, final_probability):.5f} "
            f"market={brier_score_loss(y_final, mids):.5f}; "
            f"logloss={log_loss(y_final, final_probability):.5f} "
            f"market={log_loss(y_final, mids):.5f}"
        )
        price_bands = Counter(
            int(trade.entry * 10) / 10 for trade in final_trades
        )
        print(" entry deciles:", dict(sorted(price_bands.items())))
        r1_tickers = {
            signal["market"]["ticker"]
            for signal in search.base_unbroken(markets, rem_low=6)
            if signal["market"]["close_ts"] >= split
        }
        overlap = sum(trade.ticker in r1_tickers for trade in final_trades)
        print(f" overlap with deployed R1={overlap}/{len(final_trades)}")


def own_price_rows(markets: list[dict], rem: int) -> list[dict]:
    rows = []
    for market in markets:
        candle = market["by_rem"].get(rem)
        nxt = market["by_rem"].get(rem - 1)
        if not candle or not nxt:
            continue
        try:
            bid, ask = float(candle["bc"]), float(candle["ac"])
            next_bid, next_ask = float(nxt["bo"]), float(nxt["ao"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 < bid <= ask < 1 or ask - bid > 0.02 + search.EPS:
            continue
        mid = (bid + ask) / 2
        clipped = min(max(mid, 0.005), 0.995)
        rows.append(
            {
                "ticker": market["ticker"],
                "series": market["series"],
                "ts": market["close_ts"],
                "features": [math.log(clipped / (1 - clipped))],
                "mid": mid,
                "yes_bid": bid,
                "yes_ask": max(ask, next_ask),
                "no_bid": 1 - ask,
                "no_ask": max(1 - bid, 1 - next_bid),
                "yes": market["result"] == "yes",
                "rem": rem,
            }
        )
    return rows


def _formula(model) -> tuple[float, float]:
    scaler = model.named_steps["standardscaler"]
    logistic = model.named_steps["logisticregression"]
    standardized_coefficient = float(logistic.coef_[0][0])
    scale = float(scaler.scale_[0])
    mean = float(scaler.mean_[0])
    slope = standardized_coefficient / scale
    intercept = float(logistic.intercept_[0]) - slope * mean
    return intercept, slope


def _simple_favorite(rows: list[dict], low: float, high: float) -> list[search.Trade]:
    trades = []
    for row in rows:
        favorite_side = "yes" if row["mid"] > 0.5 else "no"
        favorite_mid = max(row["mid"], 1 - row["mid"])
        if not low <= favorite_mid < high:
            continue
        entry = row["yes_ask"] if favorite_side == "yes" else row["no_ask"]
        if entry > 0.99:
            continue
        win = row["yes"] == (favorite_side == "yes")
        trades.append(
            search.Trade(
                row["ticker"], row["series"], row["ts"], entry,
                search._hold_pnl(entry, win), win, row["rem"], favorite_side
            )
        )
    return sorted(trades, key=lambda trade: trade.ts)


def own_price_deep_dive(markets: list[dict]) -> None:
    print("\n=== Own-price calibration equation deep dive ===")
    synchronized = search.cross_asset_rows(markets, 6)
    all_rows = own_price_rows(markets, 6)
    post_times = sorted({row["ts"] for row in synchronized if row["ts"] >= search.OOS_TS})
    split = post_times[len(post_times) // 2]
    market_by_ticker = {market["ticker"]: market for market in markets}

    for universe_name, rows in (("all individual books", all_rows),
                                ("all-four synchronized", synchronized)):
        research = [row for row in rows if row["ts"] < search.OOS_TS]
        validation = [row for row in rows if search.OOS_TS <= row["ts"] < split]
        final = [row for row in rows if row["ts"] >= split]
        model = _fit_model(research, False)
        intercept, slope = _formula(model)
        validation_probability = _predict(model, validation, False)
        final_probability = _predict(model, final, False)
        print(
            f"\n{universe_name}: p*=sigmoid({intercept:+.5f}"
            f" + {slope:.5f}*logit(market_mid))"
        )
        selected = None
        for margin in (0.0, 0.005, 0.01, 0.015, 0.02):
            trades = _model_trades(validation, validation_probability, margin)
            if len(trades) < 100:
                continue
            score = sum(trade.pnl for trade in trades)
            print(
                f" validation margin={margin:.3f}: n={len(trades):4d} "
                f"EV={search._mean_cents(trades):+.3f}c total=${score:+.2f}"
            )
            if selected is None or score > selected[0]:
                selected = (score, margin)
        if selected is None:
            continue
        margin = selected[1]
        final_trades = _model_trades(final, final_probability, margin)
        segment(f"FINAL margin={margin:.3f}", final_trades)
        boot = bootstrap_days(final_trades)
        print(
            f" final 95%CI=[{boot['lo95']:+.3f},{boot['hi95']:+.3f}]c "
            f"P(EV>0)={boot['p_positive']:.1%}"
        )
        final_tickers = {trade.ticker for trade in final_trades}
        final_probability_by_ticker = {
            row["ticker"]: probability
            for row, probability in zip(final, final_probability)
        }
        print(" By side")
        for side in ("yes", "no"):
            segment(side, [trade for trade in final_trades if trade.side == side])
        print(" By asset")
        for series in bt.SERIES:
            segment(
                series,
                [trade for trade in final_trades if trade.series == series],
            )
        print(" By entry band")
        for low, high in ((0.50, 0.70), (0.70, 0.80), (0.80, 0.90),
                          (0.90, 0.95), (0.95, 1.00)):
            segment(
                f"{low:.2f}-{high:.2f}",
                [trade for trade in final_trades if low <= trade.entry < high],
            )
        print(" By path state")
        unbroken = []
        flipped = []
        for trade in final_trades:
            market = market_by_ticker[trade.ticker]
            flips, _, _ = search._path_state(search._sequence(market), 6)
            (unbroken if flips == 0 else flipped).append(trade)
        segment("unbroken", unbroken)
        segment("flipped", flipped)
        print(" Concentration")
        segment("cap 2 / interval", cap_per_interval(final_trades, 2))
        segment("cap 1 / interval", cap_per_interval(final_trades, 1))
        print(" Slippage")
        for cents in (0.0, 0.25, 0.50, 1.00):
            value = statistics.mean(trade.pnl - cents / 100 for trade in final_trades)
            print(f"  +{cents:.2f}c -> {100*value:+.3f}c")
        print(" Simple-rule comparison on same final universe")
        for low, high in ((0.70, 0.90), (0.75, 0.90), (0.80, 0.90),
                          (0.90, 0.99), (0.70, 0.99)):
            simple = _simple_favorite(final, low, high)
            segment(f"favorite {low:.2f}-{high:.2f}", simple)
        # The recalibration model should be a favorite rule, not a hidden
        # contrarian bet. Count any exceptions explicitly.
        contrarian = 0
        for row in final:
            if row["ticker"] not in final_tickers:
                continue
            probability = final_probability_by_ticker[row["ticker"]]
            yes_edge = probability - row["yes_ask"] - bt.fee(row["yes_ask"])
            no_edge = 1 - probability - row["no_ask"] - bt.fee(row["no_ask"])
            model_side = "yes" if yes_edge >= no_edge else "no"
            favorite_side = "yes" if row["mid"] > 0.5 else "no"
            contrarian += model_side != favorite_side
        print(f" contrarian selections={contrarian}/{len(final_trades)}")

    print("\nExpanding-window stability, all individual books, fixed 1c margin")
    rows = all_rows
    timestamps = sorted({row["ts"] for row in rows})
    start = timestamps[0]
    for train_days in (30, 40, 50, 60):
        train_end = start + train_days * 86400
        test_end = train_end + 10 * 86400
        train = [row for row in rows if row["ts"] < train_end]
        test = [row for row in rows if train_end <= row["ts"] < test_end]
        if len(train) < 1000 or not test:
            continue
        model = _fit_model(train, False)
        probability = _predict(model, test, False)
        trades = _model_trades(test, probability, 0.01)
        intercept, slope = _formula(model)
        print(
            f"train {train_days:2d}d -> next10d: formula=({intercept:+.3f},"
            f"{slope:.3f})"
        )
        segment("walk-forward", trades)


def synchronized_calibration_stress(markets: list[dict]) -> None:
    print("\n=== Synchronized calibration: adjacency and incremental edge ===")
    base_signals = search.base_unbroken(markets, rem_low=6)
    r1_by_period = defaultdict(set)
    for signal in base_signals:
        r1_by_period["all"].add(signal["market"]["ticker"])

    for rem in (5, 6, 7):
        rows = search.cross_asset_rows(markets, rem)
        research = [row for row in rows if row["ts"] < search.OOS_TS]
        post = [row for row in rows if row["ts"] >= search.OOS_TS]
        times = sorted({row["ts"] for row in post})
        split = times[len(times) // 2]
        validation = [row for row in post if row["ts"] < split]
        final = [row for row in post if row["ts"] >= split]
        model = _fit_model(research, False)
        validation_probability = _predict(model, validation, False)
        final_probability = _predict(model, final, False)
        selected = None
        print(f"\nrem={rem}, formula={_formula(model)}")
        for margin in (0.0, 0.005, 0.01, 0.015, 0.02):
            validation_trades = _model_trades(
                validation, validation_probability, margin
            )
            if len(validation_trades) < 100:
                continue
            total = sum(trade.pnl for trade in validation_trades)
            print(
                f" margin={margin:.3f} validation n={len(validation_trades):4d} "
                f"EV={search._mean_cents(validation_trades):+.3f}c "
                f"total=${total:+.2f}"
            )
            if total > 0 and (selected is None or total > selected[0]):
                selected = (total, margin, validation_trades)
        if selected is None:
            print(" REJECT: no positive validation threshold")
            continue
        _, margin, validation_trades = selected
        final_trades = _model_trades(final, final_probability, margin)
        segment(f"selected validation", validation_trades)
        segment(f"untouched final", final_trades)
        for label, trades in (("validation", validation_trades),
                              ("final", final_trades)):
            incremental = [
                trade for trade in trades
                if trade.ticker not in r1_by_period["all"]
            ]
            segment(f"{label} non-R1", incremental)
            print(f" {label} by entry band")
            for low, high in ((0.70, 0.80), (0.80, 0.90),
                              (0.90, 0.95), (0.95, 1.00)):
                segment(
                    f"  {low:.2f}-{high:.2f}",
                    [trade for trade in trades if low <= trade.entry < high],
                )

    print("\nExpanding-window synchronized rem=6, fixed 1c margin")
    rows = search.cross_asset_rows(markets, 6)
    timestamps = sorted({row["ts"] for row in rows})
    start = timestamps[0]
    for train_days in (20, 30, 40, 50, 60):
        train_end = start + train_days * 86400
        test_end = train_end + 10 * 86400
        train = [row for row in rows if row["ts"] < train_end]
        test = [row for row in rows if train_end <= row["ts"] < test_end]
        if len(train) < 1000 or not test:
            continue
        model = _fit_model(train, False)
        probability = _predict(model, test, False)
        trades = _model_trades(test, probability, 0.01)
        print(
            f"train {train_days:2d}d -> next10d formula={_formula(model)}"
        )
        segment("walk-forward", trades)


def main() -> None:
    markets = load_markets()
    print(f"Loaded {len(markets)} markets\n")
    stable_candidate_validation(markets)
    cross_asset_validation(markets)
    own_price_deep_dive(markets)
    synchronized_calibration_stress(markets)


if __name__ == "__main__":
    main()
