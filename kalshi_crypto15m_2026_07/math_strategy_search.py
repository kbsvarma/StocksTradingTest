#!/usr/bin/env python3
"""Executable mathematical strategy search for Kalshi 15-minute crypto markets.

Every decision uses a completed minute candle. Entries use the worse of the
decision-close ask and next-minute open ask. Exits use a later minute-close bid.
Taker fees are charged on both entry and exit. The chronological split is fixed
before any sweep: pre-2026-07-07 is research, later data is holdout.
"""

from __future__ import annotations

import calendar
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import backtest as bt


OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
EPS = 1e-9


@dataclass(frozen=True)
class Trade:
    ticker: str
    series: str
    ts: int
    entry: float
    pnl: float
    win: bool
    rem: int
    side: str
    exit_price: float | None = None
    exited: bool = False

    @property
    def oos(self) -> bool:
        return self.ts >= OOS_TS


def _side_quotes(candle: dict, side: str, when: str = "close") -> tuple[float, float] | None:
    if when == "open":
        bid_key, ask_key = "bo", "ao"
    else:
        bid_key, ask_key = "bc", "ac"
    try:
        yes_bid = float(candle[bid_key])
        yes_ask = float(candle[ask_key])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= yes_bid <= yes_ask <= 1):
        return None
    return (yes_bid, yes_ask) if side == "yes" else (1 - yes_ask, 1 - yes_bid)


def _sequence(market: dict) -> dict[int, tuple[str, float, float, float]]:
    seq = {}
    for rem in range(14, 0, -1):
        quote = bt.fav_quotes(market["by_rem"].get(rem) or {})
        if quote:
            seq[rem] = quote
    return seq


def _path_state(seq: dict, rem: int) -> tuple[int, int, int | None]:
    flips = observed = 0
    previous = None
    earliest = None
    for earlier in range(14, rem - 1, -1):
        quote = seq.get(earlier)
        if not quote:
            continue
        observed += 1
        if earliest is None:
            earliest = earlier
        if previous is not None and quote[0] != previous:
            flips += 1
        previous = quote[0]
    return flips, observed, earliest


def _same_side_mid(seq: dict, rem: int, side: str) -> float | None:
    quote = seq.get(rem)
    if not quote:
        return None
    return quote[3] if quote[0] == side else 1 - quote[3]


def _entry(market: dict, rem: int, side: str, signal_ask: float) -> float | None:
    nxt = market["by_rem"].get(rem - 1)
    if not nxt:
        return None
    quote = _side_quotes(nxt, side, "open")
    if not quote:
        return None
    return max(signal_ask, quote[1])


def _hold_pnl(entry: float, win: bool) -> float:
    return ((1 - entry) if win else -entry) - bt.fee(entry)


def _exit_pnl(entry: float, exit_bid: float) -> float:
    return exit_bid - entry - bt.fee(entry) - bt.fee(exit_bid)


def _mean_cents(trades: list[Trade]) -> float:
    return 100 * statistics.mean(t.pnl for t in trades) if trades else float("nan")


def _cluster_t(trades: list[Trade], key) -> float:
    if len(trades) < 2:
        return float("nan")
    mean = statistics.mean(t.pnl for t in trades)
    groups: dict[object, list[float]] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade.pnl)
    if len(groups) < 2:
        return float("nan")
    variance = (
        len(groups)
        / (len(groups) - 1)
        * sum(
            (sum(values) - len(values) * mean) ** 2
            for values in groups.values()
        )
        / (len(trades) ** 2)
    )
    return mean / math.sqrt(variance) if variance > 0 else float("nan")


