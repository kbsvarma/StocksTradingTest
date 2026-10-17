"""Adversarial validation of the frozen Stage-C ridge10_mean survivor.

This script does not select a new candidate.  Every diagnostic variant named
below is reported, and the deployable comparison is always hard-cap three.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import (  # noqa: E402
    PRODUCTION, START, SYMS, baseline_policy,
)
from run_stage_a import pnl_stats  # noqa: E402
from run_stage_b import (  # noqa: E402
    attach_initial_risk, risk_stats, simulate_capped,
)
from run_stage_c import (  # noqa: E402
    FEATURES, causal_predictions, feature_frames, fit_ridge, teacher_trades,
)


LOOKS = 21
BOOTSTRAP_REPLICATIONS = 20_000
SEED = 20260722


def moving_block_bootstrap(delta: pd.Series, block: int, *,
                           replications: int = BOOTSTRAP_REPLICATIONS,
                           seed: int = SEED) -> dict[str, float]:
    """Paired circular moving-block bootstrap of the lifetime P&L delta."""
    values = delta.fillna(0.0).to_numpy(dtype=float)
    n = len(values)
    blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed + block)
    samples = np.empty(replications, dtype=float)
    offsets = np.arange(block, dtype=int)
    batch = 200
    for lo in range(0, replications, batch):
        hi = min(lo + batch, replications)
        starts = rng.integers(0, n, size=(hi - lo, blocks, 1))
        indices = (starts + offsets) % n
        samples[lo:hi] = values[indices.reshape(hi - lo, -1)[:, :n]].sum(axis=1)
    adjusted_alpha = 0.05 / LOOKS
    return {
        "block": block,
        "observed": float(values.sum()),
        "p_nonpositive": float(np.mean(samples <= 0.0)),
        "lower_95": float(np.quantile(samples, 0.025)),
        "upper_95": float(np.quantile(samples, 0.975)),
        "bonferroni_one_sided_alpha": adjusted_alpha,
        "adjusted_lower": float(np.quantile(samples, adjusted_alpha)),
        "bootstrap_mean": float(samples.mean()),
    }


def model_coefficient_history(teachers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    latest = teachers["exit_time"].max()
    end = pd.Timestamp(year=latest.year, month=latest.month, day=1, tz="UTC")
    months = pd.date_range(START.normalize(), end, freq="MS", tz="UTC")
    names = ("intercept", *FEATURES, "is_ES", "is_YM")
    for month in months:
        train = teachers[teachers["exit_time"] < month]
        if len(train) < 500:
            continue
        model = fit_ridge(train, 10.0)
        row = {"month": month, "observations": min(len(train), 5_000)}
        row.update(dict(zip(names, model[0])))
        rows.append(row)
    return pd.DataFrame(rows)


def trade_pnl_by_symbol(trades: pd.DataFrame,
                        bars: dict[str, pd.DataFrame]) -> pd.Series:
    out = {}
    for symbol in SYMS:
        subset = trades[trades["sym"] == symbol]
        pnl, _ = pnl_stats(subset, bars)
        out[symbol] = float(pnl.sum())
    return pd.Series(out, dtype=float)


def simulate(directions, weekly, bars):
    trades = simulate_capped(directions, weekly, bars, 3)
    pnl, stats = pnl_stats(trades, bars)
    with_risk = attach_initial_risk(trades, bars)
    return trades, pnl, stats | risk_stats(with_risk)


def acceptance_rows(label, directions, base, trades):
    rows = []
    for symbol in SYMS:
        opportunities = int(base[symbol].sum())
        accepted = int(directions[symbol].sum())
        rows.append({
            "variant": label,
            "symbol": symbol,
            "breakouts": opportunities,
            "accepted_signals": accepted,
            "acceptance_rate": accepted / opportunities,
            "completed_trades": int((trades["sym"] == symbol).sum()),
        })
    return rows


def coefficient_summary(history: pd.DataFrame) -> pd.DataFrame:
    rows = []
    excluded = {"month", "observations"}
    for name in (column for column in history.columns if column not in excluded):
        values = history[name]
        rows.append({
            "coefficient": name,
            "median": float(values.median()),
            "mean": float(values.mean()),
            "positive_share": float((values > 0).mean()),
            "last": float(values.iloc[-1]),
        })
    return pd.DataFrame(rows).sort_values("median", key=np.abs, ascending=False)


def run():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    daily = {symbol: bars[symbol]["close"].resample("1D").last().dropna()
             for symbol in SYMS}
    index = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        index = index.intersection(bars[symbol].index)
    weekly = baseline_policy(daily, index)
    features = feature_frames(bars)
    teachers = teacher_trades(bars, features)
    base = {symbol: strategy.signal_frame(bars[symbol], PRODUCTION)[
        "signal"].astype(np.int8) for symbol in SYMS}

    variant_specs = {
        "baseline": None,
        "fixed_ridge10": {"penalties": (10.0,)},
        "neighbor_ridge100": {"penalties": (100.0,)},
        "embargo_31d": {"penalties": (10.0,), "embargo_days": 31},
        "window_2000": {"penalties": (10.0,),
                        "max_observations": 2_000},
        "half_life_500": {"penalties": (10.0,), "half_life": 500.0},
        "half_life_2000": {"penalties": (10.0,), "half_life": 2_000.0},
        "without_cross_section": {
            "penalties": (10.0,),
            "feature_names": tuple(name for name in FEATURES
                                   if name not in {"relative120", "breadth120"})},
        "without_clock": {
            "penalties": (10.0,),
            "feature_names": tuple(name for name in FEATURES
                                   if name not in {"hour_sin", "hour_cos"})},
    }

    rows, paths, trade_sets, acceptance = [], {}, {}, []
    for label, spec in variant_specs.items():
        if spec is None:
            directions = base
        else:
            predicted = causal_predictions(bars, features, teachers, **spec)
            penalty = int(spec.get("penalties", (10.0,))[0])
            directions = predicted[f"ridge{penalty}_mean"]
        trades, pnl, stats = simulate(directions, weekly, bars)
        rows.append({"variant": label, **stats})
        paths[label] = pnl.groupby(pnl.index.normalize()).sum()
        trade_sets[label] = trades
        acceptance.extend(acceptance_rows(label, directions, base, trades))
        print(label, stats, flush=True)

    result = pd.DataFrame(rows)
    daily_pnl = pd.concat(paths, axis=1).fillna(0.0)
    baseline = daily_pnl["baseline"]
    survivor = daily_pnl["fixed_ridge10"]
    delta = survivor.sub(baseline, fill_value=0.0)

    yearly = pd.DataFrame({
        "baseline": baseline.groupby(baseline.index.year).sum(),
        "fixed_ridge10": survivor.groupby(survivor.index.year).sum(),
    })
    yearly["increment"] = yearly["fixed_ridge10"] - yearly["baseline"]

    instrument = pd.DataFrame({
        "baseline": trade_pnl_by_symbol(trade_sets["baseline"], bars),
        "fixed_ridge10": trade_pnl_by_symbol(
            trade_sets["fixed_ridge10"], bars),
    })
    instrument["increment"] = instrument["fixed_ridge10"] - instrument["baseline"]

    bootstrap = pd.DataFrame([
        moving_block_bootstrap(delta, 63),
        moving_block_bootstrap(delta, 252),
        {"segment": "2021_end", **moving_block_bootstrap(
            delta[delta.index.year >= 2021], 63)},
    ])

    coefficients = model_coefficient_history(teachers)
    coefficient_stats = coefficient_summary(coefficients)

    total_increment = float(delta.sum())
    attribution = {
        "total_increment": total_increment,
        "largest_year_increment": float(yearly["increment"].max()),
        "largest_year_share": float(yearly["increment"].max() / total_increment),
        "largest_instrument_increment": float(instrument["increment"].max()),
        "largest_instrument_share": float(
            instrument["increment"].max() / total_increment),
        "positive_years": int((yearly["increment"] > 0).sum()),
        "negative_years": int((yearly["increment"] < 0).sum()),
    }

    result.to_csv(HERE / "stage_d_variants.csv", index=False)
    daily_pnl.to_parquet(HERE / "stage_d_daily_pnl.parquet")
    yearly.to_csv(HERE / "stage_d_yearly_attribution.csv")
    instrument.to_csv(HERE / "stage_d_instrument_attribution.csv")
    pd.DataFrame(acceptance).to_csv(HERE / "stage_d_acceptance.csv", index=False)
    bootstrap.to_csv(HERE / "stage_d_bootstrap.csv", index=False)
    coefficients.to_csv(HERE / "stage_d_coefficient_history.csv", index=False)
    coefficient_stats.to_csv(HERE / "stage_d_coefficient_summary.csv", index=False)
    (HERE / "stage_d_attribution.json").write_text(
        json.dumps(attribution, indent=2) + "\n")

    print("\nVARIANTS")
    print(result.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print("\nYEAR ATTRIBUTION")
    print(yearly.to_string(float_format=lambda x: f"{x:,.3f}"))
    print("\nINSTRUMENT ATTRIBUTION")
    print(instrument.to_string(float_format=lambda x: f"{x:,.3f}"))
    print("\nBOOTSTRAP")
    print(bootstrap.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    print("\nCOEFFICIENT SUMMARY")
    print(coefficient_stats.to_string(index=False,
          float_format=lambda x: f"{x:,.4f}"))
    print("\nATTRIBUTION", json.dumps(attribution, indent=2))
    return result


if __name__ == "__main__":
    run()
