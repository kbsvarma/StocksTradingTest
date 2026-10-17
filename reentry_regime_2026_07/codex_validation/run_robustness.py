"""Post-screen robustness and diagnostics for the two surviving mechanisms."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
import run_study as rs  # noqa: E402


def max_exposure(trades: pd.DataFrame) -> tuple[int, float]:
    events = []
    for tr in trades.itertuples():
        # Exit before entry at the same timestamp matches the engine's
        # position lifecycle and avoids a fake one-instant overlap.
        events.append((tr.entry_time, 1, int(tr.qty)))
        events.append((tr.exit_time, 0, -int(tr.qty)))
    exposure = 0
    maximum = 0
    weighted = 0.0
    last = None
    total_hours = 0.0
    for stamp, _, change in sorted(events):
        if last is not None:
            hours = (stamp - last).total_seconds() / 3600.0
            weighted += exposure * hours
            total_hours += hours
        exposure += change
        maximum = max(maximum, exposure)
        last = stamp
    return maximum, weighted / total_hours if total_hours else 0.0


def main():
    bars, policy, base_features = rs.load_inputs()
    baseline = rs.gate_zero(bars, policy, base_features)
    base = rs.metrics(baseline, bars)
    rows = []

    # Threshold/lookback/window surface around WIDTH20/BLOCK. This is a
    # robustness map, not a second discovery screen; all 45 cells are shown.
    for width_n in (15, 20, 25):
        for regime_window in (750, 1000, 1500):
            for percentile in (0.20, 0.25, 0.30, 0.35, 0.40):
                feats = {sym: engine.precompute(
                    b, percentile=percentile, width_n=width_n,
                    regime_window=regime_window) for sym, b in bars.items()}
                h2 = {"detector": "WIDTH20", "mode": "BLOCK"}
                result = rs.simulate_cell(bars, policy, feats, h2=h2)
                stat = rs.metrics(result, bars)
                rows.append({"width_n": width_n,
                             "regime_window": regime_window,
                             "percentile": percentile,
                             **stat,
                             "delta_total": stat["total"] - base["total"],
                             "delta_p_dd": stat["p_dd"] - base["p_dd"],
                             "delta_worst": (stat["worst_completed"]
                                             - base["worst_completed"]),
                             "delta_era1": (stat["era_2011_15"]
                                            - base["era_2011_15"]),
                             "delta_era2": (stat["era_2016_20"]
                                            - base["era_2016_20"]),
                             "delta_era3": (stat["era_2021_25"]
                                            - base["era_2021_25"])})
                print(f"WIDTH n={width_n:2} w={regime_window:4} "
                      f"q={percentile:.2f} d={stat['total']-base['total']:>+8,.0f} "
                      f"ratio={stat['p_dd']:>5.2f} "
                      f"worst={stat['worst_completed']:>7,.0f}", flush=True)
    surface = pd.DataFrame(rows)
    surface["strict_shape"] = (
        (surface["delta_total"] > 0)
        & (surface["delta_p_dd"] >= 0)
        & (surface["delta_worst"] >= 0)
        & (surface[["delta_era1", "delta_era2", "delta_era3"]] > 0).all(axis=1))
    surface.to_csv(HERE / "width_surface.csv", index=False)

    # Exact exposure and friction crossover for the profit-first H1 plateau.
    diag = []
    named = {"BASELINE": baseline}
    for horizon in (6, 12, 24, 48):
        named[f"H1_ATR_m0_h{horizon}"] = rs.simulate_cell(
            bars, policy, base_features,
            h1={"kind": "ATR", "m": 0.0, "horizon": horizon})
    for name, result in named.items():
        stat = rs.metrics(result, bars)
        maximum, average = max_exposure(result)
        gross = rs.metrics(result, bars, 0.0)["total"]
        diag.append({"cell": name, **stat, "gross": gross,
                     "max_exposure": maximum, "average_exposure": average})
    diagnostics = pd.DataFrame(diag)
    b = diagnostics.iloc[0]
    diagnostics["equal_dd_baseline_net"] = (
        base["total"] * diagnostics["max_dd"].abs() / abs(base["max_dd"]))
    diagnostics["extra_contracts"] = diagnostics["contracts"] - b["contracts"]
    diagnostics["friction_crossover"] = np.where(
        diagnostics["extra_contracts"] > 0,
        3.5 + (diagnostics["total"] - b["total"])
        / diagnostics["extra_contracts"], np.nan)
    diagnostics.to_csv(HERE / "h1_diagnostics.csv", index=False)

    print("\nWIDTH surface summary")
    print(surface.groupby("percentile").agg(
        cells=("delta_total", "size"), positive=("delta_total", lambda x: int((x > 0).sum())),
        mean_delta=("delta_total", "mean"), min_delta=("delta_total", "min"),
        max_delta=("delta_total", "max"), strict=("strict_shape", "sum")))
    print("\nH1 diagnostics")
    print(diagnostics[["cell", "total", "max_dd", "p_dd", "worst_completed",
                       "contracts", "max_exposure", "average_exposure",
                       "equal_dd_baseline_net", "friction_crossover"]].to_string(index=False))


if __name__ == "__main__":
    main()
