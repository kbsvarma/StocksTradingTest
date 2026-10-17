"""Shared, research-only machinery for the frozen Phase-2 search."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [
    str(ROOT / "trend_bot"),
    str(ROOT / "trend_bot" / "research"),
    str(ROOT / "strategy_extension_2026_07"),
    str(ROOT / "adaptive_rotation_2026_07"),
    str(ROOT / "quant_replacement_2026_07"),
]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import PRODUCTION, START, SYMS  # noqa: E402
from run_stage_a import pnl_stats, simulate_direction  # noqa: E402


def load_bars() -> dict[str, pd.DataFrame]:
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    return {symbol: rg.load_60m(symbol) for symbol in SYMS}


def eligible(bars: pd.DataFrame) -> pd.Series:
    frame = strategy.signal_frame(bars, PRODUCTION)
    return frame["vol_ok"] & frame["session_ok"] & frame["atr"].notna()


def production_signal(bars: pd.DataFrame) -> pd.Series:
    return strategy.signal_frame(bars, PRODUCTION)["signal"].astype(np.int8)


def gated_first_passage(score: pd.Series, allowed: pd.Series,
                        boundary: float, reset: float) -> pd.Series:
    """One causal trigger per excursion; ineligible bars cannot consume it."""
    values = score.to_numpy(dtype=float)
    can_trade = allowed.reindex(score.index).fillna(False).to_numpy(dtype=bool)
    out = np.zeros(len(score), dtype=np.int8)
    armed = True
    for i, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not armed and value < reset:
            armed = True
        elif armed and can_trade[i] and value > boundary:
            out[i] = 1
            armed = False
    return pd.Series(out, index=score.index)


def run_directions(
    bars: dict[str, pd.DataFrame],
    directions: dict[str, dict[str, pd.Series]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """Run every named signal at fixed one contract per instrument."""
    names = tuple(directions[SYMS[0]])
    rows: list[dict] = []
    pnl_paths: dict[str, pd.Series] = {}
    trade_sets: dict[str, pd.DataFrame] = {}
    for name in names:
        trades = pd.concat([
            simulate_direction(bars[symbol], symbol,
                               directions[symbol][name])
            for symbol in SYMS
        ]).sort_values("exit_time").reset_index(drop=True)
        pnl, stats = score_trades(trades, bars)
        rows.append({"candidate": name, **stats})
        pnl_paths[name] = pnl
        trade_sets[name] = trades
        print(name, stats, flush=True)
    results = pd.DataFrame(rows).sort_values("net", ascending=False)
    daily = pd.concat({
        name: pnl.groupby(pnl.index.normalize()).sum()
        for name, pnl in pnl_paths.items()
    }, axis=1).fillna(0.0)
    return results, daily, trade_sets


def score_trade_sets(
    bars: dict[str, pd.DataFrame], trade_sets: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    paths: dict[str, pd.Series] = {}
    for name, trades in trade_sets.items():
        trades = trades.sort_values("exit_time").reset_index(drop=True)
        trade_sets[name] = trades
        pnl, stats = score_trades(trades, bars)
        rows.append({"candidate": name, **stats})
        paths[name] = pnl
        print(name, stats, flush=True)
    results = pd.DataFrame(rows).sort_values("net", ascending=False)
    daily = pd.concat({
        name: pnl.groupby(pnl.index.normalize()).sum()
        for name, pnl in paths.items()
    }, axis=1).fillna(0.0)
    return results, daily


def score_trades(trades: pd.DataFrame, bars: dict[str, pd.DataFrame],
                 friction: float = 3.5) -> tuple[pd.Series, dict]:
    """Score a path, explicitly retaining registered zero-trade cells."""
    if not trades.empty:
        return pnl_stats(trades, bars, friction)
    index = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        index = index.union(bars[symbol].index)
    index = index[index >= START]
    pnl = pd.Series(0.0, index=index)
    first_year = int(index[0].year)
    return pnl, {
        "net": 0.0, "maxDD": 0.0, "worst": 0.0,
        "worst_year": first_year, "sharpe": np.nan,
        "p7": 0.0, "p14": 0.0, "train_2011_20": 0.0,
        "test_2021_end": 0.0, "y2011_15": 0.0, "y2016_20": 0.0,
        "y2021_end": 0.0, "contracts": 0, "trades": 0,
        "short_trades": 0, "max_core": 0,
    }


def update_ledger(pass_name: str, results_file: str) -> None:
    path = HERE / "EXPERIMENT_LEDGER.csv"
    ledger = pd.read_csv(path, dtype=str).fillna("")
    mask = ledger["pass"].eq(pass_name)
    ledger.loc[mask, "status"] = "RUN"
    ledger.loc[mask, "result_artifact"] = results_file
    ledger.to_csv(path, index=False)
