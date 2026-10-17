"""Pass VI: causal random-feature nonlinear and conformal meta-filters."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist

from common import (
    HERE, PRODUCTION, START, SYMS, load_bars, run_directions, update_ledger,
)
from run_stage_c import FEATURES, feature_frames, teacher_trades
import strategy


SEED = 20260722


def raw_matrix(frame: pd.DataFrame, means=None, scales=None):
    numeric = frame[list(FEATURES)].to_numpy(dtype=float)
    if means is None:
        means = numeric.mean(axis=0)
        scales = numeric.std(axis=0, ddof=1)
        scales = np.where(scales > 0, scales, 1.0)
    standardized = (numeric - means) / scales
    symbols = frame["sym"].astype(str)
    matrix = np.column_stack((
        standardized,
        symbols.eq("ES").to_numpy(dtype=float),
        symbols.eq("YM").to_numpy(dtype=float),
    ))
    return matrix, means, scales


def random_map(x_train: np.ndarray, dimension: int):
    sample_rows = np.linspace(
        0, len(x_train) - 1, min(512, len(x_train)), dtype=int)
    distances = pdist(x_train[sample_rows])
    positive = distances[distances > 0]
    length_scale = float(np.median(positive)) if len(positive) else 1.0
    rng = np.random.default_rng(SEED)
    frequencies = rng.normal(
        0.0, 1.0 / max(length_scale, 1e-12),
        size=(x_train.shape[1], dimension))
    phase = rng.uniform(0.0, 2.0 * np.pi, size=dimension)

    def transform(x):
        return np.sqrt(2.0 / dimension) * np.cos(x @ frequencies + phase)

    return transform, length_scale


def fit_weighted_ridge(train: pd.DataFrame, dimension: int):
    x, means, scales = raw_matrix(train)
    transform, length_scale = random_map(x, dimension)
    phi = transform(x)
    design = np.column_stack((np.ones(len(phi)), phi))
    y = train["y"].to_numpy(dtype=float)
    age = np.arange(len(train) - 1, -1, -1)
    weights = np.exp(np.log(.5) * age / 1000.0)
    gram = design.T @ (design * weights[:, None])
    penalty = 10.0 * np.eye(design.shape[1])
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(gram + penalty, design.T @ (weights * y))
    return beta, means, scales, transform, length_scale


def predict(model, frame: pd.DataFrame) -> np.ndarray:
    beta, means, scales, transform, _ = model
    x, _, _ = raw_matrix(frame, means, scales)
    phi = transform(x)
    return np.column_stack((np.ones(len(phi)), phi)) @ beta


def conformal_model(train: pd.DataFrame, alpha: float):
    split = max(int(np.floor(.8 * len(train))), 1)
    proper, calibration = train.iloc[:split], train.iloc[split:]
    model = fit_weighted_ridge(proper, 128)
    calibration_prediction = predict(model, calibration)
    nonconformity = calibration_prediction - calibration["y"].to_numpy(dtype=float)
    level = min(np.ceil((len(nonconformity) + 1) * (1.0 - alpha))
                / len(nonconformity), 1.0)
    quantile = float(np.quantile(nonconformity, level, method="higher"))
    return model, quantile


def causal_directions(bars, features, teachers):
    names = ("rff64_mean", "rff128_mean", "rff128_conf20", "rff128_conf10")
    output = {
        name: {symbol: pd.Series(0, index=bars[symbol].index, dtype=np.int8)
               for symbol in SYMS}
        for name in names
    }
    base = {symbol: strategy.signal_frame(
        bars[symbol], PRODUCTION)["signal"].astype(np.int8) for symbol in SYMS}
    months = pd.date_range(
        START.normalize(), max(frame.index.max() for frame in bars.values()),
        freq="MS", tz="UTC")
    for month in months:
        next_month = month + pd.offsets.MonthBegin(1)
        train = teachers[teachers["exit_time"] < month].tail(5000)
        blocks = []
        for symbol in SYMS:
            mask = ((bars[symbol].index >= month)
                    & (bars[symbol].index < next_month)
                    & base[symbol].astype(bool))
            block = features[symbol].loc[mask, list(FEATURES)].copy()
            block["sym"] = symbol
            block["stamp"] = block.index
            blocks.append(block)
        decisions = pd.concat(blocks).dropna(subset=list(FEATURES))
        if decisions.empty:
            continue
        if len(train) < 500:
            for name in names:
                for row in decisions.itertuples():
                    output[name][row.sym].loc[row.stamp] = 1
            continue
        models = {dimension: fit_weighted_ridge(train, dimension)
                  for dimension in (64, 128)}
        predictions = {dimension: predict(models[dimension], decisions)
                       for dimension in (64, 128)}
        accepted = {
            "rff64_mean": predictions[64] > 0.0,
            "rff128_mean": predictions[128] > 0.0,
        }
        for alpha, name in ((.20, "rff128_conf20"),
                            (.10, "rff128_conf10")):
            model, quantile = conformal_model(train, alpha)
            accepted[name] = predict(model, decisions) - quantile > 0.0
        for name, mask in accepted.items():
            for row, allow in zip(decisions.itertuples(), mask):
                if allow:
                    output[name][row.sym].loc[row.stamp] = 1
    return output


def run() -> pd.DataFrame:
    bars = load_bars()
    features = feature_frames(bars)
    teachers = teacher_trades(bars, features)
    directions = causal_directions(bars, features, teachers)
    results, daily, trades = run_directions(bars, {
        symbol: {name: paths[symbol] for name, paths in directions.items()}
        for symbol in SYMS
    })
    results.to_csv(HERE / "pass6_results.csv", index=False)
    daily.to_parquet(HERE / "pass6_daily_pnl.parquet")
    pd.concat(trades, names=["candidate", "row"]).to_parquet(
        HERE / "pass6_trades.parquet")
    update_ledger("VI", "pass6_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
