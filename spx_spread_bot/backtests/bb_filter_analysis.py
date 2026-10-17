"""
Bollinger Band Filter Backtest
==============================
Tests whether adding BB-based entry filters to the existing 13-year backtest
(daily_results.csv + trade_results.csv) improves risk-adjusted returns.

Filters tested
--------------
1. upper_band  — skip if SPX entry price >= 20-day upper BB (overbought day)
2. squeeze     — skip if BB width < threshold (compression; big move incoming)
3. combined    — both filters active simultaneously

BB data source: ten_year/spx_daily_ohlc_bb.csv  (pre-computed, see fetch script)

Usage
-----
    python backtests/bb_filter_analysis.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# ── paths ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent / "ten_year"
DAILY_CSV  = BASE / "daily_results.csv"
TRADES_CSV = BASE / "trade_results.csv"
BB_CSV     = BASE / "spx_daily_ohlc_bb.csv"

# ── squeeze thresholds to sweep ───────────────────────────────────────────────
SQUEEZE_THRESHOLDS = [0.02, 0.03, 0.04, 0.05]


# ── helpers ───────────────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = pd.read_csv(TRADES_CSV, parse_dates=["Date"])
    trades["date"] = trades["Date"].dt.normalize()

    bb = pd.read_csv(BB_CSV, index_col=0, parse_dates=True)
    bb.index = pd.to_datetime(bb.index).normalize()
    bb.index.name = "date"

    return trades, bb


def merge(trades: pd.DataFrame, bb: pd.DataFrame) -> pd.DataFrame:
    df = trades.merge(bb[["close", "bb_upper", "bb_lower", "bb_mid", "bb_width"]],
                      left_on="date", right_index=True, how="left")
    df["spx_entry"] = df["SPX price at entry"]
    return df


def metrics(df: pd.DataFrame, label: str) -> dict:
    n = len(df)
    if n == 0:
        return dict(label=label, trades=0, total_pnl=0, win_rate=0,
                    avg_pnl=0, max_dd=0, sharpe=0)

    pnls = df["Total PnL"].values
    wins = (df["Win/Loss"] == "Win").sum()
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - running_max
    max_dd = float(drawdowns.min())

    avg = float(pnls.mean())
    std = float(pnls.std(ddof=1)) if n > 1 else 1.0
    sharpe = (avg / std) * np.sqrt(252) if std > 0 else 0.0

    return dict(
        label=label,
        trades=n,
        total_pnl=float(pnls.sum()),
        win_rate=round(wins / n, 3),
        avg_pnl=round(avg, 2),
        max_dd=round(max_dd, 2),
        sharpe=round(sharpe, 3),
    )


def print_table(rows: list[dict]) -> None:
    hdr = f"{'Filter':<35} {'Trades':>7} {'Total PnL':>11} {'Win%':>6} {'Avg PnL':>8} {'Max DD':>10} {'Sharpe':>7}"
    sep = "-" * len(hdr)
    print(sep)
    print(hdr)
    print(sep)
    for r in rows:
        print(
            f"{r['label']:<35} {r['trades']:>7} "
            f"${r['total_pnl']:>10,.0f} {r['win_rate']*100:>5.1f}% "
            f"${r['avg_pnl']:>7,.0f} ${r['max_dd']:>9,.0f} {r['sharpe']:>7.3f}"
        )
    print(sep)


def analyse_filtered(df: pd.DataFrame, mask: pd.Series, label: str) -> tuple[dict, dict]:
    kept   = df[mask]
    skipped = df[~mask]
    return metrics(kept, label), metrics(skipped, f"  skipped by: {label}")


def position_in_band(df: pd.DataFrame) -> None:
    """Bucket trades by where SPX entry sits within the BB and show stats."""
    df = df.copy()
    df["pct_b"] = (df["spx_entry"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    df["pct_b"] = df["pct_b"].clip(0, 1)

    bins   = [0.0, 0.2, 0.4, 0.6, 0.8, 1.01]
    labels = ["0–20% (near lower)", "20–40%", "40–60% (mid)", "60–80%", "80–100% (near upper)"]
    df["band_zone"] = pd.cut(df["pct_b"], bins=bins, labels=labels, right=False)

    print("\n%B zone breakdown (where in the band was SPX at entry):")
    grp = df.groupby("band_zone", observed=True)["Total PnL"]
    zone_df = grp.agg(trades="count", total_pnl="sum", avg_pnl="mean",
                      win_rate=lambda x: (df.loc[x.index, "Win/Loss"] == "Win").mean())
    print(zone_df.to_string())


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    trades, bb = load_data()
    df = merge(trades, bb)

    missing_bb = df["bb_upper"].isna().sum()
    if missing_bb:
        print(f"Warning: {missing_bb} trades have no BB data (warm-up period) — excluded from all filters")
    df = df[df["bb_upper"].notna()].copy()

    print(f"\nLoaded {len(df)} trades with BB data  ({len(trades) - len(df)} excluded)")

    rows: list[dict] = []

    # baseline
    rows.append(metrics(df, "Baseline (no BB filter)"))

    # filter 1: upper band — skip if SPX >= bb_upper
    mask_upper = df["spx_entry"] < df["bb_upper"]
    kept, skp = analyse_filtered(df, mask_upper, "Skip if SPX >= upper band")
    rows.append(kept)

    # filter 2: squeeze sweep
    for thr in SQUEEZE_THRESHOLDS:
        mask_sq = df["bb_width"] >= thr
        kept_sq, _ = analyse_filtered(df, mask_sq, f"Skip if bb_width < {thr:.2f} (squeeze)")
        rows.append(kept_sq)

    # filter 3: combined (upper band AND no squeeze)
    for thr in SQUEEZE_THRESHOLDS:
        mask_combo = mask_upper & (df["bb_width"] >= thr)
        kept_c, _ = analyse_filtered(df, mask_combo, f"Combined: upper + width>={thr:.2f}")
        rows.append(kept_c)

    print_table(rows)

    # per-zone breakdown
    position_in_band(df)

    # show the worst trades that BB would have avoided
    print("\nTop 10 worst losses — would upper-band filter have skipped them?")
    worst = df.nsmallest(10, "Total PnL")[
        ["date", "spx_entry", "bb_upper", "Total PnL", "Win/Loss", "Exit reason"]
    ].copy()
    worst["above_upper"] = worst["spx_entry"] >= worst["bb_upper"]
    print(worst.to_string(index=False))


if __name__ == "__main__":
    main()
