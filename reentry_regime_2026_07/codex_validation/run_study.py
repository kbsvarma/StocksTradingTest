"""Run the frozen corrected H1/H2 validation protocol."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "trend_bot"), str(ROOT / "trend_bot" / "research")]

import engine  # noqa: E402
import rotation_grid as rg  # noqa: E402
from rotation import monday_of_week  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


FRICTIONS = (3.5, 7.0, 14.0)


def configurations() -> list[tuple[str, dict | None, dict | None]]:
    cells: list[tuple[str, dict | None, dict | None]] = []
    for k in (5, 10, 15):
        for horizon in (6, 12, 24, 48):
            cells.append((f"H1_SC_k{k}_h{horizon}",
                          {"kind": "SC", "k": k, "horizon": horizon}, None))
    for horizon in (6, 12, 24, 48):
        cells.append((f"H1_RESET_h{horizon}",
                      {"kind": "RESET", "horizon": horizon}, None))
    for multiple in (0.0, 0.5, 1.0):
        for horizon in (6, 12, 24, 48):
            cells.append((f"H1_ATR_m{multiple:g}_h{horizon}",
                          {"kind": "ATR", "m": multiple,
                           "horizon": horizon}, None))
    for fraction in (0.50, 0.75, 1.00):
        for horizon in (12, 24):
            cells.append((f"H1_FRAC_r{fraction:g}_h{horizon}",
                          {"kind": "FRAC", "r": fraction,
                           "horizon": horizon}, None))
    for detector in ("ER20", "ADX14", "WIDTH20", "WHIP80"):
        for mode in ("BLOCK", "UNLOCK_SC", "UNLOCK_ATR", "TIGHT", "SCRAPE"):
            cells.append((f"H2_{detector}_{mode}", None,
                          {"detector": detector, "mode": mode}))
    assert len(cells) == 54
    return cells


def load_inputs():
    rg.START = START
    bars = {sym: rg.load_60m(sym) for sym in rg.MULT}
    daily = {sym: b["close"].resample("1D").last().dropna()
             for sym, b in bars.items()}
    policies = weekly_policy(daily, 35,
                             min(b.index.min() for b in bars.values()),
                             max(b.index.max() for b in bars.values()))
    policy = policies["baseline"]
    features = {sym: engine.precompute(b) for sym, b in bars.items()}
    return bars, policy, features


def weight_function(policy: pd.DataFrame, sym: str):
    def weight(stamp):
        monday = monday_of_week(stamp)
        return (int(policy.loc[monday, f"L_{sym}"])
                if monday in policy.index else 1)
    return weight


def gate_zero(bars, policy, features) -> pd.DataFrame:
    pieces = []
    for sym, b in bars.items():
        wq = weight_function(policy, sym)
        reference = rg.simulate_weighted(b, sym, wq)
        got = engine.simulate(b, sym, wq, features[sym])
        left = got[["entry_time", "exit_time", "entry_px", "exit_px",
                    "points", "reason", "qty"]].reset_index(drop=True)
        right = reference[["entry_time", "exit_time", "entry_px", "exit_px",
                           "points", "reason", "qty"]].reset_index(drop=True)
        if not left.equals(right):
            mismatch = next((i for i in range(min(len(left), len(right)))
                             if not left.iloc[i].equals(right.iloc[i])), None)
            raise AssertionError(
                f"{sym} baseline parity failed: got={len(left)} "
                f"ref={len(right)} first_mismatch={mismatch}")
        pieces.append(got)
        print(f"gate zero {sym}: {len(got):,} trades IDENTICAL", flush=True)
    return pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)


def simulate_cell(bars, policy, features, h1=None, h2=None):
    pieces = []
    for sym, b in bars.items():
        pieces.append(engine.simulate(
            b, sym, weight_function(policy, sym), features[sym], h1=h1, h2=h2))
    return pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)


def pnl_series(trades, bars, friction):
    return hourly_mtm(trades, bars, friction)


def metrics(trades, bars, friction=3.5):
    pnl = pnl_series(trades, bars, friction)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    yearly = pnl.groupby(pnl.index.year).sum()
    daily = pnl.groupby(pnl.index.normalize()).sum()
    daily_sd = daily.std(ddof=1)
    completed = yearly[yearly.index <= 2025]
    return {
        "total": float(pnl.sum()),
        "max_dd": float(drawdown.min()),
        "p_dd": float(pnl.sum() / abs(drawdown.min())),
        "sharpe": float(daily.mean() / daily_sd * np.sqrt(252))
                   if daily_sd > 0 else np.nan,
        "worst_completed": float(completed.min()),
        "worst_year": int(completed.idxmin()),
        "era_2011_15": float(pnl[pnl.index.year <= 2015].sum()),
        "era_2016_20": float(pnl[(pnl.index.year >= 2016)
                                  & (pnl.index.year <= 2020)].sum()),
        "era_2021_25": float(pnl[(pnl.index.year >= 2021)
                                  & (pnl.index.year <= 2025)].sum()),
        "ytd_2026": float(pnl[pnl.index.year == 2026].sum()),
        "trades": int(len(trades)),
        "contracts": int(trades["qty"].sum()),
        "fast_entries": int(trades["entry_kind"].str.startswith("H1").sum()),
    }


def weekly_returns(trades, bars, friction=3.5):
    pnl = pnl_series(trades, bars, friction)
    return pnl.resample("W-SUN").sum()


def bootstrap(differences: pd.DataFrame, seed=20260731, draws=4000,
              block=8):
    """Paired circular block bootstrap and max-t family-wise adjustment."""
    values = differences.to_numpy(dtype=float)
    n, m = values.shape
    rng = np.random.default_rng(seed)
    observed_mean = values.mean(axis=0)
    se = values.std(axis=0, ddof=1) / np.sqrt(n)
    observed_t = np.divide(observed_mean, se,
                           out=np.zeros_like(observed_mean), where=se > 0)
    centered = values - observed_mean
    boot_means = np.empty((draws, m), dtype=float)
    boot_tmax = np.empty(draws, dtype=float)
    blocks_needed = int(np.ceil(n / block))
    offsets = np.arange(block)
    for start in range(0, draws, 100):
        size = min(100, draws - start)
        starts = rng.integers(0, n, size=(size, blocks_needed))
        idx = ((starts[..., None] + offsets) % n).reshape(size, -1)[:, :n]
        sample = values[idx].mean(axis=1)
        sample_centered = centered[idx].mean(axis=1)
        boot_means[start:start + size] = sample
        bt = np.divide(sample_centered, se,
                       out=np.zeros_like(sample_centered), where=se > 0)
        boot_tmax[start:start + size] = bt.max(axis=1)
    lo, hi = np.quantile(boot_means * n, [0.025, 0.975], axis=0)
    adjusted_p = np.array([
        (1.0 + np.sum(boot_tmax >= t)) / (draws + 1.0)
        for t in observed_t
    ])
    return lo, hi, observed_t, adjusted_p


def causality_mutation_check(bars, policy):
    """Corrupt every future OHLC value and require past trades unchanged."""
    sym = "NQ"
    cutoff = pd.Timestamp("2020-01-01", tz="UTC")
    source = bars[sym]
    mutated = source.copy()
    mask = mutated.index >= cutoff
    # Mutate prices coherently but violently; preserve roll locations.
    for col in ("open", "high", "low", "close"):
        mutated.loc[mask, col] = mutated.loc[mask, col] * 7.0 + 12345.0
    h2 = {"detector": "WIDTH20", "mode": "UNLOCK_ATR"}
    original = engine.simulate(source, sym, weight_function(policy, sym),
                               engine.precompute(source), h2=h2)
    changed = engine.simulate(mutated, sym, weight_function(policy, sym),
                              engine.precompute(mutated), h2=h2)
    cols = ["entry_time", "exit_time", "entry_px", "exit_px", "points",
            "reason", "qty", "entry_kind"]
    a = original[original["exit_time"] < cutoff][cols].reset_index(drop=True)
    b = changed[changed["exit_time"] < cutoff][cols].reset_index(drop=True)
    if not a.equals(b):
        raise AssertionError("future-mutation causality check failed")
    print(f"causality mutation: {len(a):,} pre-cutoff NQ trades IDENTICAL",
          flush=True)


def main():
    bars, policy, features = load_inputs()
    print("data:", {s: (str(b.index.min()), str(b.index.max()),
                        int(b["roll"].sum())) for s, b in bars.items()},
          flush=True)
    baseline = gate_zero(bars, policy, features)
    causality_mutation_check(bars, policy)

    configs = configurations()
    trades = {"BASELINE": baseline}
    rows = []
    base_metrics = metrics(baseline, bars)
    rows.append({"cell": "BASELINE", "family": "baseline", **base_metrics})
    print("BASELINE", json.dumps(base_metrics, sort_keys=True), flush=True)

    for number, (name, h1, h2) in enumerate(configs, 1):
        result = simulate_cell(bars, policy, features, h1=h1, h2=h2)
        trades[name] = result
        stat = metrics(result, bars)
        stat["cell"] = name
        stat["family"] = "H1" if h1 else "H2"
        rows.append(stat)
        print(f"{number:02d}/54 {name:<29} net={stat['total']:>9,.0f} "
              f"d={stat['total']-base_metrics['total']:>+8,.0f} "
              f"dd={stat['max_dd']:>8,.0f} p/dd={stat['p_dd']:>5.2f} "
              f"worst={stat['worst_completed']:>7,.0f} "
              f"fast={stat['fast_entries']:>4}", flush=True)

    table = pd.DataFrame(rows)
    base = table.iloc[0]
    for col in ("total", "p_dd", "worst_completed", "era_2011_15",
                "era_2016_20", "era_2021_25"):
        table[f"delta_{col}"] = table[col] - float(base[col])

    # Friction stress and paired weekly differences.
    common_index = pd.date_range(
        min(b.index.min() for b in bars.values()).normalize(),
        max(b.index.max() for b in bars.values()).normalize(),
        freq="W-SUN", tz="UTC")
    weekly = {}
    for name, result in trades.items():
        weekly[name] = weekly_returns(result, bars).reindex(
            common_index, fill_value=0.0)
        for friction in FRICTIONS[1:]:
            table.loc[table["cell"] == name, f"total_cost_{friction:g}"] = (
                metrics(result, bars, friction)["total"])
    diff = pd.DataFrame({name: weekly[name] - weekly["BASELINE"]
                         for name, _, _ in configs})
    lo, hi, tstat, adjp = bootstrap(diff)
    boot = pd.DataFrame({"cell": diff.columns, "boot95_low": lo,
                         "boot95_high": hi, "paired_t": tstat,
                         "familywise_p": adjp})
    table = table.merge(boot, on="cell", how="left")

    table["strict_prebootstrap"] = (
        (table["delta_total"] > 0)
        & (table["delta_p_dd"] >= 0)
        & (table["delta_worst_completed"] >= 0)
        & (table["delta_era_2011_15"] > 0)
        & (table["delta_era_2016_20"] > 0)
        & (table["delta_era_2021_25"] > 0)
        & (table["total_cost_7"] > float(
            table.loc[table["cell"] == "BASELINE", "total_cost_7"].iloc[0])))
    table["strict_statistical"] = (
        table["strict_prebootstrap"]
        & (table["boot95_low"] > 0)
        & (table["familywise_p"] < 0.05))
    table["profit_first_10pct"] = (
        table["delta_total"] >= 0.10 * float(base["total"]))
    table.to_csv(HERE / "results.csv", index=False)

    # Save exact yearly MTM and entry-kind attribution for forensic review.
    yearly = {}
    attribution = []
    for name, result in trades.items():
        p = pnl_series(result, bars, 3.5)
        yearly[name] = p.groupby(p.index.year).sum()
        for kind, group in result.groupby("entry_kind"):
            realized = ((group["points"] * group["sym"].map(rg.MULT)
                         - rg.FR) * group["qty"])
            attribution.append({"cell": name, "entry_kind": kind,
                                "trades": len(group),
                                "contracts": int(group["qty"].sum()),
                                "realized_net": float(realized.sum())})
    pd.DataFrame(yearly).to_csv(HERE / "yearly.csv")
    pd.DataFrame(attribution).to_csv(HERE / "attribution.csv", index=False)

    summary = {
        "baseline": base_metrics,
        "cells": len(configs),
        "strict_prebootstrap": table.loc[
            table["strict_prebootstrap"], "cell"].tolist(),
        "strict_statistical": table.loc[
            table["strict_statistical"], "cell"].tolist(),
        "profit_first_10pct": table.loc[
            table["profit_first_10pct"], "cell"].tolist(),
        "best_net": table.sort_values("total", ascending=False).iloc[0][
            ["cell", "total", "max_dd", "p_dd", "worst_completed"]].to_dict(),
        "best_p_dd": table.sort_values("p_dd", ascending=False).iloc[0][
            ["cell", "total", "max_dd", "p_dd", "worst_completed"]].to_dict(),
    }
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2,
                                                   default=float) + "\n")
    print("SUMMARY", json.dumps(summary, indent=2, default=float), flush=True)


if __name__ == "__main__":
    main()
