"""Multi-ticker Iron Condor backtest — same template as validated AVGO strategy.

Config:
  - 0.5% OTM short put + 0.5% OTM short call
  - Wings = ~6% of spot (proportional to stock price; matches AVGO 25pt at $420)
  - 09:45 ET entry
  - 15:45 ET forced close (intraday only)
  - 50% PT, 2.0x SL
  - 0DTE only (same-day expiry)
  - M/W/F (when 0DTE available)

Tickers: NVDA, META, AAPL, MSFT, GOOGL, AMZN, TSLA + AVGO baseline
"""
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

ROOT_BASE = Path(__file__).parent / "data"
AVGO_DATA = Path(__file__).parent.parent / "avgo_ic_2025/data"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SYM_RE = re.compile(r"^([A-Z]+)\s+(\d{6})([PC])(\d{8})$")

# Per-ticker config: strike spacing and yfinance symbol
TICKER_CFG = {
    "AVGO":  {"spacing": 2.5, "yf": "AVGO",  "data_dir": AVGO_DATA},
    "NVDA":  {"spacing": 1.0, "yf": "NVDA",  "data_dir": ROOT_BASE / "nvda"},
    "META":  {"spacing": 5.0, "yf": "META",  "data_dir": ROOT_BASE / "meta"},
    "AAPL":  {"spacing": 2.5, "yf": "AAPL",  "data_dir": ROOT_BASE / "aapl"},
    "MSFT":  {"spacing": 5.0, "yf": "MSFT",  "data_dir": ROOT_BASE / "msft"},
    "GOOGL": {"spacing": 2.5, "yf": "GOOGL", "data_dir": ROOT_BASE / "googl"},
    "AMZN":  {"spacing": 2.5, "yf": "AMZN",  "data_dir": ROOT_BASE / "amzn"},
    "TSLA":  {"spacing": 5.0, "yf": "TSLA",  "data_dir": ROOT_BASE / "tsla"},
}

def log(m): print(m, flush=True)


def load_ticker_data(ticker, data_dir, only_2026=True):
    """Load all parquets for a ticker, return (expiry_index, strikes_by_day)."""
    expiry_index = defaultdict(list)
    strikes_by_day = defaultdict(lambda: defaultdict(set))
    pattern = f"{ticker.lower()}_cbbo_*.parquet" if ticker != "AVGO" else "avgo_cbbo_2026*.parquet"
    files = sorted(data_dir.glob(pattern))
    for f in files:
        df = pd.read_parquet(f)[["bid_px_00", "ask_px_00", "symbol"]]
        df["sym_clean"] = df["symbol"].str.strip()
        ext = df["sym_clean"].str.extract(SYM_RE)
        ext.columns = ["root", "yymmdd", "right", "strike_str"]
        df["expiry"] = pd.to_datetime(ext["yymmdd"], format="%y%m%d").dt.date
        df["right"] = ext["right"]
        df["strike"] = ext["strike_str"].astype(float) / 1000
        df = df.dropna(subset=["expiry", "right", "strike"])
        ds = f.stem.split("_")[-1]
        td = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        if only_2026 and td.year != 2026:
            continue
        for (exp, strike, right), grp in df.groupby(["expiry", "strike", "right"]):
            expiry_index[(td, exp, strike, right)] = grp[["bid_px_00", "ask_px_00"]]
            strikes_by_day[td][exp].add(strike)
    return expiry_index, strikes_by_day


def find_strike(avail, target, spacing):
    snapped = round(target / spacing) * spacing
    if snapped in avail: return snapped
    for d_mult in range(1, 8):
        for sign in [+1, -1]:
            t = snapped + sign * d_mult * spacing
            if t in avail: return t
    return None


def at_or_after(df, ts):
    if df is None or df.empty: return None
    sub = df.loc[df.index >= ts]
    return None if sub.empty else sub.iloc[0]


