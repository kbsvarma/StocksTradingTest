"""Attribution tests for WIDTH20/BLOCK versus matched random abstention."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
import run_study as rs  # noqa: E402


def by_symbol(trades, bars, friction=3.5):
    rows = []
    for sym in bars:
        subset = trades[trades["sym"] == sym]
        stat = rs.metrics(subset, {sym: bars[sym]}, friction)
        rows.append({"sym": sym, **stat})
    return pd.DataFrame(rows)


def main():
    bars, policy, feats = rs.load_inputs()
    baseline = rs.gate_zero(bars, policy, feats)
    candidate = rs.simulate_cell(
        bars, policy, feats,
        h2={"detector": "WIDTH20", "mode": "BLOCK"})
    base_stat = rs.metrics(baseline, bars)
    candidate_stat = rs.metrics(candidate, bars)
    actual_delta = candidate_stat["total"] - base_stat["total"]

    b_sym = by_symbol(baseline, bars).set_index("sym")
    c_sym = by_symbol(candidate, bars).set_index("sym")
    symbol = c_sym.add_prefix("candidate_").join(b_sym.add_prefix("baseline_"))
    for col in ("total", "max_dd", "p_dd", "worst_completed", "trades",
                "contracts"):
        symbol[f"delta_{col}"] = (symbol[f"candidate_{col}"]
                                         - symbol[f"baseline_{col}"])
    symbol.to_csv(HERE / "width_by_symbol.csv")

    # Circularly shift each symbol's actual chop-state series. This preserves
    # the gate's frequency and run-length structure while breaking its link to
    # contemporaneous price. It is a stronger matched-trade-diet null than
    # independent coin flips.
    rng = np.random.default_rng(20260731)
    rows = []
    draws = 250
    for seed in range(draws):
        placebo_feats = {}
        offsets = {}
        for sym, base in feats.items():
            copied = dict(base)
            copied["chop"] = dict(base["chop"])
            values = base["chop"]["WIDTH20"]
            # Avoid trivial small shifts; at least 90 days of hourly bars.
            offset = int(rng.integers(24 * 90, len(values) - 24 * 90))
            offsets[sym] = offset
            copied["chop"]["PLACEBO"] = np.roll(values, offset)
            placebo_feats[sym] = copied
        result = rs.simulate_cell(
            bars, policy, placebo_feats,
            h2={"detector": "PLACEBO", "mode": "BLOCK"})
        stat = rs.metrics(result, bars)
        rows.append({"seed": seed, **stat,
                     "delta_total": stat["total"] - base_stat["total"],
                     "delta_p_dd": stat["p_dd"] - base_stat["p_dd"],
                     "offset_NQ": offsets["NQ"],
                     "offset_ES": offsets["ES"],
                     "offset_YM": offsets["YM"]})
        if (seed + 1) % 25 == 0:
            print(f"placebo {seed + 1}/{draws}", flush=True)
    placebo = pd.DataFrame(rows)
    placebo.to_csv(HERE / "width_placebo.csv", index=False)
    p_total = ((placebo["delta_total"] >= actual_delta).sum() + 1) / (draws + 1)
    p_ratio = ((placebo["p_dd"] >= candidate_stat["p_dd"]).sum() + 1) / (draws + 1)
    print("\nSYMBOL ATTRIBUTION")
    print(symbol.to_string())
    print("\nPLACEBO")
    print({"actual_delta": actual_delta,
           "placebo_delta_median": float(placebo["delta_total"].median()),
           "placebo_delta_95": [float(placebo["delta_total"].quantile(.025)),
                                  float(placebo["delta_total"].quantile(.975))],
           "p_total": p_total,
           "actual_p_dd": candidate_stat["p_dd"],
           "p_ratio": p_ratio})


if __name__ == "__main__":
    main()
