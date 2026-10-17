"""Pass IV: online expert aggregation; robust controls run for survivors."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    HERE, SYMS, load_bars, score_trade_sets, update_ledger,
)
from run_stage_a import simulate_direction


EXPERTS = (
    "production_fixed1",
    "kalman_L120",
    "bocpd_H240",
    "cusum_h3",
    "poly_h48",
    "haar_32_128_512",
    "vr_geomean",
    "semivar_c1",
    "var_ridge10",
    "barrier_target2",
    "wonham_p60_p40",
    "posterior_trail_k4",
)


def load_expert_artifacts() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    daily_parts = [
        pd.read_parquet(HERE / "pass1_daily_pnl.parquet"),
        pd.read_parquet(HERE / "pass2_daily_pnl.parquet"),
        pd.read_parquet(HERE / "pass3_daily_pnl.parquet"),
    ]
    daily = pd.concat(daily_parts, axis=1)
    daily = daily.loc[:, ~daily.columns.duplicated()].reindex(columns=EXPERTS)
    daily = daily.fillna(0.0).sort_index()

    trade_parts = [
        pd.read_parquet(HERE / "pass1_trades.parquet"),
        pd.read_parquet(HERE / "pass2_trades.parquet"),
        pd.read_parquet(HERE / "pass3_trades.parquet"),
    ]
    combined = pd.concat(trade_parts)
    trade_sets = {}
    for expert in EXPERTS:
        rows = combined.xs(expert, level="candidate").reset_index(drop=True)
        trade_sets[expert] = rows
    return daily, trade_sets


def hedge_weights(daily: pd.DataFrame, eta: float) -> pd.DataFrame:
    """Beginning-of-day weights; today's reward updates tomorrow only."""
    prior_vol = daily.ewm(halflife=63, adjust=False,
                          min_periods=63).std().shift(1)
    log_weight = np.zeros(daily.shape[1], dtype=float)
    output = np.zeros(daily.shape, dtype=float)
    for i in range(len(daily)):
        stable = log_weight - log_weight.max()
        weight = np.exp(stable)
        output[i] = weight / weight.sum()
        reward = np.divide(
            daily.iloc[i].to_numpy(dtype=float),
            prior_vol.iloc[i].to_numpy(dtype=float),
            out=np.zeros(daily.shape[1], dtype=float),
            where=prior_vol.iloc[i].to_numpy(dtype=float) > 0,
        )
        log_weight += eta * reward
    return pd.DataFrame(output, index=daily.index, columns=daily.columns)


def holding_state(index: pd.DatetimeIndex,
                  trades: pd.DataFrame) -> np.ndarray:
    differences = np.zeros(len(index) + 1, dtype=np.int32)
    for trade in trades.itertuples(index=False):
        start = index.searchsorted(trade.entry_time, side="left")
        end = index.searchsorted(trade.exit_time, side="left")
        if start < len(index):
            differences[start] += 1
            differences[min(end, len(index))] -= 1
    return np.cumsum(differences[:-1]) > 0


def majority_direction(bars: pd.DataFrame, symbol: str,
                       trades: dict[str, pd.DataFrame],
                       weights: pd.DataFrame) -> pd.Series:
    index = bars.index
    states = np.column_stack([
        holding_state(index, trades[expert][trades[expert]["sym"] == symbol])
        for expert in EXPERTS
    ])
    by_day = weights.reindex(index.normalize()).set_axis(index).fillna(1 / len(EXPERTS))
    vote = (states * by_day.to_numpy(dtype=float)).sum(axis=1)
    return pd.Series((vote > 0.5).astype(np.int8), index=index)


def run() -> pd.DataFrame:
    bars = load_bars()
    expert_daily, expert_trades = load_expert_artifacts()
    trade_sets: dict[str, pd.DataFrame] = {}
    for eta, name in ((.05, "hedge_eta005"), (.20, "hedge_eta020")):
        weights = hedge_weights(expert_daily, eta)
        weights.to_parquet(HERE / f"{name}_weights.parquet")
        trade_sets[name] = pd.concat([
            simulate_direction(
                bars[symbol], symbol,
                majority_direction(bars[symbol], symbol,
                                   expert_trades, weights))
            for symbol in SYMS
        ], ignore_index=True)
    results, daily = score_trade_sets(bars, trade_sets)
    results.to_csv(HERE / "pass4_results.csv", index=False)
    daily.to_parquet(HERE / "pass4_daily_pnl.parquet")
    pd.concat(trade_sets, names=["candidate", "row"]).to_parquet(
        HERE / "pass4_trades.parquet")
    update_ledger("IV", "pass4_results.csv")
    # No independent Phase I-III cell passed every advancement gate.  The
    # survivor-only Kelly/CVaR cells are therefore explicitly inapplicable;
    # running them on a failed cell would violate the frozen protocol.
    ledger = pd.read_csv(HERE / "EXPERIMENT_LEDGER.csv", dtype=str).fillna("")
    no_survivor = ledger["family"].isin(("robust_kelly", "cvar_control"))
    ledger.loc[no_survivor, "status"] = "NOT_APPLICABLE"
    ledger.loc[no_survivor, "result_artifact"] = "pass4_results.csv"
    ledger.loc[no_survivor, "notes"] += "; no Pass I-III independent survivor"
    ledger.to_csv(HERE / "EXPERIMENT_LEDGER.csv", index=False)
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
