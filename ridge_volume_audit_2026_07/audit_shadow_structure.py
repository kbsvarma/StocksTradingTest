#!/usr/bin/env python3
"""Re-run only the shadow-engine/roll-flag structural audit."""
from __future__ import annotations

import pandas as pd

from audit_and_extend import (
    FEATURES,
    OUT,
    PLAIN,
    PRODUCTION,
    START,
    SYMS,
    add_volume_features,
    exposure,
    extended_teachers,
    feature_frames,
    generic_predictions,
    hourly_mtm,
    load_60m,
    policy_and_base,
    rg,
    shadow_replay,
    simulate_variant,
)


def main():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: load_60m(symbol) for symbol in SYMS}
    rows = []
    for roll_mode in ("correct", "zero"):
        altered = {symbol: frame.copy() for symbol, frame in bars.items()}
        if roll_mode == "zero":
            for frame in altered.values():
                frame["roll"] = 0
        original = feature_frames(altered)
        features, _ = add_volume_features(altered, original)
        teachers = extended_teachers(altered, features)
        policy, _ = policy_and_base(altered)
        for label, names in (("R0", FEATURES), ("RV_PLAIN", PLAIN)):
            predicted = generic_predictions(
                altered, features, teachers, names)
            for engine, trades in (
                ("joint_cap3", simulate_variant(
                    predicted, policy, altered)),
                ("independent", shadow_replay(
                    predicted, policy, altered)),
            ):
                pnl = hourly_mtm(trades, altered, 3.5)
                rows.append({
                    "roll_mode": roll_mode,
                    "engine": engine,
                    "cell": label,
                    "net_full": float(pnl.sum()),
                    "max_exposure": exposure(trades),
                    "trades": len(trades),
                    "contracts": int(trades["qty"].sum()),
                })
    result = pd.DataFrame(rows)
    result.to_csv(OUT / "shadow_structure_audit.csv", index=False)
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()

