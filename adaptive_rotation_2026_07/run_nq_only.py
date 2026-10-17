"""Pre-registered NQ-only comparison for the adaptive-rotation study.

Research-only: never imports broker or production state modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (D15, START, SYMS, allocation,
                                   baseline_policy, feature_library,
                                   current_horizon_returns,
                                   maximum_exposure, policy_quantity)  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402
from validate_finalists import (paired_bootstrap, simulate_with_global_cap)  # noqa: E402


def causal_rolling_z(series: pd.Series, window: int,
                     min_periods: int) -> pd.Series:
    """Standardize against strictly prior observations and clip outliers."""
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=min_periods).mean()
    std = prior.rolling(window, min_periods=min_periods).std(ddof=1)
    return series.sub(mean).div(std.replace(0.0, np.nan)).clip(-2.0, 2.0)


def nq_own_regime_policy(daily: dict[str, pd.Series], index: pd.DatetimeIndex,
                         live_r5: pd.Series) -> pd.Series:
    """Causal NQ-only two-speed regime sizing registered in the protocol."""
    mondays = pd.DatetimeIndex(sorted(set(index.map(
        lambda stamp: stamp.normalize()
        - pd.Timedelta(days=stamp.weekday())))))
    weekly_r35 = pd.Series(index=mondays, dtype=float)
    for monday in mondays:
        prior = daily["NQ"][daily["NQ"].index < monday]
        if len(prior) >= 35:
            weekly_r35.loc[monday] = prior.iloc[-1] / prior.iloc[-35] - 1.0
    slow_z = causal_rolling_z(weekly_r35, 156, 52)
    mapped_slow = slow_z.reindex(index.map(
        lambda stamp: stamp.normalize()
        - pd.Timedelta(days=stamp.weekday()))).set_axis(index)
    fast_z = causal_rolling_z(live_r5, 24 * 252, 24 * 126)
    score = mapped_slow + 2.0 * fast_z
    qty = pd.Series(1, index=index, dtype=int)
    valid = score.notna()
    qty.loc[valid & (score < -1.0)] = 0
    qty.loc[valid & (score >= -1.0) & (score < 0.0)] = 1
    qty.loc[valid & (score >= 0.0) & (score < 1.0)] = 2
    qty.loc[valid & (score >= 1.0)] = 3
    return qty


def trades_for_nq(quantity: pd.Series, bars: dict[str, pd.DataFrame]):
    return rg.simulate_weighted(
        bars["NQ"], "NQ",
        lambda stamp: int(quantity.asof(stamp)) if stamp >= quantity.index[0]
        else 1,
    ).assign(dir=1)


def pnl_and_stats(trades: pd.DataFrame, bars: dict[str, pd.DataFrame],
                  friction: float = 3.5):
    pnl = hourly_mtm(trades, bars, friction)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    yearly = pnl.groupby(pnl.index.year).sum()
    daily = pnl.groupby(pnl.index.normalize()).sum()
    daily_sd = daily.std(ddof=1)
    return pnl, {
        "net": float(pnl.sum()),
        "maxDD": float(drawdown.min()),
        "worst": float(yearly.min()),
        "worst_year": int(yearly.idxmin()),
        "sharpe": float(daily.mean() / daily_sd * np.sqrt(252))
        if daily_sd > 0 else np.nan,
        "p7": float(hourly_mtm(trades, bars, 7.0).sum()),
        "p14": float(hourly_mtm(trades, bars, 14.0).sum()),
        "trades": int(len(trades)),
        "contracts": int(trades["qty"].sum()),
        "max_core": maximum_exposure(trades),
        "y2011_15": float(pnl[pnl.index.year <= 2015].sum()),
        "y2016_20": float(pnl[(pnl.index.year >= 2016)
                               & (pnl.index.year <= 2020)].sum()),
        "y2021_end": float(pnl[pnl.index.year >= 2021].sum()),
        "ex2026": float(pnl[pnl.index.year < 2026].sum()),
    }


def run():
    rg.START, rg.SCFG = START, dict(D15)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    daily, index, scores = feature_library(bars)
    weekly = baseline_policy(daily, index)
    selected_score = scores["week35_R5_W2"]
    adaptive = allocation(selected_score, "graduated")

    nq_policies = {
        "nq_fixed_1": pd.Series(1, index=index, dtype=int),
        "nq_fixed_3": pd.Series(3, index=index, dtype=int),
        "nq_weekly_qty": weekly["NQ"].astype(int),
        "nq_relative_qty": adaptive["NQ"].astype(int),
        "nq_relative_any_3": adaptive["NQ"].gt(0).astype(int) * 3,
        "nq_relative_leader_3": selected_score.eq(
            selected_score.max(axis=1), axis=0)["NQ"].astype(int) * 3,
    }

    live_r5 = current_horizon_returns(bars, daily, index, (5,))[5]["NQ"]
    nq_policies["nq_own_regime"] = nq_own_regime_policy(
        daily, index, live_r5)

    original = strategy.signal_frame
    cached = {id(frame): original(frame, rg.SCFG) for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    stats, pnls, trade_sets = {}, {}, {}
    try:
        for name, quantity in nq_policies.items():
            trades = trades_for_nq(quantity, bars)
            trade_sets[name] = trades
            pnls[name], stats[name] = pnl_and_stats(trades, bars)

        basket_policies = {"weekly": weekly, "adaptive": adaptive}
        for name, policy in basket_policies.items():
            pieces = [rg.simulate_weighted(
                bars[symbol], symbol, policy_quantity(policy, symbol)
            ).assign(dir=1) for symbol in SYMS]
            trades = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
            pnls[f"basket_{name}"], stats[f"basket_{name}"] = pnl_and_stats(
                trades, bars)
            if name == "adaptive":
                for symbol in SYMS:
                    _, stats[f"adaptive_component_{symbol}"] = pnl_and_stats(
                        trades.query("sym == @symbol"), bars)
            capped = simulate_with_global_cap(policy, bars, 3)
            pnls[f"basket_{name}_cap3"], stats[f"basket_{name}_cap3"] = (
                pnl_and_stats(capped, bars))
    finally:
        strategy.signal_frame = original

    comparisons = {}
    baseline = pnls["nq_fixed_3"]
    for name, pnl in pnls.items():
        if name != "nq_fixed_3":
            comparisons[f"{name}_vs_nq_fixed_3"] = paired_bootstrap(
                pnl, baseline, seed=20260722)

    stats_frame = pd.DataFrame(stats).T
    stats_frame.to_csv(HERE / "nq_only_results.csv")
    pd.DataFrame(comparisons).T.to_csv(HERE / "nq_only_bootstrap.csv")
    print(stats_frame.to_string(float_format=lambda value: f"{value:,.3f}"))
    print("\nPaired block bootstrap versus NQ fixed 3")
    print(pd.DataFrame(comparisons).T.to_string(
        float_format=lambda value: f"{value:,.3f}"))
    return stats_frame, comparisons, nq_policies, trade_sets


if __name__ == "__main__":
    run()
