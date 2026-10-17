"""Final attribution, tail-risk and multiple-look inference for Phase 2."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import HERE, PRODUCTION, SYMS, load_bars
from run_pass8 import state_probability
import rotation_grid as rg
import strategy


RNG_SEED = 20260722
BOOTSTRAPS = 5000


def circular_totals(values: np.ndarray, block: int,
                    repetitions: int = BOOTSTRAPS) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.ndim == 1:
        values = values[:, None]
    n, columns = values.shape
    extended = np.vstack((values, values[:block]))
    cumulative = np.vstack((np.zeros((1, columns)), np.cumsum(extended, axis=0)))
    full_sums = cumulative[block:block + n] - cumulative[:n]
    count, remainder = divmod(n, block)
    partial = (cumulative[remainder:remainder + n] - cumulative[:n]
               if remainder else None)
    rng = np.random.default_rng(RNG_SEED + block)
    output = np.empty((repetitions, columns))
    for start in range(0, repetitions, 250):
        stop = min(start + 250, repetitions)
        choices = rng.integers(0, n, size=(stop - start, count))
        totals = full_sums[choices].sum(axis=1)
        if remainder:
            last = rng.integers(0, n, size=stop - start)
            totals += partial[last]
        output[start:stop] = totals
    return output.squeeze()


def paired_bootstrap(delta: pd.Series, block: int) -> dict:
    totals = circular_totals(delta.to_numpy(dtype=float), block)
    return {
        "block": block,
        "observed": float(delta.sum()),
        "p_positive": float((np.count_nonzero(totals <= 0) + 1)
                            / (len(totals) + 1)),
        "ci_low": float(np.quantile(totals, .025)),
        "ci_high": float(np.quantile(totals, .975)),
    }


def candidate_deltas() -> pd.DataFrame:
    parts = []
    base_fixed = pd.read_parquet(HERE / "pass1_daily_pnl.parquet")[
        "production_fixed1"]
    for pass_number in (1, 2, 3, 5):
        frame = pd.read_parquet(HERE / f"pass{pass_number}_daily_pnl.parquet")
        baseline = frame["production_fixed1"]
        parts.extend((f"p{pass_number}_{column}", frame[column] - baseline)
                     for column in frame if column != "production_fixed1")
    for pass_number in (4, 6):
        frame = pd.read_parquet(HERE / f"pass{pass_number}_daily_pnl.parquet")
        parts.extend((f"p{pass_number}_{column}", frame[column] - base_fixed)
                     for column in frame)
    pass7 = pd.read_parquet(HERE / "pass7_daily_pnl.parquet")
    base_cap = pass7["production_k2_cap3"]
    parts.extend((f"p7_{column}", pass7[column] - base_cap)
                 for column in pass7 if column != "production_k2_cap3")
    pass8 = pd.read_parquet(HERE / "pass8_daily_pnl.parquet")
    parts.extend((f"p8_{column}", pass8[column] - pass8["static_k2"])
                 for column in pass8 if column != "static_k2")
    pass9 = pd.read_parquet(HERE / "pass9_daily_pnl.parquet")
    parts.extend((f"p9_{column}", pass9[column] - base_cap)
                 for column in pass9)
    pass10 = pd.read_parquet(HERE / "pass10_daily_pnl.parquet")
    parts.extend((f"p10_{column}", pass10[column] - base_cap)
                 for column in pass10)

    phase1 = pd.read_parquet(
        HERE.parent / "quant_replacement_2026_07/stage_c_daily_pnl.parquet")
    for suffix in ("entry_bound", "cap3", "cap6"):
        baseline = phase1[f"donchian_{suffix}"]
        for prefix in ("ridge10_mean", "ridge100_mean",
                       "ridge10_t05", "ridge100_t05"):
            name = f"{prefix}_{suffix}"
            parts.append((f"phase1_{name}", phase1[name] - baseline))
    return pd.concat({name: series for name, series in parts}, axis=1).fillna(0.0)


def max_t_adjustment(deltas: pd.DataFrame, target: str,
                     block: int) -> dict:
    values = deltas.to_numpy(dtype=float)
    n = len(values)
    means = values.mean(axis=0)
    scales = values.std(axis=0, ddof=1)
    usable = scales > 0
    observed_t = np.zeros(len(scales))
    observed_t[usable] = np.sqrt(n) * means[usable] / scales[usable]
    centered = values - means
    totals = circular_totals(centered, block)
    if totals.ndim == 1:
        totals = totals[:, None]
    bootstrap_t = np.zeros_like(totals)
    bootstrap_t[:, usable] = totals[:, usable] / np.sqrt(n) / scales[usable]
    maxima = bootstrap_t.max(axis=1)
    location = deltas.columns.get_loc(target)
    p_value = (np.count_nonzero(maxima >= observed_t[location]) + 1) / (
        len(maxima) + 1)
    return {"block": block, "looks": int(deltas.shape[1]),
            "target_t": float(observed_t[location]),
            "familywise_p": float(p_value),
            "null_max_t_95": float(np.quantile(maxima, .95))}


def trade_risk(trades: pd.DataFrame, bars, mode: str) -> pd.DataFrame:
    frames = {symbol: strategy.signal_frame(bars[symbol], PRODUCTION)
              for symbol in SYMS}
    probabilities = ({symbol: state_probability(bars[symbol], .10, 240)
                      for symbol in SYMS} if mode != "production" else {})
    rows = []
    for trade in trades.itertuples(index=False):
        index = bars[trade.sym].index
        entry_i = index.get_loc(trade.entry_time)
        signal_i = entry_i - 1
        if mode != "production":
            p = float(probabilities[trade.sym].iloc[signal_i])
            strength = np.clip(2.0 * p - 1.0, 0.0, 1.0)
            trail = (2.0 + 18.0 * strength if mode == "dynamic_k20"
                     else (15.0 if strength > 0 else 2.0))
        else:
            p, strength, trail = np.nan, np.nan, 2.0
        atr = float(frames[trade.sym]["atr"].iloc[signal_i])
        risk = trail * atr * rg.MULT[trade.sym] * trade.qty
        rows.append({"entry_time": trade.entry_time,
                     "exit_time": trade.exit_time, "sym": trade.sym,
                     "qty": trade.qty, "p_bull": p, "strength": strength,
                     "trail_k": trail, "initial_stop_risk": risk})
    return pd.DataFrame(rows)


def maximum_portfolio_risk(risk: pd.DataFrame) -> float:
    events = []
    for row in risk.itertuples(index=False):
        events.extend(((row.entry_time, 1, row.initial_stop_risk),
                       (row.exit_time, 0, -row.initial_stop_risk)))
    frame = pd.DataFrame(events, columns=("time", "order", "change"))
    return float(frame.sort_values(["time", "order"])["change"].cumsum().max())


def daily_tail(path: pd.Series) -> dict:
    path = path.sort_index().fillna(0.0)
    equity = path.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    cutoff = path.quantile(.05)
    return {"worst_day": float(path.min()),
            "cvar_95": float(path[path <= cutoff].mean()),
            "max_dd": float(drawdown.min()),
            "max_dd_date": str(drawdown.idxmin().date())}


def realized_by_symbol(trades: pd.DataFrame) -> pd.Series:
    values = ((trades["points"] * trades["sym"].map(rg.MULT) - 3.50)
              * trades["qty"])
    return values.groupby(trades["sym"]).sum()


def run() -> None:
    pass7_results = pd.read_csv(HERE / "pass7_results.csv").set_index("candidate")
    pass9_results = pd.read_csv(HERE / "pass9_results.csv").set_index("candidate")
    baseline = pass7_results.loc["production_k2_cap3"]
    candidate = pass9_results.loc["base_dynamic_k20"]
    raw_profit = pass9_results.loc["base_step_k15"]
    ridge = pass7_results.loc["ridge_k2_cap3"]
    pass10_results = pd.read_csv(HERE / "pass10_results.csv").set_index(
        "candidate")
    risk_translated = pass10_results.loc["step_k15_cap12000"]

    scoreboard = pd.DataFrame({
        "production_k2_cap3": baseline,
        "ridge_k2_cap3": ridge,
        "posterior_dynamic_k20_cap3": candidate,
        "posterior_step_k15_cap3": raw_profit,
        "posterior_step_k15_risk12k": risk_translated,
    }).T
    scoreboard["profit_to_drawdown"] = scoreboard["net"] / -scoreboard["maxDD"]
    scoreboard["net_vs_production"] = scoreboard["net"] - baseline["net"]
    scoreboard["net_vs_production_pct"] = (
        scoreboard["net"] / baseline["net"] - 1.0) * 100.0
    scoreboard.to_csv(HERE / "FINAL_SCOREBOARD.csv")

    daily7 = pd.read_parquet(HERE / "pass7_daily_pnl.parquet")
    daily9 = pd.read_parquet(HERE / "pass9_daily_pnl.parquet")
    daily10 = pd.read_parquet(HERE / "pass10_daily_pnl.parquet")
    comparison = pd.concat({
        "production": daily7["production_k2_cap3"],
        "dynamic_k20": daily9["base_dynamic_k20"],
        "step_k15": daily9["base_step_k15"],
        "step_k15_risk12k": daily10["step_k15_cap12000"],
    }, axis=1).fillna(0.0)
    delta = comparison["dynamic_k20"] - comparison["production"]
    step_delta = comparison["step_k15"] - comparison["production"]
    translated_delta = comparison["step_k15_risk12k"] - comparison["production"]
    bootstrap_rows = []
    for name, difference in (("dynamic_k20", delta),
                             ("step_k15", step_delta),
                             ("step_k15_risk12k", translated_delta)):
        for period, sample in (("full", difference),
                               ("through_2025", difference[
                                   difference.index.year < 2026])):
            for block in (63, 252):
                bootstrap_rows.append({"candidate": name, "period": period,
                                       **paired_bootstrap(sample, block)})
    bootstrap = pd.DataFrame(bootstrap_rows)
    bootstrap.to_csv(HERE / "candidate_bootstrap.csv", index=False)

    deltas = candidate_deltas()
    adjustment_rows = []
    for target in ("p9_base_dynamic_k20", "p9_base_step_k15",
                   "p10_step_k15_cap12000"):
        for period, sample in (("full", deltas),
                               ("through_2025", deltas[
                                   deltas.index.year < 2026])):
            for block in (63, 252):
                adjustment_rows.append({"target": target, "period": period,
                                        **max_t_adjustment(sample, target, block)})
    adjustments = pd.DataFrame(adjustment_rows)
    adjustments.to_csv(HERE / "multiple_testing.csv", index=False)

    years = comparison.groupby(comparison.index.year).sum()
    years["dynamic_minus_production"] = (
        years["dynamic_k20"] - years["production"])
    years["step_minus_production"] = years["step_k15"] - years["production"]
    years["translated_minus_production"] = (
        years["step_k15_risk12k"] - years["production"])
    years.to_csv(HERE / "candidate_years.csv")

    bars = load_bars()
    trades7 = pd.read_parquet(HERE / "pass7_trades.parquet")
    trades9 = pd.read_parquet(HERE / "pass9_trades.parquet")
    baseline_trades = trades7.xs("production_k2_cap3", level="candidate")
    candidate_trades = trades9.xs("base_dynamic_k20", level="candidate")
    step_trades = trades9.xs("base_step_k15", level="candidate")
    trades10 = pd.read_parquet(HERE / "pass10_trades.parquet")
    translated_trades = trades10.xs(
        "step_k15_cap12000", level="candidate")
    baseline_risk = trade_risk(baseline_trades, bars, "production")
    candidate_risk = trade_risk(candidate_trades, bars, "dynamic_k20")
    step_risk = trade_risk(step_trades, bars, "step_k15")
    translated_risk = trade_risk(translated_trades, bars, "step_k15")
    baseline_risk.to_parquet(HERE / "production_initial_risk.parquet")
    candidate_risk.to_parquet(HERE / "candidate_initial_risk.parquet")
    step_risk.to_parquet(HERE / "step_initial_risk.parquet")
    translated_risk.to_parquet(HERE / "translated_initial_risk.parquet")

    gross_baseline = float(baseline["net"] + 3.5 * baseline["contracts"])
    gross_candidate = float(candidate["net"] + 3.5 * candidate["contracts"])
    risk_summary = {
        "gross_baseline": gross_baseline,
        "gross_candidate": gross_candidate,
        "gross_improvement": gross_candidate - gross_baseline,
        "net_improvement": float(candidate["net"] - baseline["net"]),
        "friction_savings_share": float(
            1.0 - (gross_candidate - gross_baseline)
            / (candidate["net"] - baseline["net"])),
        "candidate_trail_k_quantiles": {
            str(q): float(candidate_risk["trail_k"].quantile(q))
            for q in (0, .1, .25, .5, .75, .9, .99, 1.0)},
        "candidate_initial_risk_quantiles": {
            str(q): float(candidate_risk["initial_stop_risk"].quantile(q))
            for q in (.5, .9, .99, 1.0)},
        "baseline_max_portfolio_initial_risk": maximum_portfolio_risk(
            baseline_risk),
        "candidate_max_portfolio_initial_risk": maximum_portfolio_risk(
            candidate_risk),
        "step_max_portfolio_initial_risk": maximum_portfolio_risk(step_risk),
        "translated_max_portfolio_initial_risk": maximum_portfolio_risk(
            translated_risk),
        "step_initial_risk_quantiles": {
            str(q): float(step_risk["initial_stop_risk"].quantile(q))
            for q in (.5, .9, .99, 1.0)},
        "baseline_tail": daily_tail(comparison["production"]),
        "candidate_tail": daily_tail(comparison["dynamic_k20"]),
        "step_tail": daily_tail(comparison["step_k15"]),
        "translated_tail": daily_tail(comparison["step_k15_risk12k"]),
        "baseline_by_symbol": realized_by_symbol(baseline_trades).to_dict(),
        "candidate_by_symbol": realized_by_symbol(candidate_trades).to_dict(),
        "step_by_symbol": realized_by_symbol(step_trades).to_dict(),
        "translated_by_symbol": realized_by_symbol(translated_trades).to_dict(),
        "dynamic_improvement_2026_share": float(
            years.loc[2026, "dynamic_minus_production"] / delta.sum()),
        "step_improvement_2026_share": float(
            (years.loc[2026, "step_k15"] - years.loc[2026, "production"])
            / step_delta.sum()),
        "translated_improvement_2026_share": float(
            years.loc[2026, "translated_minus_production"]
            / translated_delta.sum()),
    }
    (HERE / "candidate_attribution.json").write_text(
        json.dumps(risk_summary, indent=2, default=float) + "\n")
    print(scoreboard[["net", "maxDD", "worst", "sharpe", "p14",
                      "profit_to_drawdown", "net_vs_production_pct"]])
    print("\nbootstrap\n", bootstrap)
    print("\nmultiple testing\n", adjustments)
    print("\nattribution\n", json.dumps(risk_summary, indent=2, default=float))


if __name__ == "__main__":
    run()
