"""VIX/VIX3M term-structure filter backtest — 2026-05-15

Reuses prior 3.0x premium SL backtest (matches CURRENT live config:
50pt BPS, 1% OTM, 1 ctr, 3.0x premium SL).

For each trading day in the existing backtest (2014-2026), we:
  1. Pull VIX and VIX3M daily close from yfinance.
  2. Compute ratio = VIX / VIX3M.
  3. Apply each filter threshold: skip entry if ratio > threshold.
  4. Recompute aggregate metrics for filtered trade set.

LIMITATIONS (disclosed in report):
  - yfinance gives DAILY closes only. We use PREVIOUS day's close as the
    proxy for "ratio at 10:30 ET entry today". This is a standard proxy
    (term structure changes slowly day-to-day) but is not exact.
  - Real-time deployment would use intraday VIX/VIX3M (available via CBOE
    feed or IBKR) — values would differ slightly from prior-close proxy.
  - VIX3M (^VIX3M) ticker has full coverage 2007+ on yfinance.
"""
from __future__ import annotations
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

PRIOR = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/sl_variants_2026_05_15/variant_prem_3.0x.csv")
OUT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/vix_termstructure_2026_05_15")
OUT.mkdir(parents=True, exist_ok=True)

THRESHOLDS = [None, 0.95, 1.00, 1.02, 1.05, 1.10]

# ── Load prior trade-by-trade backtest ────────────────────────────────
trades = pd.read_csv(PRIOR, parse_dates=["date"])
print(f"Loaded {len(trades)} trades from {trades['date'].min().date()} to {trades['date'].max().date()}")

