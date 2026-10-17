#!/usr/bin/env python3
"""Frozen Wave-3 test: is plain volume merely a nonlinear clock proxy?"""
from __future__ import annotations

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
    exposure,
    extended_teachers,
    feature_frames,
    generic_predictions,
    hourly_mtm,
    load_60m,
    moving_block_bootstrap,
    path_stats,
    policy_and_base,
    rg,
    simulate_variant,
)


SPECS = {
    "CLOCK_2": FEATURES + ("hour_sin2", "hour_cos2"),
    "CLOCK_23": FEATURES + (
        "hour_sin2", "hour_cos2", "hour_sin3", "hour_cos3"),
    "CLOCK_234": FEATURES + (
        "hour_sin2", "hour_cos2", "hour_sin3", "hour_cos3",
        "hour_sin4", "hour_cos4"),
}


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    features = feature_frames(bars)
    for symbol in SYMS:
        hour = bars[symbol].index.tz_convert(
            "America/New_York").hour.to_numpy(dtype=float)
        for harmonic in (2, 3, 4):
            features[symbol][f"hour_sin{harmonic}"] = np.sin(
                2 * np.pi * harmonic * hour / 24)
            features[symbol][f"hour_cos{harmonic}"] = np.cos(
                2 * np.pi * harmonic * hour / 24)
    names = sorted(set().union(*SPECS.values()))
    teachers = extended_teachers(bars, features, names=names)
    policy, _ = policy_and_base(bars)
    r0 = pd.read_parquet(OUT / "daily_pnl.parquet")["R0"]

    rows, paths, bootstraps, attrs = [], {}, [], []
    for name, feature_names in SPECS.items():
        print(f"running {name}", flush=True)
        directions = generic_predictions(
            bars, features, teachers, feature_names)
        trades = simulate_variant(directions, policy, bars)
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
    result.to_csv(OUT / "clock_results.csv")
    pd.concat(paths, axis=1).fillna(0.0).to_parquet(
        OUT / "clock_daily_pnl.parquet")
    pd.concat(attrs, ignore_index=True).to_csv(
        OUT / "clock_symbol_attribution.csv", index=False)
    pd.DataFrame(bootstraps).to_csv(
        OUT / "clock_bootstrap.csv", index=False)
    print(result.to_string(float_format=lambda value: f"{value:,.3f}"))


if __name__ == "__main__":
    main()

