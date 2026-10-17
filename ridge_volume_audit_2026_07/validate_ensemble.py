#!/usr/bin/env python3
"""Validation diagnostics for ENS_VOTE4; never selects another cell."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_and_extend import END_SELECTION, OUT, SEED


ROOT = Path(__file__).resolve().parent.parent


def sharpe(values: np.ndarray) -> np.ndarray:
    mean = values.mean(axis=0)
    standard_deviation = values.std(axis=0, ddof=1)
    return np.divide(
        mean, standard_deviation,
        out=np.full_like(mean, -np.inf, dtype=float),
        where=standard_deviation > 0,
    ) * np.sqrt(252)


def cscv(paths: pd.DataFrame, blocks=16):
    """Combinatorially symmetric cross-validation PBO diagnostic."""
    indices = np.array_split(np.arange(len(paths)), blocks)
    records = []
    # Each train/test partition is symmetric; retain one of each complement.
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        if 0 not in chosen:
            continue
        train_blocks = set(chosen)
        train_index = np.concatenate(
            [indices[i] for i in sorted(train_blocks)])
        test_index = np.concatenate(
            [indices[i] for i in range(blocks) if i not in train_blocks])
        train_sharpe = sharpe(paths.iloc[train_index].to_numpy())
        test_sharpe = sharpe(paths.iloc[test_index].to_numpy())
        winner = int(np.argmax(train_sharpe))
        # Percentile rank in (0,1), where <=.5 is below-median OOS.
        rank = (np.sum(test_sharpe < test_sharpe[winner]) + 0.5) / len(
            test_sharpe)
        records.append({
            "winner": paths.columns[winner],
            "train_sharpe": train_sharpe[winner],
            "test_sharpe": test_sharpe[winner],
            "oos_percentile": rank,
            "logit": float(np.log(rank / (1.0 - rank))),
        })
    frame = pd.DataFrame(records)
    summary = {
        "paths": list(paths.columns),
        "blocks": blocks,
        "partitions": len(frame),
        "pbo": float(frame["logit"].le(0).mean()),
        "median_oos_percentile": float(frame["oos_percentile"].median()),
        "mean_degradation": float(
            (frame["train_sharpe"] - frame["test_sharpe"]).mean()),
        "winner_frequency": frame["winner"].value_counts(
            normalize=True).to_dict(),
    }
    return frame, summary


def random_subperiods(delta: pd.Series, years=5, replications=20_000):
    """Distribution of contiguous five-year delta windows."""
    daily = delta.sort_index()
    starts = daily.index[
        daily.index <= daily.index.max() - pd.DateOffset(years=years)]
    random = np.random.default_rng(SEED)
    selected = random.choice(len(starts), replications, replace=True)
    totals = np.array([
        daily.loc[starts[i]:starts[i] + pd.DateOffset(years=years)].sum()
        for i in selected
    ])
    return {
        "years": years,
        "replications": replications,
        "positive_share": float(np.mean(totals > 0)),
        "median": float(np.median(totals)),
        "p05": float(np.quantile(totals, 0.05)),
        "p95": float(np.quantile(totals, 0.95)),
    }


def main():
    corrected = pd.read_parquet(OUT / "daily_pnl.parquet")
    ensemble = pd.read_parquet(OUT / "ensemble_daily_pnl.parquet")
    paths = pd.concat(
        [
            corrected["R0"],
            ensemble,
        ],
        axis=1,
    ).fillna(0.0)
    paths = paths[paths.index <= END_SELECTION]
    partitions, pbo = cscv(paths)
    partitions.to_csv(OUT / "ensemble_cscv_partitions.csv", index=False)

    combined = pd.concat(
        [
            corrected["R0"],
            ensemble["ENS_VOTE4"],
        ],
        axis=1,
    ).fillna(0.0)
    combined = combined[combined.index <= END_SELECTION]
    delta = combined["ENS_VOTE4"] - combined["R0"]
    rolling = {}
    for years in (3, 5, 8):
        totals = []
        for start in pd.date_range(
                delta.index.min().normalize(),
                delta.index.max() - pd.DateOffset(years=years),
                freq="QS", tz="UTC"):
            totals.append({
                "years": years,
                "start": start,
                "end": start + pd.DateOffset(years=years),
                "delta": float(delta.loc[
                    start:start + pd.DateOffset(years=years)].sum()),
            })
        rolling[years] = totals
    rolling_frame = pd.DataFrame(
        [row for values in rolling.values() for row in values])
    rolling_frame.to_csv(
        OUT / "ensemble_rolling_windows.csv", index=False)
    summary = {
        "cscv": pbo,
        "random_five_year": random_subperiods(delta),
        "rolling_window_positive_share": {
            str(years): float(
                (rolling_frame.loc[
                    rolling_frame["years"].eq(years), "delta"] > 0).mean())
            for years in rolling
        },
    }
    (OUT / "ensemble_validation.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
