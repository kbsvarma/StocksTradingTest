#!/usr/bin/env python3
"""Forensic reproduction of the preregistered ratio (not shipped log-ratio)."""
from __future__ import annotations

import pandas as pd

from audit_and_extend import (
    END_SELECTION,
    FEATURES,
    OUT,
    PRODUCTION,
    START,
    SYMS,
    extended_teachers,
    feature_frames,
    generic_predictions,
    hourly_mtm,
    load_60m,
    path_stats,
    policy_and_base,
    rg,
    simulate_variant,
)


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    features = feature_frames(bars)
    for symbol in SYMS:
        volume = bars[symbol]["volume"].replace(0.0, pd.NA).astype(float)
        median = volume.rolling(480, min_periods=240).median().shift(1)
        features[symbol]["rv_ratio"] = (volume / median).clip(0.0, 20.0)
    names = FEATURES + ("rv_ratio",)
    teachers = extended_teachers(bars, features, names=names)
    policy, _ = policy_and_base(bars)
    directions = generic_predictions(
        bars, features, teachers, names)
    trades = simulate_variant(directions, policy, bars)
    pnl = hourly_mtm(trades, bars, 3.5)
    result = {
        **path_stats(pnl, trades),
        "net_full": float(pnl.sum()),
        "full_trades": len(trades),
        "full_contracts": int(trades["qty"].sum()),
    }
    pd.DataFrame([result]).to_csv(
        OUT / "declared_ratio_reproduction.csv", index=False)
    trades.to_parquet(
        OUT / "trades_RV_DECLARED_RATIO.parquet", index=False)
    print(pd.Series(result).to_string())


if __name__ == "__main__":
    main()

