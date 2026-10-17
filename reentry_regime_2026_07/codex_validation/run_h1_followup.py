"""Post-hoc H1 mechanism map around the one-bar quarantine finding."""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study as rs  # noqa: E402


def main():
    bars, policy, feats = rs.load_inputs()
    baseline = rs.gate_zero(bars, policy, feats)
    base = rs.metrics(baseline, bars)
    rows = []
    for multiple in (-0.25, 0.0, 0.25, 0.5):
        for minimum in (1, 2, 3):
            for horizon in (6, 12):
                h1 = {"kind": "ATR", "m": multiple, "horizon": horizon,
                      "min_elapsed": minimum}
                result = rs.simulate_cell(bars, policy, feats, h1=h1)
                stat = rs.metrics(result, bars)
                s7 = rs.metrics(result, bars, 7.0)
                rows.append({"m": multiple, "min_elapsed": minimum,
                             "horizon": horizon, **stat,
                             "delta_total": stat["total"] - base["total"],
                             "delta_cost7": (s7["total"]
                                             - rs.metrics(baseline, bars, 7.0)["total"])})
                print(f"m={multiple:+.2f} wait={minimum} h={horizon:2} "
                      f"d={stat['total']-base['total']:>+9,.0f} "
                      f"dd={stat['max_dd']:>9,.0f} p/dd={stat['p_dd']:>5.2f} "
                      f"worst={stat['worst_completed']:>8,.0f}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "h1_followup.csv", index=False)
    print("\nTop net")
    print(out.sort_values("total", ascending=False).head(10)[
        ["m", "min_elapsed", "horizon", "total", "delta_total", "max_dd",
         "p_dd", "worst_completed", "delta_cost7", "era_2011_15",
         "era_2016_20", "era_2021_25", "ytd_2026"]].to_string(index=False))


if __name__ == "__main__":
    main()
