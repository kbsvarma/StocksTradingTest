#!/usr/bin/env python3
"""Causal loss-regime diagnosis for the BTC unbroken-favorite control.

All thresholds are estimated before the fresh holdout. The final holdout is
also split in time so an apparent rescue confined to one short episode is
visible rather than averaged away.
"""

from __future__ import annotations

import math
import statistics
import time

import study


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    return ordered[int(probability * (len(ordered) - 1))]


def feature_rows(markets: list[study.Market]) -> list[dict]:
    perps = study.load_perp()
    market_by_ticker = {market.ticker: market for market in markets}
    controls = study.unbroken_strategies(markets)["control_unbroken_90_99"]
    expirations = sorted(
        (market.close_ts, market.expiration)
        for market in markets if market.expiration is not None
    )
    rows = []
    prior_control_losses = []
    for trade in controls:
        market = market_by_ticker[trade.ticker]
        decision_ts = trade.close_ts - trade.signal_rem * 60
        index = study.previous_value(perps, decision_ts)
        index_3 = study.previous_value(perps, decision_ts - 180)
        sigma = study.robust_sigma(perps, decision_ts)
        path = study.path_state(market, trade.signal_rem, trade.side)
        direction = 1 if trade.side == "yes" else -1
        z = None
        signed_trend_3 = None
        sigma_bps = None
        if index is not None and market.strike and sigma:
            z = direction * math.log(index / market.strike) / (
                sigma * math.sqrt(trade.signal_rem + 0.5)
            )
            sigma_bps = sigma * 10_000
        if index is not None and index_3 is not None:
            signed_trend_3 = direction * math.log(index / index_3) * 10_000

        prior_values = [
            value for timestamp, value in expirations
            if trade.close_ts - 3600 <= timestamp < trade.close_ts
        ]
        prior_returns = [
            abs(math.log(right / left)) * 10_000
            for left, right in zip(prior_values, prior_values[1:])
            if left and right
        ]
        realized_hour_vol = sum(prior_returns)
        recent_loss = any(
            trade.close_ts - 3600 <= timestamp < trade.close_ts
            for timestamp in prior_control_losses
        )
        row = {
            "trade": trade,
            "entry": trade.entry,
            "rem": trade.signal_rem,
            "side_yes": trade.side == "yes",
            "z": z,
            "signed_trend_3": signed_trend_3,
            "sigma_bps": sigma_bps,
            "hour_vol": realized_hour_vol,
            "recent_loss": recent_loss,
            "momentum_3": path["momentum_3"],
            "largest_drop": path["largest_drop"],
        }
        rows.append(row)
        if trade.pnl < 0:
            prior_control_losses.append(trade.close_ts)
    return rows


def trades(rows: list[dict], predicate) -> list[study.Trade]:
    return [row["trade"] for row in rows if predicate(row)]


def print_segments(label: str, selected: list[study.Trade], fresh_split: int) -> None:
    print(f"\n{label}")
    print(study.summarize("validation", study.segment(selected, study.VALIDATION_START, study.FRESH_START)))
    print(study.summarize("fresh all", study.segment(selected, study.FRESH_START)))
    print(study.summarize("fresh first half", study.segment(selected, study.FRESH_START, fresh_split)))
    print(study.summarize("fresh final half", study.segment(selected, fresh_split)))


def metric_table(rows: list[dict], feature: str, thresholds: list[float], fresh_split: int) -> None:
    print(f"\n--- {feature} fixed threshold table ---")
    for threshold in thresholds:
        selected = trades(
            rows,
            lambda row, key=feature, value=threshold: row[key] is not None and row[key] >= value,
        )
        validation = study.segment(selected, study.VALIDATION_START, study.FRESH_START)
        fresh_1 = study.segment(selected, study.FRESH_START, fresh_split)
        fresh_2 = study.segment(selected, fresh_split)
        def compact(part: list[study.Trade]) -> str:
            if not part:
                return "n=0"
            return f"n={len(part)} ev25={100*statistics.mean(t.pnl for t in part)-0.25:+.2f}c"
        print(
            f">={threshold:7.3f}  val {compact(validation):20} "
            f"fresh1 {compact(fresh_1):20} fresh2 {compact(fresh_2):20}"
        )