def _max_drawdown(trades: list[Trade]) -> float:
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda row: row.ts):
        equity += trade.pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _summarize(label: str, trades: list[Trade]) -> dict:
    research = [trade for trade in trades if not trade.oos]
    holdout = [trade for trade in trades if trade.oos]
    days = max((max((trade.ts for trade in trades), default=0)
                - min((trade.ts for trade in trades), default=0)) / 86400, 1)
    result = {
        "label": label,
        "n": len(trades),
        "n_is": len(research),
        "n_oos": len(holdout),
        "ev": _mean_cents(trades),
        "is_ev": _mean_cents(research),
        "oos_ev": _mean_cents(holdout),
        "wr": (sum(trade.win for trade in trades) / len(trades)) if trades else float("nan"),
        "per_day": len(trades) / days,
        "day_t": _cluster_t(trades, lambda trade: trade.ts // 86400),
        "interval_t": _cluster_t(trades, lambda trade: trade.ts),
        "max_dd": _max_drawdown(trades),
        "total": sum(trade.pnl for trade in trades),
        "exit_rate": (sum(trade.exited for trade in trades) / len(trades))
        if trades else 0.0,
    }
    print(
        f"{label:44} n={result['n']:5d} "
        f"EV={result['ev']:+6.3f}c IS={result['is_ev']:+6.3f}c "
        f"OOS={result['oos_ev']:+6.3f}c day-t={result['day_t']:+5.2f} "
        f"$/d@1={result['total']/days:+6.3f} DD@1={result['max_dd']:+6.2f}"
    )
    return result


def stable_midband(
    markets: list[dict],
    band_low: float,
    band_high: float,
    flat_limit: float,
    rem_low: int,
) -> list[Trade]:
    trades = []
    for market in markets:
        seq = _sequence(market)
        for rem in range(8, rem_low - 1, -1):
            quote = seq.get(rem)
            if not quote:
                continue
            side, favorite_bid, favorite_ask, favorite_mid = quote
            if not band_low <= favorite_mid < band_high:
                continue
            if favorite_ask - favorite_bid > 0.01 + EPS:
                continue
            flips, observed, earliest = _path_state(seq, rem)
            prior_mid = _same_side_mid(seq, rem + 2, side)
            if flips or observed < 5 or earliest is None or earliest < 10:
                continue
            if prior_mid is None or abs(favorite_mid - prior_mid) > flat_limit:
                continue
            entry = _entry(market, rem, side, favorite_ask)
            if entry is None or entry > 0.99:
                continue
            win = market["result"] == side
            trades.append(
                Trade(
                    market["ticker"], market["series"], market["close_ts"],
                    entry, _hold_pnl(entry, win), win, rem, side
                )
            )
            break
    return sorted(trades, key=lambda trade: trade.ts)


def base_unbroken(markets: list[dict], rem_low: int = 6) -> list[dict]:
    signals = []
    for market in markets:
        seq = _sequence(market)
        for rem in range(8, rem_low - 1, -1):
            quote = seq.get(rem)
            if not quote:
                continue
            side, favorite_bid, favorite_ask, favorite_mid = quote
            if not 0.90 <= favorite_mid < 0.99:
                continue
            if favorite_ask - favorite_bid > 0.01 + EPS:
                continue
            flips, observed, earliest = _path_state(seq, rem)
            if flips or observed < 5 or earliest is None or earliest < 10:
                continue
            entry = _entry(market, rem, side, favorite_ask)
            if entry is None or entry > 0.99:
                continue
            signals.append(
                {
                    "market": market,
                    "seq": seq,
                    "rem": rem,
                    "side": side,
                    "signal_ask": favorite_ask,
                    "entry": entry,
                    "win": market["result"] == side,
                }
            )
            break
    return signals


def favorite_scalp(
    signals: list[dict],
    target: float,
    horizon: int,
    fallback: str,
) -> list[Trade]:
    trades = []
    for signal in signals:
        market, rem, side = signal["market"], signal["rem"], signal["side"]
        entry = signal["entry"]
        exit_bid = None
        last_bid = None
        for step, later_rem in enumerate(range(rem - 2, 0, -1), 1):
            quote = _side_quotes(market["by_rem"].get(later_rem) or {}, side)
            if not quote:
                continue
            last_bid = quote[0]
            if quote[0] >= entry + target:
                exit_bid = quote[0]
                break
            if step >= horizon:
                break
        win = signal["win"]
        if exit_bid is not None:
            pnl, exited = _exit_pnl(entry, exit_bid), True
        elif fallback == "time_exit" and last_bid is not None:
            pnl, exit_bid, exited = _exit_pnl(entry, last_bid), last_bid, True
        else:
            pnl, exited = _hold_pnl(entry, win), False
        trades.append(
            Trade(
                market["ticker"], market["series"], market["close_ts"],
                entry, pnl, win, rem, side, exit_bid, exited
            )
        )
    return sorted(trades, key=lambda trade: trade.ts)


def longshot_scalp(
    markets: list[dict],
    favorite_low: float,
    state: str,
    target: float,
    horizon: int,
) -> list[Trade]:
    trades = []
    for market in markets:
        seq = _sequence(market)
        for rem in range(8, 5, -1):
            quote = seq.get(rem)
            if not quote:
                continue
            favorite_side, favorite_bid, favorite_ask, favorite_mid = quote
            if not favorite_low <= favorite_mid < 0.99:
                continue
            if favorite_ask - favorite_bid > 0.01 + EPS:
                continue
            flips, observed, earliest = _path_state(seq, rem)
            prior = _same_side_mid(seq, rem + 1, favorite_side)
            jump = favorite_mid - prior if prior is not None else 0.0
            if state == "unbroken" and (flips or observed < 5 or (earliest or 0) < 10):
                continue
            if state == "fresh_flip" and not (flips and observed >= 5):
                continue
            if state == "accelerating" and not (flips == 0 and jump >= 0.05):
                continue
            side = "no" if favorite_side == "yes" else "yes"
            signal_ask = 1 - favorite_bid
            entry = _entry(market, rem, side, signal_ask)
            if entry is None or entry >= 0.20:
                continue
            exit_bid = last_bid = None
            for step, later_rem in enumerate(range(rem - 2, 0, -1), 1):
                side_quote = _side_quotes(market["by_rem"].get(later_rem) or {}, side)
                if not side_quote:
                    continue
                last_bid = side_quote[0]
                if last_bid >= entry + target:
                    exit_bid = last_bid
                    break
                if step >= horizon:
                    break
            if exit_bid is None:
                if last_bid is None:
                    continue
                exit_bid = last_bid
            win = market["result"] == side
            trades.append(
                Trade(
                    market["ticker"], market["series"], market["close_ts"],
                    entry, _exit_pnl(entry, exit_bid), win, rem, side,
                    exit_bid, True
                )
            )
            break
    return sorted(trades, key=lambda trade: trade.ts)


def delayed_confirmation(
    signals: list[dict],
    wait_minutes: int,
    max_price_increase: float,
) -> list[Trade]:
    trades = []
    for signal in signals:
        market, seq = signal["market"], signal["seq"]
        original_rem, side = signal["rem"], signal["side"]
        decision_rem = original_rem - wait_minutes
        if decision_rem < 2:
            continue
        quote = seq.get(decision_rem)
        if not quote or quote[0] != side:
            continue
        flips, observed, earliest = _path_state(seq, decision_rem)
        if flips or observed < 5 or earliest is None or earliest < 10:
            continue
        if quote[2] > signal["entry"] + max_price_increase:
            continue
        entry = _entry(market, decision_rem, side, quote[2])
        if entry is None or entry > 0.99:
            continue
        win = market["result"] == side
        trades.append(
            Trade(
                market["ticker"], market["series"], market["close_ts"],
                entry, _hold_pnl(entry, win), win, decision_rem, side
            )
        )
    return sorted(trades, key=lambda trade: trade.ts)


def pullback_entry(
    signals: list[dict],
    pullback: float,
    wait_minutes: int,
) -> list[Trade]:
    trades = []
    for signal in signals:
        market, seq = signal["market"], signal["seq"]
        original_rem, side = signal["rem"], signal["side"]
        entered = None
        for decision_rem in range(original_rem - 1,
                                  max(original_rem - wait_minutes - 1, 1), -1):
            quote = seq.get(decision_rem)
            if not quote or quote[0] != side:
                break
            if quote[2] > signal["signal_ask"] - pullback:
                continue
            entry = _entry(market, decision_rem, side, quote[2])
            if entry is not None and entry <= 0.99:
                entered = (decision_rem, entry)
                break
        if entered is None:
            continue
        rem, entry = entered
        win = market["result"] == side
        trades.append(
            Trade(
                market["ticker"], market["series"], market["close_ts"],
                entry, _hold_pnl(entry, win), win, rem, side
            )
        )
    return sorted(trades, key=lambda trade: trade.ts)


def cross_asset_rows(markets: list[dict], rem: int) -> list[dict]:
    grouped: dict[int, dict[str, dict]] = defaultdict(dict)
    for market in markets:
        grouped[market["close_ts"]][market["series"]] = market
    rows = []
    asset_index = {series: idx for idx, series in enumerate(bt.SERIES)}
    for close_ts, group in grouped.items():
        if len(group) != len(bt.SERIES):
            continue
        snapshots = {}
        valid = True
        for series, market in group.items():
            candle = market["by_rem"].get(rem)
            prior = market["by_rem"].get(rem + 2)
            nxt = market["by_rem"].get(rem - 1)
            if not candle or not prior or not nxt:
                valid = False
                break
            try:
                bid, ask = float(candle["bc"]), float(candle["ac"])
                prior_mid = (float(prior["bc"]) + float(prior["ac"])) / 2
                next_bid, next_ask = float(nxt["bo"]), float(nxt["ao"])
            except (TypeError, ValueError, KeyError):
                valid = False
                break
            if not 0 < bid <= ask < 1 or ask - bid > 0.02 + EPS:
                valid = False
                break
            snapshots[series] = {
                "market": market,
                "mid": (bid + ask) / 2,
                "bid": bid,
                "ask": ask,
                "next_bid": next_bid,
                "next_ask": next_ask,
                "drift2": (bid + ask) / 2 - prior_mid,
            }
        if not valid:
            continue
        for series, snap in snapshots.items():
            own_mid = min(max(snap["mid"], 0.005), 0.995)
            other_mids = [row["mid"] for name, row in snapshots.items() if name != series]
            features = [
                math.log(own_mid / (1 - own_mid)),
                statistics.mean(math.log(min(max(mid, 0.005), 0.995)
                                         / (1 - min(max(mid, 0.005), 0.995)))
                                for mid in other_mids),
                snap["drift2"],
                statistics.pstdev(other_mids),
            ]
            features.extend(1.0 if asset_index[series] == idx else 0.0
                            for idx in range(len(bt.SERIES) - 1))
            rows.append(
                {
                    "ticker": snap["market"]["ticker"],
                    "series": series,
                    "ts": close_ts,
                    "features": features,
                    "mid": own_mid,
                    "yes_bid": snap["bid"],
                    "yes_ask": max(snap["ask"], snap["next_ask"]),
                    "no_bid": 1 - snap["ask"],
                    "no_ask": max(1 - snap["bid"], 1 - snap["next_bid"]),
                    "yes": snap["market"]["result"] == "yes",
                    "rem": rem,
                }
            )
    return rows


def cross_asset_model(markets: list[dict], rem: int, margin: float) -> tuple[list[Trade], dict]:
    rows = cross_asset_rows(markets, rem)
    research = [row for row in rows if row["ts"] < OOS_TS]
    holdout = [row for row in rows if row["ts"] >= OOS_TS]
    x_train = np.asarray([row["features"] for row in research])
    y_train = np.asarray([row["yes"] for row in research], dtype=int)
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.25, max_iter=2000, random_state=7),
    )
    model.fit(x_train, y_train)
    trades = []
    probabilities = model.predict_proba(np.asarray([row["features"] for row in holdout]))[:, 1]
    for row, probability in zip(holdout, probabilities):
        yes_edge = probability - row["yes_ask"] - bt.fee(row["yes_ask"])
        no_probability = 1 - probability
        no_edge = no_probability - row["no_ask"] - bt.fee(row["no_ask"])
        if max(yes_edge, no_edge) <= margin:
            continue
        side = "yes" if yes_edge >= no_edge else "no"
        entry = row["yes_ask"] if side == "yes" else row["no_ask"]
        if entry > 0.99:
            continue
        win = row["yes"] == (side == "yes")
        trades.append(
            Trade(
                row["ticker"], row["series"], row["ts"], entry,
                _hold_pnl(entry, win), win, rem, side
            )
        )
    holdout_y = np.asarray([row["yes"] for row in holdout], dtype=int)
    holdout_mid = np.asarray([row["mid"] for row in holdout])
    metrics = {
        "brier_market": brier_score_loss(holdout_y, holdout_mid),
        "brier_model": brier_score_loss(holdout_y, probabilities),
        "logloss_market": log_loss(holdout_y, holdout_mid),
        "logloss_model": log_loss(holdout_y, probabilities),
        "train_n": len(research),
        "test_n": len(holdout),
    }
    return sorted(trades, key=lambda trade: trade.ts), metrics


