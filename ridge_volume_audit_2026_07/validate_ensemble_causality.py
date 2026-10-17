#!/usr/bin/env python3
"""Future-label mutation test for all three ensemble decision streams."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from audit_and_extend import (
    FEATURES,
    OUT,
    PRODUCTION,
    START,
    SYMS,
    extended_teachers,
    feature_frames,
    load_60m,
    policy_and_base,
    rg,
)
from run_model_ensemble import ensemble_directions


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    features = feature_frames(bars)
    teachers = extended_teachers(bars, features, names=FEATURES)
    _, base = policy_and_base(bars)
    original = ensemble_directions(
        bars, features, teachers, base)

    cutoff = pd.Timestamp("2020-01-01", tz="UTC")
    mutated = teachers.copy()
    future = mutated["exit_time"] >= cutoff
    mutated.loc[future, "y"] = (
        -10_000 * mutated.loc[future, "y"] + 123.0)
    attacked = ensemble_directions(
        bars, features, mutated, base)

    result = {}
    for cell in original:
        result[cell] = {}
        for symbol in SYMS:
            before = original[cell][symbol]
            before = before[before.index < cutoff]
            after = attacked[cell][symbol].reindex(before.index)
            result[cell][symbol] = {
                "rows": len(before),
                "different": int(before.ne(after).sum()),
            }
    (OUT / "ensemble_causality.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