# ── Pull VIX and VIX3M ────────────────────────────────────────────────
start = (trades["date"].min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
end = (trades["date"].max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)["Close"]
vix3m = yf.download("^VIX3M", start=start, end=end, progress=False, auto_adjust=False)["Close"]

vix = vix.squeeze().rename("vix_close")
vix3m = vix3m.squeeze().rename("vix3m_close")

term = pd.concat([vix, vix3m], axis=1).dropna()
term["ratio"] = term["vix_close"] / term["vix3m_close"]
# Proxy: use PRIOR day's close (shifted forward by 1 trading day)
term["ratio_prev"] = term["ratio"].shift(1)
term["vix_prev"] = term["vix_close"].shift(1)
term["vix3m_prev"] = term["vix3m_close"].shift(1)
term = term.reset_index().rename(columns={"Date": "date"})

print(f"VIX/VIX3M data: {term['date'].min().date()} to {term['date'].max().date()} ({len(term)} days)")
print(f"Ratio stats: min={term['ratio'].min():.3f} median={term['ratio'].median():.3f} max={term['ratio'].max():.3f}")
print(f"Days ratio>1.00: {(term['ratio']>1.00).sum()} ({(term['ratio']>1.00).mean()*100:.1f}%)")

# Merge prior-close ratio onto trade dates
df = trades.merge(term[["date","ratio_prev","vix_prev","vix3m_prev"]], on="date", how="left")
missing = df["ratio_prev"].isna().sum()
print(f"Trades missing VIX3M data: {missing} ({missing/len(df)*100:.1f}%)")
# For missing, treat as ratio=1.0 (neutral) — won't be filtered by most thresholds
df["ratio_prev"] = df["ratio_prev"].fillna(1.0)


def metrics(d: pd.DataFrame, label: str) -> dict:
    if len(d) == 0:
        return {"variant": label, "n_trades": 0}
    pnl = d["net_pnl_$"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    daily_ret = pnl  # 1 trade/day, so per-trade pnl == daily pnl
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0
    return {
        "variant": label,
        "n_trades": len(d),
        "total_pnl_$": round(pnl.sum(), 2),
        "win_rate_%": round((pnl > 0).mean() * 100, 2),
        "avg_win_$": round(wins.mean(), 2) if len(wins) else 0,
        "avg_loss_$": round(losses.mean(), 2) if len(losses) else 0,
        "max_loss_$": round(pnl.min(), 2),
        "max_win_$": round(pnl.max(), 2),
        "sharpe_annual": round(sharpe, 3),
        "expectancy_per_trade_$": round(pnl.mean(), 2),
    }


# ── Run each variant ──────────────────────────────────────────────────
results = []
filter_quality = []
worst10_analysis = []

baseline = df.copy()
baseline_pnl = baseline["net_pnl_$"].sum()

# Identify worst 10 days for separate analysis
worst10 = df.nsmallest(10, "net_pnl_$")[["date","net_pnl_$","ratio_prev","vix_prev","vix3m_prev"]]
worst10.to_csv(OUT/"worst_10_days.csv", index=False)

for thr in THRESHOLDS:
    if thr is None:
        label = "baseline_no_filter"
        kept = df
        skipped = df.iloc[0:0]
    else:
        label = f"skip_if_ratio_gt_{thr:.2f}"
        kept = df[df["ratio_prev"] <= thr]
        skipped = df[df["ratio_prev"] > thr]

    m = metrics(kept, label)
    m["n_skipped"] = len(skipped)
    m["pnl_vs_baseline_$"] = round(m.get("total_pnl_$",0) - baseline_pnl, 2)

    if len(skipped) > 0:
        skipped_wins = (skipped["net_pnl_$"] > 0).sum()
        skipped_losses = (skipped["net_pnl_$"] <= 0).sum()
        skipped_pnl = skipped["net_pnl_$"].sum()
        loss_avoid_rate = skipped_losses / len(skipped) * 100
        # baseline loss rate for comparison
        baseline_loss_rate = (df["net_pnl_$"] <= 0).mean() * 100
        m["skipped_wins"] = int(skipped_wins)
        m["skipped_losses"] = int(skipped_losses)
        m["skipped_pnl_avoided_$"] = round(-skipped_pnl, 2)  # positive = avoided loss
        m["skipped_loss_rate_%"] = round(loss_avoid_rate, 2)
        m["baseline_loss_rate_%"] = round(baseline_loss_rate, 2)
        # Filter edge: how much higher is skipped loss rate vs base?
        m["filter_edge_pct_pts"] = round(loss_avoid_rate - baseline_loss_rate, 2)
        # Worst-10 protection
        worst10_skipped = skipped[skipped["date"].isin(worst10["date"])]
        m["worst10_skipped"] = len(worst10_skipped)

        filter_quality.append({
            "threshold": thr,
            "n_skipped": len(skipped),
            "skipped_wins": int(skipped_wins),
            "skipped_losses": int(skipped_losses),
            "skipped_loss_rate_%": round(loss_avoid_rate, 2),
            "baseline_loss_rate_%": round(baseline_loss_rate, 2),
            "filter_edge_pct_pts": round(loss_avoid_rate - baseline_loss_rate, 2),
            "pnl_avoided_$": round(-skipped_pnl, 2),
            "worst10_skipped": len(worst10_skipped),
        })
    else:
        m["skipped_wins"] = 0
        m["skipped_losses"] = 0
        m["skipped_pnl_avoided_$"] = 0
        m["filter_edge_pct_pts"] = 0
        m["worst10_skipped"] = 0

    results.append(m)

summary = pd.DataFrame(results)
summary.to_csv(OUT/"summary.csv", index=False)
pd.DataFrame(filter_quality).to_csv(OUT/"filter_quality.csv", index=False)

# Save merged per-trade with ratio for inspection
df.to_csv(OUT/"trades_with_ratio.csv", index=False)

print("\n=== SUMMARY ===")
print(summary.to_string(index=False))
print("\n=== FILTER QUALITY ===")
print(pd.DataFrame(filter_quality).to_string(index=False))
print("\n=== WORST 10 DAYS ===")
print(worst10.to_string(index=False))