def simulate_ic(ticker, td, target_exp, expiry_index, strikes_by_day,
                otm_pct, wing_pct, spot_entry, spot_exp_close, spacing,
                stop_mult=2.0, pt_frac=0.5):
    avail = strikes_by_day[td].get(target_exp, set())
    if not avail: return None

    sp = find_strike(avail, spot_entry * (1 - otm_pct), spacing)
    sc = find_strike(avail, spot_entry * (1 + otm_pct), spacing)
    if sp is None or sc is None: return None

    wing_dollars = spot_entry * wing_pct
    lp = find_strike(avail, sp - wing_dollars, spacing)
    lc = find_strike(avail, sc + wing_dollars, spacing)
    if lp is None or lc is None: return None

    sp_df = expiry_index.get((td, target_exp, sp, "P"))
    sc_df = expiry_index.get((td, target_exp, sc, "C"))
    lp_df = expiry_index.get((td, target_exp, lp, "P"))
    lc_df = expiry_index.get((td, target_exp, lc, "C"))
    if any(x is None for x in [sp_df, sc_df, lp_df, lc_df]): return None

    entry_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=9, minute=45).astimezone(UTC))
    exit_ts = pd.Timestamp(datetime.combine(td, datetime.min.time(), tzinfo=ET).replace(
        hour=15, minute=45).astimezone(UTC))

    e_sp = at_or_after(sp_df, entry_ts); e_sc = at_or_after(sc_df, entry_ts)
    e_lp = at_or_after(lp_df, entry_ts); e_lc = at_or_after(lc_df, entry_ts)
    if any(x is None for x in [e_sp, e_sc, e_lp, e_lc]): return None

    credit = float(e_sp["bid_px_00"]) + float(e_sc["bid_px_00"]) - float(e_lp["ask_px_00"]) - float(e_lc["ask_px_00"])
    if credit < 0.10: return None

    stop_debit = credit * stop_mult
    pt_debit = credit * (1 - pt_frac)

    sp_w = sp_df.loc[(sp_df.index >= entry_ts) & (sp_df.index <= exit_ts)]
    sc_w = sc_df.loc[(sc_df.index >= entry_ts) & (sc_df.index <= exit_ts)]
    lp_w = lp_df.loc[(lp_df.index >= entry_ts) & (lp_df.index <= exit_ts)]
    lc_w = lc_df.loc[(lc_df.index >= entry_ts) & (lc_df.index <= exit_ts)]

    if any(s.empty for s in [sp_w, sc_w, lp_w, lc_w]):
        return None

    sp_r = sp_w.rename(columns={"bid_px_00": "b_sp", "ask_px_00": "a_sp"})
    sc_r = sc_w.rename(columns={"bid_px_00": "b_sc", "ask_px_00": "a_sc"})
    lp_r = lp_w.rename(columns={"bid_px_00": "b_lp", "ask_px_00": "a_lp"})
    lc_r = lc_w.rename(columns={"bid_px_00": "b_lc", "ask_px_00": "a_lc"})
    al = sp_r.join(sc_r, how="inner").join(lp_r, how="inner").join(lc_r, how="inner")
    if al.empty: return None

    al["debit"] = al["a_sp"] + al["a_sc"] - al["b_lp"] - al["b_lc"]
    sl = al[al["debit"] >= stop_debit]
    pt = al[al["debit"] <= pt_debit]
    sl_ts = sl.index.min() if not sl.empty else None
    pt_ts = pt.index.min() if not pt.empty else None

    exit_val = None; exit_reason = None
    if sl_ts is not None and (pt_ts is None or sl_ts < pt_ts):
        exit_val = al.loc[sl_ts, "debit"]; exit_reason = "stop_loss"
    elif pt_ts is not None:
        exit_val = al.loc[pt_ts, "debit"]; exit_reason = "profit_target"
    else:
        # Use intrinsic at close
        sp_int = max(0, sp - spot_exp_close); lp_int = max(0, lp - spot_exp_close)
        sc_int = max(0, spot_exp_close - sc); lc_int = max(0, spot_exp_close - lc)
        exit_val = sp_int + sc_int - lp_int - lc_int
        exit_reason = "expiry"

    pnl = (credit - exit_val) * 100
    return {
        "ticker": ticker, "trade_date": td, "dow": td.strftime("%a"),
        "expiry": target_exp, "dte": (target_exp - td).days,
        "sp_k": sp, "sc_k": sc, "lp_k": lp, "lc_k": lc,
        "credit": credit, "credit_dollars": credit * 100,
        "wing_dollars": (sp - lp),  # actual wing width in dollars
        "exit_reason": exit_reason, "pnl": pnl,
    }


def run_ticker(ticker, otm_pct=0.005, wing_pct=0.06):
    cfg = TICKER_CFG[ticker]
    log(f"\nLoading {ticker} data...")
    t0 = time.time()
    expiry_index, strikes_by_day = load_ticker_data(ticker, cfg["data_dir"])
    log(f"  {len(strikes_by_day)} trade dates ({time.time()-t0:.0f}s)")

    spot_hist = yf.Ticker(cfg["yf"]).history(start="2026-01-01", end="2026-06-01")
    spot_hist.index = pd.to_datetime(spot_hist.index).tz_localize(None)
    spot_by_date = {pd.Timestamp(d).date(): row for d, row in spot_hist.iterrows()}

    trades = []
    for td in sorted(strikes_by_day.keys()):
        if td.weekday() not in (0, 2, 4): continue  # M/W/F only
        if td not in spot_by_date: continue
        if td not in strikes_by_day[td]: continue  # need same-day expiry

        spot_row = spot_by_date[td]
        spot_o = float(spot_row["Open"]); spot_c = float(spot_row["Close"])
        spot_entry = spot_o + 0.3 * (spot_c - spot_o)

        r = simulate_ic(ticker, td, td, expiry_index, strikes_by_day,
                        otm_pct, wing_pct, spot_entry, spot_c, cfg["spacing"])
        if r: trades.append(r)
    return pd.DataFrame(trades)