def exploratory_surface(markets: list[study.Market], fresh_split: int) -> None:
    """Broad state table for diagnosis only, never used as a promotion test."""
    print("\n=== exploratory fresh state surface (not eligible for selection) ===")
    cells = []
    for low, high in zip(study.PRICE_BUCKETS, study.PRICE_BUCKETS[1:]):
        if low < 0.80:
            continue
        for remainder in (8, 7, 6, 5, 4, 3, 2):
            selected = []
            for market in markets:
                quote = study.favorite_quote(market.by_rem.get(remainder) or {})
                if quote is None:
                    continue
                side, bid, ask, mid = quote
                if not low <= mid < high or ask - bid > 0.01 + study.EPSILON:
                    continue
                entry = study.executable_entry(market, remainder, side)
                if entry is None:
                    continue
                selected.append(
                    study.Trade(
                        "surface", market.ticker, market.close_ts, side, entry,
                        study.settlement_pnl(entry, market.result == side), remainder,
                    )
                )
            first = study.segment(selected, study.FRESH_START, fresh_split)
            second = study.segment(selected, fresh_split)
            if len(first) < 20 or len(second) < 20:
                continue
            ev1 = 100 * statistics.mean(t.pnl for t in first) - 0.25
            ev2 = 100 * statistics.mean(t.pnl for t in second) - 0.25
            cells.append((min(ev1, ev2), low, high, remainder, len(first), ev1, len(second), ev2))
    for row in sorted(cells, reverse=True)[:20]:
        floor, low, high, rem, n1, ev1, n2, ev2 = row
        print(
            f"price={low:.2f}-{high:.2f} rem={rem} "
            f"H1 n={n1:3d} ev25={ev1:+6.2f}c H2 n={n2:3d} ev25={ev2:+6.2f}c "
            f"min={floor:+6.2f}c"
        )


def main() -> None:
    markets = study.load_markets()
    rows = feature_rows(markets)
    fresh_times = sorted({row["trade"].close_ts for row in rows if row["trade"].close_ts >= study.FRESH_START})
    fresh_split = fresh_times[len(fresh_times) // 2]
    training = [row for row in rows if study.VALIDATION_START <= row["trade"].close_ts < study.FRESH_START]
    print(
        f"rows={len(rows)}, fresh split={time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(fresh_split))}"
    )

    for feature in ("z", "signed_trend_3", "sigma_bps", "hour_vol"):
        values = [row[feature] for row in training if row[feature] is not None]
        print(
            f"{feature:16} train q25={quantile(values, .25):.3f} "
            f"q50={quantile(values, .50):.3f} q75={quantile(values, .75):.3f}"
        )

    z_q25 = quantile([row["z"] for row in training if row["z"] is not None], 0.25)
    z_q50 = quantile([row["z"] for row in training if row["z"] is not None], 0.50)
    vol_q75 = quantile([row["hour_vol"] for row in training], 0.75)
    sigma_q75 = quantile([row["sigma_bps"] for row in training if row["sigma_bps"] is not None], 0.75)

    definitions = {
        "control": lambda row: True,
        "entry below 95c": lambda row: row["entry"] < 0.95,
        "independent z >= train q25": lambda row: row["z"] is not None and row["z"] >= z_q25,
        "independent z >= train median": lambda row: row["z"] is not None and row["z"] >= z_q50,
        "quiet hour <= train q75": lambda row: row["hour_vol"] <= vol_q75,
        "sigma <= train q75": lambda row: row["sigma_bps"] is not None and row["sigma_bps"] <= sigma_q75,
        "one-hour loss cooldown": lambda row: not row["recent_loss"],
        "z q25 + quiet + cooldown": lambda row: (
            row["z"] is not None and row["z"] >= z_q25
            and row["hour_vol"] <= vol_q75 and not row["recent_loss"]
        ),
    }
    for label, predicate in definitions.items():
        print_segments(label, trades(rows, predicate), fresh_split)

    metric_table(rows, "z", [-0.5, 0.0, 0.5, 1.0, 1.5, 2.0], fresh_split)
    metric_table(rows, "signed_trend_3", [-5, 0, 2, 5, 10], fresh_split)
    exploratory_surface(markets, fresh_split)


if __name__ == "__main__":
    main()
