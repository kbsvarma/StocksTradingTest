"""Post-hoc interaction check: does WIDTH add anything to causal ridge gates?"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "ridge_volume_audit_2026_07")]

import engine  # noqa: E402
import audit_and_extend as audit  # noqa: E402


def main():
    audit.rg.START, audit.rg.SCFG = audit.START, dict(audit.PRODUCTION)
    bars = {symbol: audit.load_60m(symbol) for symbol in audit.SYMS}
    original = audit.feature_frames(bars)
    features, _ = audit.add_volume_features(bars, original)
    teachers = audit.extended_teachers(bars, features)
    policy, _ = audit.policy_and_base(bars)
    predictions = {
        "R0": audit.generic_predictions(
            bars, features, teachers, audit.FEATURES),
        "R2": audit.generic_predictions(
            bars, features, teachers, audit.PLAIN),
    }
    width = {symbol: engine.precompute(bars[symbol])["chop"]["WIDTH20"]
             for symbol in audit.SYMS}
    for parent in ("R0", "R2"):
        predictions[parent + "_WIDTH"] = {
            symbol: (predictions[parent][symbol].astype(bool)
                     & ~pd.Series(width[symbol], index=bars[symbol].index)).astype("int8")
            for symbol in audit.SYMS
        }

    rows, daily = [], {}
    for label, direction in predictions.items():
        trades = audit.simulate_variant(direction, policy, bars)
        pnl = audit.hourly_mtm(trades, bars, 3.5)
        stat = audit.path_stats(pnl, trades)
        stat.update(cell=label, net_full=float(pnl.sum()),
                    max_exposure=audit.exposure(trades))
        rows.append(stat)
        daily[label] = pnl.groupby(pnl.index.normalize()).sum()
        print(label, stat, flush=True)
    result = pd.DataFrame(rows).set_index("cell")
    for child, parent in (("R0_WIDTH", "R0"), ("R2_WIDTH", "R2")):
        for col in ("net_2011_25", "maxDD_hourly", "pdd_hourly",
                    "worst_year", "trades_2011_25", "contracts_2011_25",
                    "2011_15", "2016_20", "2021_25", "net_full"):
            result.loc[child, "delta_" + col] = (
                result.loc[child, col] - result.loc[parent, col])
        delta = pd.concat(daily, axis=1).fillna(0.0)[child].sub(
            pd.concat(daily, axis=1).fillna(0.0)[parent], fill_value=0.0)
        for block in (63, 252):
            boot = audit.moving_block_bootstrap(
                delta[delta.index <= audit.END_SELECTION], block)
            result.loc[child, f"boot{block}_p_nonpositive"] = boot["p_nonpositive"]
            result.loc[child, f"boot{block}_lower95"] = boot["lower_95"]
    result.to_csv(HERE / "ridge_composition.csv")
    print("\n", result.to_string())


if __name__ == "__main__":
    main()
