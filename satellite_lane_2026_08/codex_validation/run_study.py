"""Run the frozen corrected day-satellite verification and tuning study."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "reentry_regime_2026_07"))
sys.path.insert(0, str(ROOT / "strategy_extension_2026_07"))
import engine_v as ev  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402
from rotation import monday_of_week  # noqa: E402
import strategy  # noqa: E402

from engine import DayCell, exposure_summary, simulate_day  # noqa: E402

rg = ev.rg
START = pd.Timestamp("2011-01-01", tz="UTC")
END = pd.Timestamp("2026-01-01", tz="UTC")
DISCOVERY_END_YEAR = 2017
FRICTIONS = (0.0, 3.5, 7.0, 14.0)


def load_context():
    bars, daily, feats = {}, {}, {}
    for sym in ("NQ", "ES", "YM"):
        bars[sym] = rg.load_60m(sym)
        daily[sym] = bars[sym]["close"].resample("1D").last().dropna()
        sf = strategy.signal_frame(bars[sym], rg.SCFG)
        feats[sym] = (sf["atr"].to_numpy(), sf["vol_ok"].to_numpy())
    rg.START = START
    weights = rg.weekly_weights(
        daily, 35, "aggressive300", min(x.index.min() for x in bars.values()),
        max(x.index.max() for x in bars.values()))
    bench = {sym: benched_mask(sym, bars[sym].index, weights) for sym in bars}
    return bars, weights, feats, bench


def benched_mask(sym, index, weights):
    out = np.zeros(len(index), dtype=bool)
    for i, stamp in enumerate(index):
        mon = monday_of_week(stamp)
        if mon not in weights.index:
            continue
        row = weights.loc[mon]
        out[i] = int(row[sym]) == 0 and sum(int(row[s]) == 0 for s in rg.MULT) < 3
    return out


def core_trades(bars, weights):
    out = []
    for sym in bars:
        def qty(stamp, s=sym):
            mon = monday_of_week(stamp)
            return int(weights.loc[mon, s]) if mon in weights.index else 1
        tr = rg.simulate_weighted(bars[sym], sym, qty)
        tr = tr[tr["qty"] > 0].copy()
        tr["dir"] = 1
        out.append(tr)
    return pd.concat(out, ignore_index=True)


def run_cell(cell, bars, feats, bench):
    out = []
    for sym in bars:
        atr, vol_ok = feats[sym]
        out.append(simulate_day(
            bars[sym], sym, atr, vol_ok, bench[sym], cell, START))
    result = pd.concat(out, ignore_index=True)
    return result.sort_values(["entry_time", "sym"]).reset_index(drop=True)


def pnl_series(trades, bars, friction):
    if trades.empty:
        return pd.Series(dtype=float)
    return hourly_mtm(trades, bars, friction).loc[lambda x: (x.index >= START) & (x.index < END)]


def period_metrics(pnl, lo_year, hi_year):
    p = pnl[(pnl.index.year >= lo_year) & (pnl.index.year <= hi_year)]
    eq = p.cumsum()
    dd = eq - eq.cummax().clip(lower=0.0)
    yr = p.groupby(p.index.year).sum()
    total = float(p.sum())
    max_dd = float(dd.min()) if len(dd) else 0.0
    return {"total": total, "max_dd": max_dd,
            "ratio": total / abs(max_dd) if max_dd < 0 else np.inf,
            "worst": float(yr.min()) if len(yr) else 0.0,
            "worst_year": int(yr.idxmin()) if len(yr) else None}


def combined_row(cell, sat, core_pnl, bars, friction=3.5):
    sat_pnl = pnl_series(sat, bars, friction)
    primary_trades = sat[(sat["exit_time"] >= START)
                         & (sat["exit_time"] < END)]
    combined = core_pnl.add(sat_pnl, fill_value=0).sort_index()
    base_d = period_metrics(core_pnl, 2011, 2017)
    comb_d = period_metrics(combined, 2011, 2017)
    base_v = period_metrics(core_pnl, 2018, 2025)
    comb_v = period_metrics(combined, 2018, 2025)
    base_f = period_metrics(core_pnl, 2011, 2025)
    comb_f = period_metrics(combined, 2011, 2025)
    return {
        "cell": cell.name, "start_hour": cell.start_hour,
        "close_hour": cell.close_hour, "trail_k": cell.trail_k,
        "prior_bars": cell.prior_bars, "buffer_atr": cell.buffer_atr,
        "trades": len(primary_trades), "sat_total": float(sat_pnl.sum()),
        "d_train": comb_d["total"] - base_d["total"],
        "d_valid": comb_v["total"] - base_v["total"],
        "d_full": comb_f["total"] - base_f["total"],
        "combined_total": comb_f["total"],
        "combined_dd": comb_f["max_dd"],
        "combined_ratio": comb_f["ratio"],
        "combined_worst": comb_f["worst"],
        "combined_worst_year": comb_f["worst_year"],
        "base_train": base_d["total"], "base_valid": base_v["total"],
        "base_total": base_f["total"], "base_dd": base_f["max_dd"],
        "base_ratio": base_f["ratio"], "base_worst": base_f["worst"],
    }, sat_pnl, combined


def choose(rows):
    frame = pd.DataFrame(rows)
    eligible = frame[(frame.d_train > 0)
                     & (frame.combined_worst >= frame.base_worst - 500)].copy()
    if eligible.empty:
        eligible = frame.copy()
    return eligible.sort_values(
        ["d_train", "combined_ratio", "trades"],
        ascending=[False, False, True]).iloc[0]


def block_bootstrap_probability(pnl, seed=20260803, block=20, draws=20000):
    daily = pnl.groupby(pnl.index.normalize()).sum().sort_index()
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D", tz="UTC")
    values = daily.reindex(full, fill_value=0.0).to_numpy()
    rng = np.random.default_rng(seed)
    n = len(values)
    totals = np.empty(draws)
    for d in range(draws):
        starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
        sample = np.concatenate([values[s:s + block] for s in starts])[:n]
        totals[d] = sample.sum()
    return {"p_positive": float(np.mean(totals > 0)),
            "ci_025": float(np.quantile(totals, 0.025)),
            "ci_975": float(np.quantile(totals, 0.975))}


def main():
    print("loading roll-aware data and fixed core...", flush=True)
    bars, weights, feats, bench = load_context()
    core = core_trades(bars, weights)
    core_pnl = pnl_series(core, bars, rg.FR)
    base = period_metrics(core_pnl, 2011, 2025)
    print("corrected MTM core", base, flush=True)

    # Closest corrected implementation of Claude's nominal configuration.
    claimed_cell = DayCell(9, 17, 2.0, 2, 0.0)
    claimed_sat = run_cell(claimed_cell, bars, feats, bench)
    claimed_row, _, _ = combined_row(claimed_cell, claimed_sat, core_pnl, bars)
    print("corrected nominal", claimed_row, flush=True)

    phase_a_cells = [
        DayCell(start, close, k, 2, 0.0)
        for start in (8, 9, 10)
        for close in (15, 16, 17)
        if close - start >= 3
        for k in (1.0, 1.5, 2.0, 2.5, 3.0)
    ]
    rows_a, trades_by_name = [], {}
    for n, cell in enumerate(phase_a_cells, 1):
        sat = run_cell(cell, bars, feats, bench)
        row, _, _ = combined_row(cell, sat, core_pnl, bars)
        rows_a.append(row); trades_by_name[cell.name] = sat
        print(f"A {n:02}/{len(phase_a_cells)} {cell.name} "
              f"train {row['d_train']:+.0f} valid {row['d_valid']:+.0f} "
              f"full {row['d_full']:+.0f}", flush=True)
    pick_a = choose(rows_a)
    print("phase A discovery pick", pick_a.to_dict(), flush=True)

    phase_b_cells = [
        DayCell(int(pick_a.start_hour), int(pick_a.close_hour),
                float(pick_a.trail_k), prior, buffer)
        for prior in (1, 2, 3, 4)
        for buffer in (0.0, 0.10, 0.25, 0.50)
    ]
    rows_b = []
    for n, cell in enumerate(phase_b_cells, 1):
        sat = trades_by_name.get(cell.name)
        if sat is None:
            sat = run_cell(cell, bars, feats, bench)
            trades_by_name[cell.name] = sat
        row, _, _ = combined_row(cell, sat, core_pnl, bars)
        rows_b.append(row)
        print(f"B {n:02}/{len(phase_b_cells)} {cell.name} "
              f"train {row['d_train']:+.0f} valid {row['d_valid']:+.0f} "
              f"full {row['d_full']:+.0f}", flush=True)
    pick_b = choose(rows_b)
    locked_name = str(pick_b.cell)
    locked_sat = trades_by_name[locked_name]
    locked_cell = DayCell(int(pick_b.start_hour), int(pick_b.close_hour),
                          float(pick_b.trail_k), int(pick_b.prior_bars),
                          float(pick_b.buffer_atr))
    locked_row, locked_pnl, combined = combined_row(
        locked_cell, locked_sat, core_pnl, bars)

    # Friction sensitivity and diagnostics.
    cost_rows = []
    for fr in FRICTIONS:
        row, satp, _ = combined_row(locked_cell, locked_sat, core_pnl, bars, fr)
        cost_rows.append({"friction": fr, "sat_total": float(satp.sum()),
                          "d_train": row["d_train"], "d_valid": row["d_valid"],
                          "d_full": row["d_full"]})
    gross = next(x["sat_total"] for x in cost_rows if x["friction"] == 0)
    locked_primary = locked_sat[(locked_sat.exit_time >= START)
                                & (locked_sat.exit_time < END)].copy()
    crossover = gross / len(locked_primary) if len(locked_primary) else 0.0

    valid_sat_pnl = locked_pnl[(locked_pnl.index.year >= 2018)
                               & (locked_pnl.index.year <= 2025)]
    bootstrap = block_bootstrap_probability(valid_sat_pnl)
    ex = exposure_summary(core[core.entry_time < END], locked_primary)

    overlap = []
    for tr in locked_primary.itertuples():
        live = core[(core.entry_time <= tr.entry_time) & (core.exit_time > tr.entry_time)]
        overlap.append(int(live.loc[live.sym == tr.sym, "qty"].sum()))
    locked_diag = locked_primary.copy()
    locked_diag["same_symbol_core_qty"] = overlap
    locked_diag["pnl"] = (locked_diag.points * locked_diag.sym.map(rg.MULT) - rg.FR)
    locked_diag.to_csv(HERE / "locked_trades.csv", index=False)
    yearly = locked_pnl.groupby(locked_pnl.index.year).sum().rename("sat_pnl")
    yearly.to_csv(HERE / "locked_yearly.csv")

    pd.DataFrame(rows_a).to_csv(HERE / "phase_a.csv", index=False)
    pd.DataFrame(rows_b).to_csv(HERE / "phase_b.csv", index=False)
    pd.DataFrame(cost_rows).to_csv(HERE / "cost_stress.csv", index=False)
    summary = {
        "corrected_core": base,
        "corrected_nominal": claimed_row,
        "phase_a_pick": pick_a.to_dict(),
        "locked": locked_row,
        "cost_crossover_per_rt": crossover,
        "cost_stress": cost_rows,
        "bootstrap_validation": bootstrap,
        "exposure": ex,
        "same_symbol_overlap_trades": int((locked_diag.same_symbol_core_qty > 0).sum()),
        "same_symbol_overlap_pnl": float(locked_diag.loc[
            locked_diag.same_symbol_core_qty > 0, "pnl"].sum()),
        "by_symbol": locked_diag.groupby("sym")["pnl"].agg(
            ["count", "sum", "mean"]).to_dict(orient="index"),
        "by_reason": locked_diag.groupby("reason")["pnl"].agg(
            ["count", "sum", "mean"]).to_dict(orient="index"),
    }
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print("LOCKED", json.dumps(summary, indent=2, default=float), flush=True)


if __name__ == "__main__":
    main()
