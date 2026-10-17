"""Causal monthly ridge meta-filter over unchanged production breakouts."""
from __future__ import annotations

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
    PRODUCTION, START, SYMS, baseline_policy, cross_z,
)
from run_stage_a import (  # noqa: E402
    efficiency_ratio, normalized_ema_score, pnl_stats,
    roll_clean_log_returns, rolling_t_score, simulate_direction,
)
from run_stage_b import quantity_lookup, simulate_capped  # noqa: E402


FEATURES = (
    "overshoot", "t24", "t120", "t480", "nema", "eff120", "vol_ratio",
    "relative120", "breadth120", "hour_sin", "hour_cos",
)


def feature_frames(bars: dict[str, pd.DataFrame], config=PRODUCTION):
    frames, ret120 = {}, {}
    for symbol in SYMS:
        market = bars[symbol]
        signal = strategy.signal_frame(market, config)
        returns = roll_clean_log_returns(market)
        ret120[symbol] = returns.rolling(120, min_periods=120).sum()
        hour = market.index.tz_convert("America/New_York").hour
        frames[symbol] = pd.DataFrame({
            "overshoot": (market["close"] - signal["donchian"]) / signal["atr"],
            "t24": rolling_t_score(returns, 24),
            "t120": rolling_t_score(returns, 120),
            "t480": rolling_t_score(returns, 480),
            "nema": normalized_ema_score(market),
            "eff120": efficiency_ratio(returns, 120),
            "vol_ratio": signal["atr_pct"] / signal["vol_thresh"],
            "hour_sin": np.sin(2 * np.pi * hour / 24),
            "hour_cos": np.cos(2 * np.pi * hour / 24),
        }, index=market.index)
    common = ret120[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(ret120[symbol].index)
    relative = pd.concat({symbol: ret120[symbol].reindex(common)
                          for symbol in SYMS}, axis=1)
    relative_z = cross_z(relative)
    breadth = relative.gt(0).sum(axis=1) / len(SYMS)
    for symbol in SYMS:
        frames[symbol]["relative120"] = relative_z[symbol].reindex(
            frames[symbol].index)
        frames[symbol]["breadth120"] = breadth.reindex(frames[symbol].index)
    return frames


def teacher_trades(bars: dict[str, pd.DataFrame], features,
                   config=PRODUCTION):
    rows = []
    for symbol in SYMS:
        directions = strategy.signal_frame(bars[symbol], config)["signal"].astype(
            np.int8)
        trades = simulate_direction(
            bars[symbol], symbol, directions, config=config)
        atr = strategy.signal_frame(bars[symbol], config)["atr"]
        index = bars[symbol].index
        for trade in trades.itertuples():
            entry_i = index.get_loc(trade.entry_time)
            signal_i = entry_i - 1
            if signal_i < 0:
                continue
            feature = features[symbol].iloc[signal_i]
            if not np.isfinite(feature[list(FEATURES)].to_numpy(dtype=float)).all():
                continue
            risk_dollars = (config["k_trail"] * float(atr.iloc[signal_i])
                            * rg.MULT[symbol])
            net_dollars = trade.points * rg.MULT[symbol] - rg.FR
            rows.append({
                "signal_time": index[signal_i], "exit_time": trade.exit_time,
                "sym": symbol, "y": net_dollars / risk_dollars,
                **feature[list(FEATURES)].to_dict(),
            })
    return pd.DataFrame(rows).sort_values("exit_time").reset_index(drop=True)


def design_matrix(frame: pd.DataFrame, means=None, scales=None,
                  feature_names=FEATURES):
    feature_names = tuple(feature_names)
    numeric = frame[list(feature_names)].to_numpy(dtype=float)
    if means is None:
        means = np.nanmean(numeric, axis=0)
        scales = np.nanstd(numeric, axis=0, ddof=1)
        scales = np.where(np.isfinite(scales) & (scales > 0), scales, 1.0)
    standardized = (numeric - means) / scales
    symbols = frame["sym"].astype(str)
    matrix = np.column_stack([
        np.ones(len(frame)), standardized,
        symbols.eq("ES").to_numpy(dtype=float),
        symbols.eq("YM").to_numpy(dtype=float),
    ])
    return matrix, means, scales


def fit_ridge(train: pd.DataFrame, penalty: float, feature_names=FEATURES,
              max_observations: int = 5_000, half_life: float = 1_000.0):
    feature_names = tuple(feature_names)
    train = train.tail(max_observations)
    x, means, scales = design_matrix(train, feature_names=feature_names)
    y = train["y"].to_numpy(dtype=float)
    age = np.arange(len(train) - 1, -1, -1)
    weights = np.exp(np.log(0.5) * age / half_life)
    xtwx = x.T @ (x * weights[:, None])
    penalizer = np.eye(x.shape[1]) * penalty
    penalizer[0, 0] = 0.0
    penalizer[-2:, -2:] = 0.0
    information = xtwx + penalizer
    inverse = np.linalg.pinv(information)
    beta = inverse @ (x.T @ (weights * y))
    residual = y - x @ beta
    dof = max(weights.sum() - x.shape[1], 1.0)
    variance = float(np.sum(weights * residual ** 2) / dof)
    return beta, inverse, variance, means, scales, feature_names


def predict(model, frame: pd.DataFrame):
    beta, inverse, variance, means, scales, feature_names = model
    x, _, _ = design_matrix(frame, means, scales, feature_names)
    mean = x @ beta
    mean_variance = variance * np.einsum("ij,jk,ik->i", x, inverse, x)
    se = np.sqrt(np.clip(mean_variance, 1e-12, None))
    return mean, mean / se


def causal_predictions(bars, features, teachers, *, penalties=(10.0, 100.0),
                       feature_names=FEATURES, max_observations=5_000,
                       half_life=1_000.0, embargo_days=0,
                       config=PRODUCTION):
    feature_names = tuple(feature_names)
    base = {symbol: strategy.signal_frame(bars[symbol], config)[
        "signal"].astype(np.int8) for symbol in SYMS}
    output = {
        name: {symbol: pd.Series(0, index=bars[symbol].index, dtype=np.int8)
               for symbol in SYMS}
        for penalty in penalties
        for name in (f"ridge{int(penalty)}_mean",
                     f"ridge{int(penalty)}_t05")
    }
    months = pd.date_range(START.normalize(),
                           max(frame.index.max() for frame in bars.values()),
                           freq="MS", tz="UTC")
    for month in months:
        next_month = month + pd.offsets.MonthBegin(1)
        cutoff = month - pd.Timedelta(days=embargo_days)
        train = teachers[teachers["exit_time"] < cutoff]
        blocks = []
        for symbol in SYMS:
            mask = ((bars[symbol].index >= month)
                    & (bars[symbol].index < next_month)
                    & base[symbol].astype(bool))
            block = features[symbol].loc[mask, list(feature_names)].copy()
            block["sym"] = symbol
            block["stamp"] = block.index
            blocks.append(block)
        decisions = pd.concat(blocks).dropna(subset=list(feature_names))
        if len(decisions) == 0:
            continue
        if len(train) < 500:
            for name in output:
                for row in decisions.itertuples():
                    output[name][row.sym].loc[row.stamp] = 1
            continue
        for penalty in penalties:
            model = fit_ridge(
                train, penalty, feature_names=feature_names,
                max_observations=max_observations, half_life=half_life)
            mean, evidence = predict(model, decisions)
            masks = {"mean": mean > 0.0, "t05": evidence > 0.5}
            for suffix, accepted in masks.items():
                name = f"ridge{int(penalty)}_{suffix}"
                for row, allow in zip(decisions.itertuples(), accepted):
                    if allow:
                        output[name][row.sym].loc[row.stamp] = 1
    return output


def causal_scores(bars, features, teachers, *, penalty=10.0,
                  feature_names=FEATURES, max_observations=5_000,
                  half_life=1_000.0, embargo_days=0,
                  config=PRODUCTION):
    """Predicted risk-unit expectancy at each baseline signal timestamp."""
    feature_names = tuple(feature_names)
    base = {symbol: strategy.signal_frame(bars[symbol], config)[
        "signal"].astype(np.int8) for symbol in SYMS}
    output = {symbol: pd.Series(np.nan, index=bars[symbol].index, dtype=float)
              for symbol in SYMS}
    months = pd.date_range(START.normalize(),
                           max(frame.index.max() for frame in bars.values()),
                           freq="MS", tz="UTC")
    for month in months:
        next_month = month + pd.offsets.MonthBegin(1)
        cutoff = month - pd.Timedelta(days=embargo_days)
        train = teachers[teachers["exit_time"] < cutoff]
        if len(train) < 500:
            continue
        blocks = []
        for symbol in SYMS:
            mask = ((bars[symbol].index >= month)
                    & (bars[symbol].index < next_month)
                    & base[symbol].astype(bool))
            block = features[symbol].loc[mask, list(feature_names)].copy()
            block["sym"] = symbol
            block["stamp"] = block.index
            blocks.append(block)
        decisions = pd.concat(blocks).dropna(subset=list(feature_names))
        if decisions.empty:
            continue
        model = fit_ridge(
            train, penalty, feature_names=feature_names,
            max_observations=max_observations, half_life=half_life)
        means, _ = predict(model, decisions)
        for row, mean in zip(decisions.itertuples(), means):
            output[row.sym].loc[row.stamp] = float(mean)
    return output


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
    teachers.to_parquet(HERE / "stage_c_teacher.parquet", index=False)
    predictions = causal_predictions(bars, features, teachers)

    rows, pnl_paths = [], {}
    base_directions = {symbol: strategy.signal_frame(
        bars[symbol], PRODUCTION)["signal"].astype(np.int8) for symbol in SYMS}
    candidates = {"donchian": base_directions, **predictions}
    for name, directions in candidates.items():
        pieces = [simulate_direction(
            bars[symbol], symbol, directions[symbol],
            quantity=quantity_lookup(weekly, symbol)) for symbol in SYMS]
        trades = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
        pnl, stats = pnl_stats(trades, bars)
        rows.append({"candidate": name, "mode": "entry_bound", **stats})
        pnl_paths[f"{name}_entry_bound"] = pnl.groupby(
            pnl.index.normalize()).sum()
        print(name, "entry_bound", stats, flush=True)
        for cap in (3, 6):
            capped = simulate_capped(directions, weekly, bars, cap)
            capped_pnl, capped_stats = pnl_stats(capped, bars)
            rows.append({"candidate": name, "mode": f"cap{cap}",
                         **capped_stats})
            pnl_paths[f"{name}_cap{cap}"] = capped_pnl.groupby(
                capped_pnl.index.normalize()).sum()
            print(name, f"cap{cap}", capped_stats, flush=True)
    result = pd.DataFrame(rows)
    result.to_csv(HERE / "stage_c_results.csv", index=False)
    pd.concat(pnl_paths, axis=1).fillna(0.0).to_parquet(
        HERE / "stage_c_daily_pnl.parquet")
    print(result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result


if __name__ == "__main__":
    run()