def choose_by_research(results: list[tuple[dict, list[Trade]]], minimum: int = 100):
    eligible = []
    for params, trades in results:
        research = [trade for trade in trades if not trade.oos]
        if len(research) >= minimum:
            eligible.append((statistics.mean(trade.pnl for trade in research), params, trades))
    return max(eligible, default=None, key=lambda row: row[0])


def main() -> None:
    markets = []
    for series in bt.SERIES:
        markets.extend(bt.load(series))
    markets.sort(key=lambda market: market["close_ts"])
    print(f"Loaded {len(markets)} settled markets")

    print("\n=== 1. Stable mid-band favorite states ===")
    midband_results = []
    for band_low, band_high in ((0.70, 0.80), (0.80, 0.85), (0.85, 0.90),
                                (0.75, 0.90), (0.80, 0.90)):
        for flat_limit in (0.005, 0.01, 0.02):
            for rem_low in (4, 6):
                params = {
                    "band": (band_low, band_high),
                    "flat": flat_limit,
                    "rem_low": rem_low,
                }
                trades = stable_midband(markets, band_low, band_high, flat_limit, rem_low)
                midband_results.append((params, trades))
    selected = choose_by_research(midband_results)
    if selected:
        _, params, trades = selected
        _summarize(f"IS-selected midband {params}", trades)
    print("Top holdout-positive stable cells:")
    ranked = sorted(
        midband_results,
        key=lambda row: _mean_cents([trade for trade in row[1] if trade.oos]),
        reverse=True,
    )
    for params, trades in ranked[:8]:
        _summarize(str(params), trades)

    print("\n=== 2. Relative-target favorite scalps ===")
    signals = base_unbroken(markets, rem_low=6)
    baseline = [
        Trade(
            signal["market"]["ticker"], signal["market"]["series"],
            signal["market"]["close_ts"], signal["entry"],
            _hold_pnl(signal["entry"], signal["win"]), signal["win"],
            signal["rem"], signal["side"]
        )
        for signal in signals
    ]
    _summarize("R1 rem6-8 hold baseline", baseline)
    scalp_results = []
    for target in (0.01, 0.02, 0.03, 0.05):
        for horizon in (1, 2, 3, 6):
            for fallback in ("hold", "time_exit"):
                params = {"target": target, "horizon": horizon, "fallback": fallback}
                scalp_results.append((params, favorite_scalp(signals, target, horizon, fallback)))
    scalp_selected = choose_by_research(scalp_results)
    if scalp_selected:
        _, params, trades = scalp_selected
        _summarize(f"IS-selected favorite scalp {params}", trades)
    print("Best target variants by holdout delta:")
    baseline_oos = _mean_cents([trade for trade in baseline if trade.oos])
    for params, trades in sorted(
        scalp_results,
        key=lambda row: _mean_cents([trade for trade in row[1] if trade.oos])
        - baseline_oos,
        reverse=True,
    )[:6]:
        summary = _summarize(str(params), trades)
        print(f"  holdout delta vs hold: {summary['oos_ev'] - baseline_oos:+.3f}c")

    print("\n=== 3. Longshot retracement scalps ===")
    longshot_results = []
    for favorite_low in (0.90, 0.93, 0.95, 0.97):
        for state in ("unbroken", "fresh_flip", "accelerating"):
            for target in (0.02, 0.03, 0.05, 0.10):
                for horizon in (1, 2, 3):
                    params = {
                        "favorite_low": favorite_low,
                        "state": state,
                        "target": target,
                        "horizon": horizon,
                    }
                    trades = longshot_scalp(
                        markets, favorite_low, state, target, horizon
                    )
                    longshot_results.append((params, trades))
    longshot_selected = choose_by_research(longshot_results)
    if longshot_selected:
        _, params, trades = longshot_selected
        _summarize(f"IS-selected longshot scalp {params}", trades)
    print("Best holdout longshot cells:")
    for params, trades in sorted(
        longshot_results,
        key=lambda row: _mean_cents([trade for trade in row[1] if trade.oos]),
        reverse=True,
    )[:8]:
        _summarize(str(params), trades)

    print("\n=== 4. Delayed confirmation and pullback entries ===")
    delayed_results = []
    for wait in (1, 2):
        for max_increase in (0.00, 0.01, 0.02, 0.05):
            params = {"wait": wait, "max_increase": max_increase}
            delayed_results.append(
                (params, delayed_confirmation(signals, wait, max_increase))
            )
    delayed_selected = choose_by_research(delayed_results)
    if delayed_selected:
        _, params, trades = delayed_selected
        _summarize(f"IS-selected delayed {params}", trades)
        print(
            f"  capture={len(trades)/len(signals):.1%}; expected per original signal="
            f"{sum(trade.pnl for trade in trades)/len(signals)*100:+.3f}c"
        )
    for params, trades in sorted(
        delayed_results,
        key=lambda row: sum(trade.pnl for trade in row[1] if trade.oos)
        / max(sum(signal["market"]["close_ts"] >= OOS_TS for signal in signals), 1),
        reverse=True,
    )[:5]:
        summary = _summarize(str(params), trades)
        print(f"  capture={len(trades)/len(signals):.1%}")

    pullback_results = []
    for pullback in (0.005, 0.01, 0.02, 0.03):
        for wait in (1, 2, 3):
            params = {"pullback": pullback, "wait": wait}
            pullback_results.append((params, pullback_entry(signals, pullback, wait)))
    pullback_selected = choose_by_research(pullback_results)
    if pullback_selected:
        _, params, trades = pullback_selected
        _summarize(f"IS-selected pullback {params}", trades)
        print(
            f"  capture={len(trades)/len(signals):.1%}; expected per original signal="
            f"{sum(trade.pnl for trade in trades)/len(signals)*100:+.3f}c"
        )

    print("\n=== 5. Cross-asset residual-pricing equation ===")
    for rem in (4, 6, 8):
        for margin in (0.00, 0.01, 0.02, 0.03, 0.05):
            trades, metrics = cross_asset_model(markets, rem, margin)
            summary = _summarize(f"cross-asset rem={rem} margin={margin:.2f}", trades)
            print(
                f"  model Brier {metrics['brier_model']:.5f} vs market "
                f"{metrics['brier_market']:.5f}; logloss {metrics['logloss_model']:.5f} "
                f"vs {metrics['logloss_market']:.5f}; holdout-only EV "
                f"{summary['ev']:+.3f}c"
            )


if __name__ == "__main__":
    main()
