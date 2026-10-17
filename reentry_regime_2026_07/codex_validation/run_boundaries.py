"""Mechanism-boundary checks discovered during finalist diagnosis.

These are explicitly post-screen diagnostics, not additions to the frozen
54-cell discovery family.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study as rs  # noqa: E402


def main():
    bars, policy, feats = rs.load_inputs()
    baseline = rs.gate_zero(bars, policy, feats)
    base = rs.metrics(baseline, bars)
    rows = []

    # Causal same-stop-bar confirmation: stop is intrabar, decision uses that
    # bar's completed close, fill remains next-open. The frozen primary cells
    # required at least one later completed bar.
    for multiple in (0.0, 0.5, 1.0):
        for horizon in (6, 12, 24):
            h1 = {"kind": "ATR", "m": multiple, "horizon": horizon,
                  "min_elapsed": 0}
            result = rs.simulate_cell(bars, policy, feats, h1=h1)
            stat = rs.metrics(result, bars)
            rows.append({"kind": "same_bar", "m": multiple,
                         "horizon": horizon, **stat,
                         "delta_total": stat["total"] - base["total"]})
            print(f"same-bar m={multiple:g} h={horizon}: "
                  f"d={stat['total']-base['total']:>+9,.0f} "
                  f"dd={stat['max_dd']:>9,.0f} worst={stat['worst_completed']:>8,.0f}")

    # Do not apply WIDTH blocking during the first 20 completed bars after a
    # splice. Persistence here rules out the filter merely exploiting
    # continuous-contract roll distortion.
    no_roll_feats = {}
    for sym, base_feat in feats.items():
        copied = dict(base_feat)
        copied["chop"] = dict(base_feat["chop"])
        roll = bars[sym]["roll"].to_numpy(dtype=bool)
        safe = np.ones(len(roll), dtype=bool)
        for i in np.flatnonzero(roll):
            safe[i:min(i + 21, len(safe))] = False
        copied["chop"]["WIDTH_NO_ROLL"] = (
            base_feat["chop"]["WIDTH20"] & safe)
        no_roll_feats[sym] = copied
    nr = rs.simulate_cell(
        bars, policy, no_roll_feats,
        h2={"detector": "WIDTH_NO_ROLL", "mode": "BLOCK"})
    nr_stat = rs.metrics(nr, bars)
    rows.append({"kind": "width_no_roll", "m": np.nan,
                 "horizon": np.nan, **nr_stat,
                 "delta_total": nr_stat["total"] - base["total"]})
    print("WIDTH excluding 20 post-roll bars:", nr_stat,
          "delta", nr_stat["total"] - base["total"])
    pd.DataFrame(rows).to_csv(HERE / "boundary_diagnostics.csv", index=False)


if __name__ == "__main__":
    main()
