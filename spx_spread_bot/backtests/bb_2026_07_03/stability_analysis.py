"""BB study supplement — fair comparison + split-half stability.

Fixes two metric problems in the base scripts:
  1. bb_filter_analysis.py annualizes per-trade Sharpe with sqrt(252)
     regardless of trade frequency — a filter that drops trades gets a free
     Sharpe boost. Here: portfolio-level daily P&L series (0 on skipped
     days) for baseline vs filtered, same calendar.
  2. Tests whether the trades each filter SKIPS are actually bad (mean P&L
     of skipped set, t-test) — the honest test of a skip-filter.
Split halves: 2013-2019 vs 2020-2026 and 2016-2020 vs 2021-2026.

Also split-half checks for the standalone BB mean-reversion strategy.
Atomic writes (tmp+rename).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from bb_filter_analysis import load_data, merge  # noqa: E402
from bb_standalone_backtest import (  # noqa: E402
    load_bb,
    strategy_mean_reversion,
)

OUT = HERE / "filter_stability.csv"


def daily_sharpe(df: pd.DataFrame, mask: pd.Series) -> float:
    """Annualized Sharpe of the DAILY P&L series over the full calendar of
    baseline trade dates (skipped days contribute 0)."""
    pnl = df["Total PnL"].where(mask, 0.0)
    if pnl.std(ddof=1) == 0:
        return 0.0
    return float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(252))


def main() -> None:
    trades, bb = load_data()
    df = merge(trades, bb)
    df = df[df["bb_upper"].notna()].copy()
    df["year"] = df["date"].dt.year

    filters = {
        "baseline": pd.Series(True, index=df.index),
        "skip SPX>=upper": df["spx_entry"] < df["bb_upper"],
        "skip width<0.03": df["bb_width"] >= 0.03,
        "skip width<0.04": df["bb_width"] >= 0.04,
        "skip width<0.05": df["bb_width"] >= 0.05,
        "upper + width>=0.04": (df["spx_entry"] < df["bb_upper"]) & (df["bb_width"] >= 0.04),
    }

    rows = []
    for name, mask in filters.items():
        kept = df[mask]
        skipped = df[~mask]
        halves = {}
        for tag, sel in [
            ("h13_19", df["year"] <= 2019),
            ("h20_26", df["year"] >= 2020),
            ("e16_20", (df["year"] >= 2016) & (df["year"] <= 2020)),
            ("e21_26", df["year"] >= 2021),
        ]:
            k = df[mask & sel]
            s = df[~mask & sel]
            halves[f"{tag}_kept_total"] = round(k["Total PnL"].sum())
            halves[f"{tag}_skip_n"] = len(s)
            halves[f"{tag}_skip_avg"] = round(s["Total PnL"].mean()) if len(s) else np.nan
        if len(skipped) > 1:
            tt = stats.ttest_1samp(skipped["Total PnL"], 0.0)
            skip_t, skip_p = round(float(tt.statistic), 2), float(tt.pvalue)
        else:
            skip_t, skip_p = np.nan, np.nan
        rows.append(
            dict(
                filter=name,
                kept_n=len(kept),
                kept_total=round(kept["Total PnL"].sum()),
                kept_maxdd=round(
                    (kept["Total PnL"].cumsum() - kept["Total PnL"].cumsum().cummax()).min()
                ),
                daily_sharpe=round(daily_sharpe(df, mask), 3),
                skip_n=len(skipped),
                skip_total=round(skipped["Total PnL"].sum()),
                skip_avg=round(skipped["Total PnL"].mean()) if len(skipped) else np.nan,
                skip_stoploss_n=int((skipped["Exit reason"] == "stop_loss").sum()),
                skip_t=skip_t,
                skip_p=round(skip_p, 4) if skip_p == skip_p else np.nan,
                **halves,
            )
        )
    res = pd.DataFrame(rows)
    tmp = OUT.with_suffix(".csv.tmp")
    res.to_csv(tmp, index=False)
    tmp.rename(OUT)
    pd.set_option("display.width", 300)
    print("=== BB filter on live BPS — fair (portfolio-level) comparison ===")
    print(res.to_string(index=False))

    # ── standalone mean reversion: split halves ────────────────────────────
    print("\n=== Standalone BB mean-reversion (long fade, $50/pt) ===")
    bbd = load_bb()
    tr = pd.DataFrame(strategy_mean_reversion(bbd))
    tr["year"] = pd.to_datetime(tr["entry"]).dt.year
    def seg(sel, tag):
        s = tr[sel]
        if len(s) == 0:
            print(f"  {tag}: no trades")
            return
        cum = s["pnl"].cumsum()
        dd = (cum - cum.cummax()).min()
        tt = stats.ttest_1samp(s["pnl"], 0.0) if len(s) > 1 else None
        print(
            f"  {tag}: n={len(s)} total=${s['pnl'].sum():,.0f} avg=${s['pnl'].mean():,.0f} "
            f"win={(s['pnl']>0).mean()*100:.0f}% maxDD=${dd:,.0f}"
            + (f" t={tt.statistic:.2f} p={tt.pvalue:.3f}" if tt else "")
        )
    seg(tr["year"] > 0, "ALL      ")
    seg(tr["year"] <= 2019, "2013-2019")
    seg(tr["year"] >= 2020, "2020-2026")
    seg((tr["year"] >= 2016) & (tr["year"] <= 2020), "2016-2020")
    seg(tr["year"] >= 2021, "2021-2026")
    print(f"  trades/year ≈ {len(tr)/13.2:.1f}; capital at risk: naked long 1x$50/pt SPX, no stop")


if __name__ == "__main__":
    main()