# ─── Main ───────────────────────────────────────────────────────────────────
log("=" * 110)
log("MULTI-TICKER IC BACKTEST")
log("Config: 0.5% OTM, ~6% wings, 09:45 entry, 15:45 forced close, 2.0x SL, 50% PT")
log("Period: M/W/F 0DTE only, Feb-May 2026")
log("=" * 110)

all_results = {}
summary_rows = []

for ticker in ["AVGO", "NVDA", "META", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]:
    df = run_ticker(ticker)
    if df.empty:
        log(f"  {ticker}: no trades")
        continue
    all_results[ticker] = df
    n = len(df); wins = (df["pnl"] > 0).sum()
    total = df["pnl"].sum()
    summary_rows.append({
        "ticker": ticker,
        "n": n,
        "win_rate": wins / n * 100,
        "avg_credit": df["credit_dollars"].mean(),
        "avg_wing_dollars": df["wing_dollars"].mean(),
        "total_pnl": total,
        "avg_pnl": df["pnl"].mean(),
        "best": df["pnl"].max(),
        "worst": df["pnl"].min(),
        "stops": (df["exit_reason"] == "stop_loss").sum(),
        "pts": (df["exit_reason"] == "profit_target").sum(),
        "exps": (df["exit_reason"] == "expiry").sum(),
    })
    log(f"  {ticker}: N={n}, Win={wins/n*100:.0f}%, Total=${total:+,.0f}, Avg=${df['pnl'].mean():+.0f}")

summary = pd.DataFrame(summary_rows).sort_values("total_pnl", ascending=False)
log("\n" + "=" * 110)
log("RESULTS SUMMARY (sorted by total P&L)")
log("=" * 110)
log(f"{'Ticker':<8}{'N':<5}{'Win%':<7}{'AvgCr':<8}{'WingW':<8}{'TotalP&L':<11}{'AvgP&L':<10}{'Best':<8}{'Worst':<10}{'Stops':<7}")
log("-" * 90)
for _, r in summary.iterrows():
    log(f"{r['ticker']:<8}{r['n']:<5}{r['win_rate']:<6.0f}%${r['avg_credit']:<7.0f}{r['avg_wing_dollars']:<7.1f}${r['total_pnl']:<+10,.0f}${r['avg_pnl']:<+9.0f}${r['best']:<+7.0f}${r['worst']:<+9.0f}{int(r['stops']):<7}")

# Per ticker per DOW
log("\n" + "=" * 110)
log("PER TICKER × DAY-OF-WEEK")
log("=" * 110)
for ticker, df in all_results.items():
    log(f"\n{ticker}:")
    for dow in ["Mon", "Wed", "Fri"]:
        sub = df[df["dow"] == dow]
        if sub.empty: continue
        n = len(sub); wins = (sub["pnl"] > 0).sum()
        log(f"  {dow}: N={n}, Win={wins/n*100:.0f}%, Total=${sub['pnl'].sum():+,.0f}, Avg=${sub['pnl'].mean():+.0f}")

# Correlation across tickers (P&L on shared trade dates)
log("\n" + "=" * 110)
log("CROSS-TICKER P&L CORRELATION (shared trade dates only)")
log("=" * 110)
combined = None
for ticker, df in all_results.items():
    df_renamed = df[["trade_date", "pnl"]].rename(columns={"pnl": ticker})
    if combined is None:
        combined = df_renamed
    else:
        combined = combined.merge(df_renamed, on="trade_date", how="outer")

combined = combined.set_index("trade_date")
log(f"\nShared days available across all tickers: {combined.dropna().shape[0]}")

import numpy as np
corr = combined.corr()
log("\nCorrelation matrix:")
log(corr.round(2).to_string())

# Daily-stacked P&L (running all tickers on same day)
log("\n" + "=" * 110)
log("HYPOTHETICAL: Run ALL tickers each day (1 contract each)")
log("=" * 110)
combined["total_daily"] = combined.sum(axis=1, skipna=True)
combined["n_trades"] = combined.notna().sum(axis=1) - 1  # excluding total_daily col
daily_avg_pnl = combined["total_daily"].mean()
total_combined = combined["total_daily"].sum()
log(f"  Total combined P&L: ${total_combined:+,.0f}")
log(f"  Avg per day: ${daily_avg_pnl:+.0f}")
log(f"  Best day: ${combined['total_daily'].max():+.0f}")
log(f"  Worst day: ${combined['total_daily'].min():+.0f}")
log(f"  Per-day capital required (worst case): up to ${len(all_results) * 2500:,.0f} ({len(all_results)} contracts × ~$2,500 BPR)")

# Save
combined.to_csv("/tmp/multi_ic_daily_pnl.csv")
summary.to_csv("/tmp/multi_ic_summary.csv", index=False)
corr.to_csv("/tmp/multi_ic_correlation.csv")
log("\nResults saved.")
