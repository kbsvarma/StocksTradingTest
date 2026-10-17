"""Robustness study for the causal core-active breadth condition."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import run_study as rs
from engine import DayCell, exposure_summary
from run_policy_search import hourly_mtm

HERE = Path(__file__).resolve().parent


def core_exposure_path(core: pd.DataFrame) -> pd.Series:
    events = pd.concat([
        pd.Series(core.qty.to_numpy(), index=pd.DatetimeIndex(core.entry_time)),
        pd.Series(-core.qty.to_numpy(), index=pd.DatetimeIndex(core.exit_time)),
    ])
    return events.groupby(level=0).sum().sort_index().cumsum()


def core_qty_at_entry(sat: pd.DataFrame, exposure: pd.Series,
                      include_simultaneous: bool = False) -> np.ndarray:
    # State immediately BEFORE the entry timestamp: exclude a core entry with
    # the identical bar label, while retaining a core position whose stop may
    # execute later within that labelled bar.
    side = "right" if include_simultaneous else "left"
    loc = exposure.index.searchsorted(pd.DatetimeIndex(sat.entry_time), side=side) - 1
    return np.where(loc >= 0, exposure.to_numpy()[np.maximum(loc, 0)], 0).astype(int)


def before_hour(trades: pd.DataFrame, hour: int) -> pd.DataFrame:
    h = trades.entry_time.dt.tz_convert("America/New_York").dt.hour
    return trades[h < hour].copy()


def row_for(cell, cutoff, sat, core_pnl, bars, friction):
    row, sat_pnl, combined = rs.combined_row(cell, sat, core_pnl, bars, friction)
    row.update({"entry_before": cutoff, "friction": friction})
    row["y2026"] = 0.0
    if not sat.empty:
        all_pnl = hourly_mtm(sat, bars, friction)
        row["y2026"] = float(all_pnl[all_pnl.index.year == 2026].sum())
    return row, sat_pnl, combined


def main():
    include_simultaneous = "--pending" in sys.argv[1:]
    prefix = "pending_" if include_simultaneous else ""
    state_rule = ("core_active_or_entering_same_bar" if include_simultaneous
                  else "core_confirmed_before_entry_bar")
    print("loading fixed data/core", flush=True)
    bars, weights, feats, bench = rs.load_context()
    core = rs.core_trades(bars, weights)
    core_pnl = rs.pnl_series(core, bars, rs.rg.FR)
    core_exposure = core_exposure_path(core)

    cells = [DayCell(9, close, k, 2, buffer)
             for close in (15, 16, 17)
             for k in (1.5, 2.0, 2.5)
             for buffer in (0.0, 0.10, 0.25, 0.50)]
    rows, trade_map = [], {}
    for n, cell in enumerate(cells, 1):
        raw = rs.run_cell(cell, bars, feats, bench)
        active = raw[core_qty_at_entry(
            raw, core_exposure, include_simultaneous=include_simultaneous) > 0].copy()
        for cutoff in (13, 14, 15):
            sat = before_hour(active, cutoff)
            key = f"{cell.name}_eb{cutoff}"
            trade_map[key] = sat
            for friction in (3.5, 7.0):
                row, _, _ = row_for(cell, cutoff, sat, core_pnl, bars, friction)
                row["key"] = key
                rows.append(row)
        print(f"{n:02}/{len(cells)} {cell.name}", flush=True)

    frame = pd.DataFrame(rows)
    frame.to_csv(HERE / f"{prefix}followup_surface.csv", index=False)
    train7 = frame[(frame.friction == 7.0) & (frame.d_train > 0)
                   & (frame.combined_worst >= frame.base_worst - 500)].copy()
    if train7.empty:
        train7 = frame[frame.friction == 7.0].copy()
    pick = train7.sort_values(
        ["d_train", "combined_ratio", "trades"],
        ascending=[False, False, True]).iloc[0]
    key = str(pick.key)
    chosen = trade_map[key]
    chosen35 = frame[(frame.key == key) & (frame.friction == 3.5)].iloc[0]
    chosen7 = frame[(frame.key == key) & (frame.friction == 7.0)].iloc[0]

    # Cost crossover and validation bootstrap.
    cell = DayCell(int(pick.start_hour), int(pick.close_hour),
                   float(pick.trail_k), int(pick.prior_bars),
                   float(pick.buffer_atr))
    _, gross_pnl, _ = row_for(cell, int(pick.entry_before), chosen,
                              core_pnl, bars, 0.0)
    primary = chosen[(chosen.exit_time >= rs.START) & (chosen.exit_time < rs.END)]
    crossover = float(gross_pnl.sum() / len(primary))
    valid = rs.pnl_series(chosen, bars, 3.5)
    valid = valid[(valid.index.year >= 2018) & (valid.index.year <= 2025)]
    bootstrap = rs.block_bootstrap_probability(valid)

    # A transparent leverage scenario: the same signals at two contracts.
    doubled = chosen.copy(); doubled["qty"] = 2
    doubled_row, _, _ = row_for(cell, int(pick.entry_before), doubled,
                                core_pnl, bars, 3.5)
    exp1 = exposure_summary(core[core.entry_time < rs.END], primary)
    exp2 = exposure_summary(core[core.entry_time < rs.END],
                            doubled[(doubled.exit_time >= rs.START)
                                    & (doubled.exit_time < rs.END)])

    f35 = frame[frame.friction == 3.5]
    robustness = {
        "cells": int(len(f35)),
        "positive_both_halves": int(((f35.d_train > 0)
                                      & (f35.d_valid > 0)).sum()),
        "positive_full": int((f35.d_full > 0).sum()),
    }
    summary = {
        "state_rule": state_rule,
        "selected_on_2011_2017_at_7_cost": pick.to_dict(),
        "selected_at_3_5": chosen35.to_dict(),
        "selected_at_7": chosen7.to_dict(),
        "cost_crossover_per_rt": crossover,
        "bootstrap_validation_3_5": bootstrap,
        "available_2026_3_5": float(chosen35.y2026),
        "robustness": robustness,
        "exposure_1x": exp1,
        "doubled_sizing_3_5": doubled_row,
        "exposure_2x": exp2,
    }
    (HERE / f"{prefix}followup_summary.json").write_text(
        json.dumps(summary, indent=2, default=float))
    primary.to_csv(HERE / f"{prefix}followup_selected_trades.csv", index=False)
    print(json.dumps(summary, indent=2, default=float), flush=True)


if __name__ == "__main__":
    main()
