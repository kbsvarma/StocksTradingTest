#!/usr/bin/env python3
"""Frozen Wave-2 ensemble of the pre-existing causal ridge neighborhood."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from audit_and_extend import (
    END_SELECTION,
    FEATURES,
    FRICTIONS,
    OUT,
    PRODUCTION,
    START,
    SYMS,
    attribution,
    baseline_policy,
    exposure,
    extended_teachers,
    feature_frames,
    hourly_mtm,
    load_60m,
    moving_block_bootstrap,
    path_stats,
    pnl_stats,
    policy_and_base,
    rg,
    simulate_variant,
    strategy,
)
from run_stage_c import causal_scores


EXPERTS = {
    "r0": {},
    "penalty100": {"penalty": 100.0},
    "embargo31": {"embargo_days": 31},
    "window2000": {"max_observations": 2_000},
    "half500": {"half_life": 500.0},
    "half2000": {"half_life": 2_000.0},
}


def ensemble_directions(bars, features, teachers, base):
    scores = {}
    for name, arguments in EXPERTS.items():
        print(f"scoring expert {name}", flush=True)
        scores[name] = causal_scores(
            bars, features, teachers, feature_names=FEATURES, **arguments)

    output = {
        name: {
            symbol: pd.Series(0, index=bars[symbol].index, dtype=np.int8)
            for symbol in SYMS
        }
        for name in ("ENS_MEAN", "ENS_MEDIAN", "ENS_VOTE4")
    }
    for symbol in SYMS:
        matrix = pd.concat(
            {name: score[symbol] for name, score in scores.items()}, axis=1)
        opportunities = base[symbol].astype(bool)
        available = matrix.notna().sum(axis=1)
        startup = opportunities & available.eq(0)
        output["ENS_MEAN"][symbol].loc[startup] = 1
        output["ENS_MEDIAN"][symbol].loc[startup] = 1
        output["ENS_VOTE4"][symbol].loc[startup] = 1
        active = opportunities & available.gt(0)
        output["ENS_MEAN"][symbol].loc[
            active & matrix.mean(axis=1).gt(0)] = 1
        output["ENS_MEDIAN"][symbol].loc[
            active & matrix.median(axis=1).gt(0)] = 1
        output["ENS_VOTE4"][symbol].loc[
            active & matrix.gt(0).sum(axis=1).ge(4)] = 1
    return output


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    features = feature_frames(bars)
    teachers = extended_teachers(bars, features, names=FEATURES)
    policy, base = policy_and_base(bars)
    directions = ensemble_directions(
        bars, features, teachers, base)

    r0 = pd.read_parquet(OUT / "daily_pnl.parquet")["R0"]
    rows, bootstraps, paths, attrs = [], [], {}, []
    for name, direction in directions.items():
        print(f"simulating {name}", flush=True)
        trades = simulate_variant(direction, policy, bars)
        pnl = hourly_mtm(trades, bars, 3.5)
        paths[name] = pnl.groupby(pnl.index.normalize()).sum()
        row = {
            "cell": name,
            **path_stats(pnl, trades),
            "net_full": float(pnl.sum()),
            "max_exposure": exposure(trades),
        }
        for friction in FRICTIONS:
            stressed = hourly_mtm(trades, bars, friction)
            row[f"net_friction_{friction:g}"] = float(
                stressed[stressed.index <= END_SELECTION].sum())
        rows.append(row)
        attr = attribution(trades, bars)
        attr["cell"] = name
        attrs.append(attr)
        trades.to_parquet(OUT / f"trades_{name}.parquet", index=False)

        delta = paths[name].sub(r0, fill_value=0.0)
        delta = delta[delta.index <= END_SELECTION]
        for block in (63, 252):
            bootstraps.append(
                {"cell": name, **moving_block_bootstrap(delta, block)})

    result = pd.DataFrame(rows).set_index("cell")
    result.to_csv(OUT / "ensemble_results.csv")
    pd.concat(paths, axis=1).fillna(0.0).to_parquet(
        OUT / "ensemble_daily_pnl.parquet")
    pd.concat(attrs, ignore_index=True).to_csv(
        OUT / "ensemble_symbol_attribution.csv", index=False)
    pd.DataFrame(bootstraps).to_csv(
        OUT / "ensemble_bootstrap.csv", index=False)
    (OUT / "ensemble_summary.json").write_text(json.dumps(
        {"experts": EXPERTS, "cells": list(result.index)}, indent=2) + "\n")
    print(result.to_string(float_format=lambda value: f"{value:,.3f}"))


if __name__ == "__main__":
    main()
