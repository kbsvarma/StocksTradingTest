#!/usr/bin/env python3
"""Run the frozen daily volatility/direction matrix on a PIT panel."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
SELECTORS = ("ATR20", "RV20", "VOL_SPIKE", "SHOCK")
DIRECTIONS = (
    "ALWAYS_LONG",
    "ALWAYS_SHORT",
    "MR1",
    "MR5",
    "MOM5",
    "MOM20",
    "PRICE_MA50",
    "PRICE_MA200",
    "MA50_MA200",
    "BREAKOUT20",
    "TREND_VOTE",
    "REGIME_HYBRID",
)
HOLDS = (1, 2, 5)
BOOK_NOTIONAL = 10_000.0
COMMISSION_PER_SHARE = 0.005


def signed(values: pd.Series, fallback: pd.Series | float = 1.0) -> pd.Series:
    result = np.sign(values).astype(float)
    if isinstance(fallback, pd.Series):
        result = result.where(result != 0, np.sign(fallback).replace(0, 1.0))
    else:
        result = result.where(result != 0, fallback)
    return result


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.sort_values(["raw_symbol", "history_segment", "day"]).copy()

    def per_symbol(group: pd.DataFrame) -> pd.DataFrame:
        group = group.copy()
        close = group["adj_close"]
        previous = close.shift(1)
        true_range = pd.concat(
            [
                group["adj_high"] - group["adj_low"],
                (group["adj_high"] - previous).abs(),
                (group["adj_low"] - previous).abs(),
            ],
            axis=1,
        ).max(axis=1)
        returns = group["clean_return"]
        group["ATR20"] = true_range.rolling(20, min_periods=20).mean() / close
        group["RV20"] = returns.rolling(20, min_periods=20).std(ddof=1)
        group["VOL_SPIKE"] = (
            group["volume"] / group["volume"].rolling(20, min_periods=20).mean()
        )
        group["ret1"] = returns
        group["ret5"] = close / close.shift(5) - 1.0
        group["ret20"] = close / close.shift(20) - 1.0
        group["gap"] = group["adj_open"] / previous - 1.0
        group["session_return"] = close / group["adj_open"] - 1.0
        group["ma50"] = close.rolling(50, min_periods=50).mean()
        group["ma200"] = close.rolling(200, min_periods=200).mean()
        group["roll_high20"] = group["adj_high"].rolling(20, min_periods=20).max()
        group["roll_low20"] = group["adj_low"].rolling(20, min_periods=20).min()
        group["roll_high252"] = group["adj_high"].rolling(
            252, min_periods=200
        ).max()
        group["price_to_high252"] = close / group["roll_high252"]
        return group

    featured = (
        panel.groupby(["raw_symbol", "history_segment"], group_keys=True)
        .apply(per_symbol, include_groups=False)
        .reset_index(drop=False)
    )
    return featured.drop(
        columns=[column for column in featured if column.startswith("level_")],
        errors="ignore",
    )


def direction_columns(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["ALWAYS_LONG"] = 1.0
    panel["ALWAYS_SHORT"] = -1.0
    panel["MR1"] = -signed(panel["ret1"], panel["ret20"])
    panel["MR5"] = -signed(panel["ret5"], panel["ret20"])
    panel["MOM5"] = signed(panel["ret5"], panel["ret20"])
    panel["MOM20"] = signed(panel["ret20"])
    panel["PRICE_MA50"] = signed(panel["adj_close"] / panel["ma50"] - 1.0)
    panel["PRICE_MA200"] = signed(panel["adj_close"] / panel["ma200"] - 1.0)
    panel["MA50_MA200"] = signed(panel["ma50"] / panel["ma200"] - 1.0)
    range_midpoint = (panel["roll_high20"] + panel["roll_low20"]) / 2.0
    panel["BREAKOUT20"] = signed(
        panel["adj_close"] / range_midpoint - 1.0, panel["ret20"]
    )
    vote = (
        panel["MOM5"]
        + panel["MOM20"]
        + panel["PRICE_MA50"]
        + panel["MA50_MA200"]
    )
    panel["TREND_VOTE"] = signed(vote, panel["MOM20"])
    aligned = panel["PRICE_MA50"] == panel["MA50_MA200"]
    panel["REGIME_HYBRID"] = panel["MR1"]
    panel.loc[aligned, "REGIME_HYBRID"] = panel.loc[aligned, "PRICE_MA50"]
    return panel


def selected_rows(
    panel: pd.DataFrame, universe: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    panel = panel.copy()
    panel["month"] = panel["day"].str[:7]
    merged = panel.merge(
        universe[["month", "symbol", "rank", "mega"]],
        left_on=["month", "raw_symbol"],
        right_on=["month", "symbol"],
        how="inner",
    )
    required = list(SELECTORS[:-1]) + [
        "ret1",
        "ret5",
        "ret20",
        "ma50",
        "ma200",
        "roll_high20",
        "roll_low20",
    ]
    eligible = merged.dropna(subset=required).copy()
    ranks = [
        eligible.groupby("day")[column].rank(pct=True)
        for column in ("ATR20", "RV20", "VOL_SPIKE")
    ]
    ranks.append(
        eligible.groupby("day")["ret5"].transform(lambda value: value.abs().rank(pct=True))
    )
    eligible["SHOCK"] = sum(ranks)
    selections = {}
    for selector in SELECTORS:
        index = eligible.groupby("day")[selector].idxmax()
        selections[selector] = eligible.loc[index].sort_values("day").copy()
    all_days = pd.DatetimeIndex(sorted(pd.to_datetime(panel["day"].unique())))
    return selections, all_days


def nw_t_stat(values: pd.Series, lag: int) -> float:
    array = np.asarray(values.dropna(), dtype=float)
    count = len(array)
    if count < 3:
        return np.nan
    demeaned = array - array.mean()
    variance = float(np.dot(demeaned, demeaned) / count)
    for current_lag in range(1, min(lag, count - 1) + 1):
        covariance = float(
            np.dot(demeaned[current_lag:], demeaned[:-current_lag]) / count
        )
        variance += 2.0 * (1.0 - current_lag / (lag + 1.0)) * covariance
    standard_error = math.sqrt(max(variance, 0.0) / count)
    return array.mean() / standard_error if standard_error > 0 else np.nan


def bh_qvalues(pvalues: pd.Series) -> pd.Series:
    values = pvalues.astype(float)
    order = np.argsort(values.to_numpy())
    sorted_values = values.to_numpy()[order]
    count = len(sorted_values)
    adjusted = sorted_values * count / np.arange(1, count + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    output = np.empty(count)
    output[order] = np.minimum(adjusted, 1.0)
    return pd.Series(output, index=values.index)


def simulate(
    selection: pd.DataFrame,
    lookup: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    selector: str,
    direction_name: str,
    hold: int,
) -> pd.DataFrame:
    calendar_positions = {day.strftime("%Y-%m-%d"): index for index, day in enumerate(calendar)}
    rows = []
    for signal in selection.itertuples():
        signal_day = str(signal.day)
        position = calendar_positions.get(signal_day)
        if position is None or position + hold >= len(calendar):
            continue
        entry_day = calendar[position + 1].strftime("%Y-%m-%d")
        exit_day = calendar[position + hold].strftime("%Y-%m-%d")
        key_entry = (entry_day, signal.raw_symbol)
        key_exit = (exit_day, signal.raw_symbol)
        if key_entry not in lookup.index or key_exit not in lookup.index:
            continue
        entry_row = lookup.loc[key_entry]
        exit_row = lookup.loc[key_exit]
        if isinstance(entry_row, pd.DataFrame):
            entry_row = entry_row.iloc[-1]
        if isinstance(exit_row, pd.DataFrame):
            exit_row = exit_row.iloc[-1]
        entry = float(entry_row["adj_open"])
        exit_price = float(exit_row["adj_close"])
        raw_entry = float(entry_row["open"])
        raw_exit = float(exit_row["close"])
        direction = float(getattr(signal, direction_name))
        if not all(
            np.isfinite(value) and value > 0
            for value in (entry, exit_price, raw_entry, raw_exit)
        ):
            continue
        half_spread = 0.0003 if bool(signal.mega) else 0.0005
        entry_scale = entry / raw_entry
        exit_scale = exit_price / raw_exit
        exit_quantity_multiplier = exit_scale / entry_scale
        commission_return = (
            COMMISSION_PER_SHARE
            * (1.0 + exit_quantity_multiplier)
            / raw_entry
        )
        calendar_days = max(
            1, (pd.Timestamp(exit_day) - pd.Timestamp(entry_day)).days
        )
        borrow = 0.0025 * calendar_days / 365.0 if direction < 0 else 0.0
        borrow_stress = 0.25 * calendar_days / 365.0 if direction < 0 else 0.0
        if direction > 0:
            gross_return = exit_price / entry - 1.0
            net_return = (
                exit_price * (1.0 - half_spread)
                - entry * (1.0 + half_spread)
            ) / entry - commission_return
            stress_return = (
                exit_price * (1.0 - 2.0 * half_spread)
                - entry * (1.0 + 2.0 * half_spread)
            ) / entry - 2.0 * commission_return
        else:
            gross_return = 1.0 - exit_price / entry
            net_return = (
                entry * (1.0 - half_spread)
                - exit_price * (1.0 + half_spread)
            ) / entry - commission_return - borrow
            stress_return = (
                entry * (1.0 - 2.0 * half_spread)
                - exit_price * (1.0 + 2.0 * half_spread)
            ) / entry - 2.0 * commission_return - borrow_stress
        ticket = BOOK_NOTIONAL / hold
        rows.append(
            {
                "selector": selector,
                "direction_rule": direction_name,
                "hold": hold,
                "signal_day": signal_day,
                "entry_day": entry_day,
                "exit_day": exit_day,
                "symbol": signal.raw_symbol,
                "side": "LONG" if direction > 0 else "SHORT",
                "entry": entry,
                "exit": exit_price,
                "raw_entry": raw_entry,
                "raw_exit": raw_exit,
                "exit_quantity_multiplier": exit_quantity_multiplier,
                "gross_return": gross_return,
                "net_return": net_return,
                "stress_return": stress_return,
                "notional": ticket,
                "gross_pnl": ticket * gross_return,
                "net_pnl": ticket * net_return,
                "stress_pnl": ticket * stress_return,
            }
        )
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame, eligible_days: int) -> dict:
    pnl = trades["net_pnl"]
    hold = int(trades["hold"].iloc[0])
    development = trades[trades["signal_day"] < "2023-01-01"]
    holdout = trades[trades["signal_day"] >= "2023-01-01"]
    cumulative = pnl.cumsum()
    drawdown = cumulative - cumulative.cummax().clip(lower=0.0)
    yearly = trades.assign(year=trades["signal_day"].str[:4]).groupby("year")[
        "net_pnl"
    ].sum()
    positive = pnl[pnl > 0]
    ticker_positive = (
        trades.loc[trades["net_pnl"] > 0]
        .groupby("symbol")["net_pnl"]
        .sum()
    )
    concentration = (
        ticker_positive.max() / ticker_positive.sum()
        if ticker_positive.sum() > 0
        else np.nan
    )
    t_holdout = nw_t_stat(holdout["net_pnl"], hold)
    one_sided_p = (
        float(0.5 * math.erfc(t_holdout / math.sqrt(2.0)))
        if np.isfinite(t_holdout)
        else 1.0
    )
    return {
        "selector": trades["selector"].iloc[0],
        "direction_rule": trades["direction_rule"].iloc[0],
        "hold": hold,
        "n": len(trades),
        "coverage": len(trades) / eligible_days if eligible_days else 0.0,
        "long_rate": (trades["side"] == "LONG").mean(),
        "mean_pnl": pnl.mean(),
        "median_pnl": pnl.median(),
        "total_pnl": pnl.sum(),
        "gross_total_pnl": trades["gross_pnl"].sum(),
        "stress_total_pnl": trades["stress_pnl"].sum(),
        "development_total": development["net_pnl"].sum(),
        "development_mean": development["net_pnl"].mean(),
        "holdout_total": holdout["net_pnl"].sum(),
        "holdout_mean": holdout["net_pnl"].mean(),
        "holdout_t": t_holdout,
        "holdout_p": one_sided_p,
        "win_rate": (pnl > 0).mean(),
        "max_drawdown": drawdown.min(),
        "worst_trade": pnl.min(),
        "positive_years": int((yearly > 0).sum()),
        "year_count": len(yearly),
        "key_years_negative": int(
            sum(yearly.get(str(year), 0.0) < 0 for year in (2020, 2022, 2025, 2026))
        ),
        "max_ticker_positive_contribution": concentration,
        "top10_trade_positive_contribution": (
            positive.nlargest(10).sum() / positive.sum()
            if positive.sum() > 0
            else np.nan
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label", help="dataset label, e.g. 2018_2026")
    args = parser.parse_args()
    panel = pd.read_parquet(DATA / f"pit_panel_{args.label}.parquet")
    universe = pd.read_parquet(DATA / f"pit_universe_{args.label}.parquet")
    print(
        f"loaded panel={len(panel):,} universe={len(universe):,}",
        flush=True,
    )
    feature_panel = direction_columns(add_features(panel))
    selections, calendar = selected_rows(feature_panel, universe)
    lookup = feature_panel.set_index(["day", "raw_symbol"])
    print(
        "selection days: "
        + ", ".join(f"{key}={len(value)}" for key, value in selections.items()),
        flush=True,
    )

    ledgers = []
    summaries = []
    total_cells = len(SELECTORS) * len(DIRECTIONS) * len(HOLDS)
    completed = 0
    for selector, selection in selections.items():
        for direction in DIRECTIONS:
            for hold in HOLDS:
                trades = simulate(
                    selection,
                    lookup,
                    calendar,
                    selector,
                    direction,
                    hold,
                )
                if trades.empty:
                    continue
                ledgers.append(trades)
                summaries.append(summarize(trades, len(selection)))
                completed += 1
                if completed % 24 == 0:
                    print(f"completed {completed}/{total_cells} cells", flush=True)
    all_trades = pd.concat(ledgers, ignore_index=True)
    summary = pd.DataFrame(summaries)
    summary["holdout_q"] = bh_qvalues(summary["holdout_p"])

    benchmark = summary[
        summary["direction_rule"].isin(["ALWAYS_LONG", "ALWAYS_SHORT"])
    ].set_index(["selector", "direction_rule", "hold"])["total_pnl"]
    beats_benchmarks = []
    for row in summary.itertuples():
        long_benchmark = benchmark.get((row.selector, "ALWAYS_LONG", row.hold), np.inf)
        short_benchmark = benchmark.get((row.selector, "ALWAYS_SHORT", row.hold), np.inf)
        beats_benchmarks.append(
            row.total_pnl > long_benchmark and row.total_pnl > short_benchmark
        )
    summary["beats_both_fixed_sides"] = beats_benchmarks
    summary["passes_all_gates"] = (
        (summary["coverage"] >= 0.95)
        & (summary["development_total"] > 0)
        & (summary["development_mean"] > 0)
        & (summary["holdout_total"] > 0)
        & (summary["holdout_mean"] > 0)
        & (summary["holdout_t"] >= 2.0)
        & (summary["stress_total_pnl"] > 0)
        & (summary["positive_years"] >= 6)
        & (summary["key_years_negative"] <= 1)
        & (summary["max_ticker_positive_contribution"] <= 0.20)
        & (summary["holdout_q"] <= 0.10)
        & summary["beats_both_fixed_sides"]
    )
    summary = summary.sort_values("total_pnl", ascending=False)
    all_trades.to_csv(BASE / f"trades_{args.label}.csv", index=False)
    summary.to_csv(BASE / f"summary_{args.label}.csv", index=False)
    yearly = (
        all_trades.assign(year=all_trades["signal_day"].str[:4])
        .groupby(["selector", "direction_rule", "hold", "year"])["net_pnl"]
        .sum()
        .reset_index()
    )
    yearly.to_csv(BASE / f"yearly_{args.label}.csv", index=False)
    print(summary.head(25).to_string(index=False))
    print(
        f"\nexecuted={len(summary)} cells "
        f"survivors={int(summary.passes_all_gates.sum())}"
    )


if __name__ == "__main__":
    main()
