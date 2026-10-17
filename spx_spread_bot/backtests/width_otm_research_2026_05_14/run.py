"""SPX 0DTE 12-Year Analysis Batch

Runs 5 analyses in one pass over 2014-04 → 2026-04 SPX option chains:
  1. Width × OTM grid (4 × 6 = 24 configs)
  2. VIX regime breakdown
  3. Event-day blackout analysis
  4. Tail-risk top-30 worst days
  5. Day-of-week + month seasonality

Outputs to /tmp/spx_12yr_analysis/.
"""
from __future__ import annotations
import re, warnings, json, sys
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

import databento as db
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

OUT = Path("/tmp/spx_12yr_analysis")
OUT.mkdir(exist_ok=True)

# ── Strategy constants ─────────────────────────────────────────────────
STOP_MULT = 2.0
SL_FILL_MULT = 2.5  # actual exit price when SL fires (slippage past 2x)
DIRECTION_FILTER = True
SLIP_PER_SPREAD_PER_SIDE = 0.05  # round-trip = 2x this per spread
MIN_CREDIT = 0.05  # very loose for narrow spreads
MULT = 100.0
NOMINAL_TARGET = 5000  # ~$5K matched exposure

# Sweep configs
WIDTHS = [5, 10, 25, 50]
OTM_PCTS = [0.005, 0.0075, 0.010, 0.0125, 0.015, 0.020]

# ── FOMC dates (2014-2026) — public Fed announcement dates ──────────────
FOMC_DATES = {
    # 2014
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18",
    "2014-07-30", "2014-09-17", "2014-10-29", "2014-12-17",
    # 2015
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020 (incl. emergency cuts)
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-03-23",
    "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16",
    "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (projected schedule)
    "2026-01-28", "2026-03-18", "2026-04-29",
}

# ── Calendar helpers ────────────────────────────────────────────────────
def nth_weekday(year, month, weekday, n):
    """Return date of the nth occurrence of weekday in given month. weekday=0=Mon."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)

def is_opex(d: date) -> bool:
    """3rd Friday of any month."""
    return d == nth_weekday(d.year, d.month, 4, 3)

def is_triple_witching(d: date) -> bool:
    """3rd Friday of Mar/Jun/Sep/Dec."""
    return is_opex(d) and d.month in (3, 6, 9, 12)

def is_nfp(d: date) -> bool:
    """First Friday of month (NFP release day)."""
    return d == nth_weekday(d.year, d.month, 4, 1)

def is_cpi(d: date) -> bool:
    """CPI typically released 10-15th of each month, weekday only.
    Approximation: 13th of month, or next business day."""
    target = date(d.year, d.month, 13)
    while target.weekday() > 4:
        target += timedelta(days=1)
    return d == target

def is_fomc(d: date) -> bool:
    return d.isoformat() in FOMC_DATES

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")
def parse_symbol(s):
    m = OCC_RE.match(s.strip())
    if not m: return None
    try:
        return (m.group("root"),
                datetime.strptime(m.group("yymmdd"), "%y%m%d").date(),
                m.group("right"),
                int(m.group("strike")) / 1000.0)
    except: return None

# ── SPX/VIX daily ───────────────────────────────────────────────────────
SPX_CACHE = OUT / "spx_vix_2014_2026.parquet"
def load_spx():
    if SPX_CACHE.exists():
        df = pd.read_parquet(SPX_CACHE)
        print(f"  SPX/VIX cache loaded: {len(df)} rows")
        return df
    print("  Downloading SPX/VIX from yfinance...")
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-15", auto_adjust=True, progress=False)
    vix = yf.download("^VIX", start="2014-01-01", end="2026-05-15", auto_adjust=True, progress=False)
    if isinstance(spx.columns, pd.MultiIndex):
        spx.columns = [c[0] for c in spx.columns]
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = [c[0] for c in vix.columns]
    df = spx[["Open", "High", "Low", "Close"]].copy()
    df.columns = ["spx_open", "spx_high", "spx_low", "spx_close"]
    df["vix_close"] = vix["Close"]
    df.index = pd.to_datetime(df.index).normalize()
    df.dropna(subset=["spx_open"], inplace=True)
    df.to_parquet(SPX_CACHE)
    print(f"  SPX/VIX saved: {len(df)} rows")
    return df

# ── Options chain loader ────────────────────────────────────────────────
def load_opts_all():
    BASE = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/data/databento")
    DIRS = [
        BASE / "spxw_opt_2014_2015",
        BASE / "spxw_opt_2016_2018",
        BASE / "spxw_opt_2019",
        BASE / "spxw_opt_2020_2022",
        BASE / "spxw_opt",
        BASE / "spxw_opt_2025_2026",
    ]
    out = {}
    files = []
    for d in DIRS:
        if d.exists():
            files.extend(sorted(d.glob("opra-pillar-*.ohlcv-1d.dbn.zst")))
    print(f"  Loading {len(files)} option-chain files...")
    cnt = 0
    for fp in files:
        try:
            df = db.DBNStore.from_file(fp).to_df()
        except Exception:
            continue
        if df.empty:
            continue
        df["parsed"] = df["symbol"].apply(parse_symbol)
        df = df[df["parsed"].notna()].copy()
        if df.empty:
            continue
        df["expiry"] = df["parsed"].apply(lambda x: x[1])
        df["right"] = df["parsed"].apply(lambda x: x[2])
        df["strike"] = df["parsed"].apply(lambda x: x[3])
        try:
            td = datetime.strptime(fp.name.split("-")[2].split(".")[0], "%Y%m%d").date()
        except ValueError:
            continue
        puts = df[(df["right"] == "P") & (df["expiry"] == td)][["strike","open","close"]]
        if not puts.empty:
            out[td] = puts.reset_index(drop=True)
            cnt += 1
        if cnt % 500 == 0 and cnt > 0:
            print(f"    {cnt} files processed...")
    print(f"  {len(out)} trading days with 0DTE put chains loaded")
    return out

# ── Backtest engine ─────────────────────────────────────────────────────
@dataclass
class Trade:
    date: date
    width: int
    otm_pct: float
    short_strike: float
    long_strike: float
    actual_width: float
    credit: float           # $ per single contract
    exit_mark: float
    pnl_per_spread: float   # $
    exit_reason: str
    spx_open: float
    spx_close: float
    spx_low: float
    vix: float
    is_fomc: bool
    is_cpi: bool
    is_nfp: bool
    is_opex: bool
    is_triple_witching: bool
    weekday: int
    month: int

def run_one_config(opts, spx, width, otm_pct):
    """Run backtest for a single (width, otm) config."""
    trades = []
    for day in sorted(opts):
        ts = pd.Timestamp(day)
        if ts not in spx.index: continue
        row = spx.loc[ts]
        if isinstance(row, pd.DataFrame): row = row.iloc[0]
        spx_open = float(row["spx_open"])
        spx_close = float(row["spx_close"])
        spx_low = float(row["spx_low"])
        vix = float(row["vix_close"]) if not pd.isna(row["vix_close"]) else 20.0

        if DIRECTION_FILTER and spx_close < spx_open: continue

        opts_d = opts[day].copy()
        target_short = spx_open * (1.0 - otm_pct)
        opts_d["d"] = (opts_d["strike"] - target_short).abs()
        sr = opts_d.loc[opts_d["d"].idxmin()]
        short_strike = float(sr["strike"])
        long_target = short_strike - width
        exact = opts_d[opts_d["strike"] == long_target]
        if not exact.empty:
            lr = exact.iloc[0]
        else:
            below = opts_d[opts_d["strike"] < short_strike]
            if below.empty: continue
            below = below.copy(); below["d"] = (below["strike"] - long_target).abs()
            lr = below.loc[below["d"].idxmin()]
        long_strike = float(lr["strike"])
        actual_width = short_strike - long_strike

        credit = float(sr["open"]) - float(lr["open"])
        if credit < MIN_CREDIT or credit <= 0: continue

        # SL proxy via SPX intraday low
        sl_triggered = spx_low <= short_strike
        if sl_triggered:
            exit_mark = min(credit * SL_FILL_MULT, actual_width)
            pnl = credit - exit_mark
            reason = "stop_proxy"
        else:
            mark_close = max(0.0, float(sr["close"]) - float(lr["close"]))
            if mark_close <= 0.05:
                pnl = credit
                reason = "expire"
                exit_mark = 0.0
            else:
                pnl = credit - mark_close
                reason = "close"
                exit_mark = mark_close
                if -pnl > actual_width: pnl = -actual_width

        trades.append(Trade(
            date=day, width=width, otm_pct=otm_pct,
            short_strike=short_strike, long_strike=long_strike,
            actual_width=actual_width, credit=credit*MULT,
            exit_mark=exit_mark*MULT, pnl_per_spread=pnl*MULT,
            exit_reason=reason,
            spx_open=spx_open, spx_close=spx_close, spx_low=spx_low, vix=vix,
            is_fomc=is_fomc(day), is_cpi=is_cpi(day),
            is_nfp=is_nfp(day), is_opex=is_opex(day),
            is_triple_witching=is_triple_witching(day),
            weekday=day.weekday(), month=day.month,
        ))
    return trades

def aggregate_with_scaling(trades, n_spreads):
    """Apply slippage and contract scaling, return metrics dict."""
    if not trades: return None
    rt_slip = SLIP_PER_SPREAD_PER_SIDE * 2 * MULT * n_spreads
    rows = []
    for t in trades:
        net = t.pnl_per_spread * n_spreads - rt_slip
        rows.append({
            "date": t.date, "credit": t.credit*n_spreads,
            "raw_pnl": t.pnl_per_spread*n_spreads, "net_pnl": net,
            "exit_reason": t.exit_reason, "win": net > 0,
            "vix": t.vix,
            "is_fomc": t.is_fomc, "is_cpi": t.is_cpi, "is_nfp": t.is_nfp,
            "is_opex": t.is_opex, "is_triple_witching": t.is_triple_witching,
            "weekday": t.weekday, "month": t.month,
        })
    df = pd.DataFrame(rows)
    cumul = df["net_pnl"].cumsum()
    return {
        "trades": len(df),
        "win_rate": df["win"].mean(),
        "total_pnl": df["net_pnl"].sum(),
        "avg_pnl": df["net_pnl"].mean(),
        "median_pnl": df["net_pnl"].median(),
        "best_day": df["net_pnl"].max(),
        "worst_day": df["net_pnl"].min(),
        "max_dd": (cumul.cummax() - cumul).max(),
        "sharpe": df["net_pnl"].mean() / df["net_pnl"].std() * np.sqrt(252) if df["net_pnl"].std() else 0,
        "stops": int((df["exit_reason"] == "stop_proxy").sum()),
        "expires": int((df["exit_reason"] == "expire").sum()),
        "df": df,
    }

# ── ANALYSIS DRIVERS ────────────────────────────────────────────────────
def n_for_width(width):
    """Return how many contracts to size the given width to ~$5K nominal."""
    return max(1, round(NOMINAL_TARGET / (width * MULT)))

def analysis_1_width_otm_grid(opts, spx):
    """Build the 24-config heatmap."""
    rows = []
    all_trades = {}
    for w in WIDTHS:
        n = n_for_width(w)
        for otm in OTM_PCTS:
            trades = run_one_config(opts, spx, w, otm)
            agg = aggregate_with_scaling(trades, n)
            if agg:
                all_trades[(w, otm)] = trades
                rows.append({
                    "width": w, "otm_pct": otm, "n_contracts": n,
                    "trades": agg["trades"], "win_rate": agg["win_rate"],
                    "total_pnl": agg["total_pnl"], "avg_pnl": agg["avg_pnl"],
                    "best_day": agg["best_day"], "worst_day": agg["worst_day"],
                    "max_dd": agg["max_dd"], "sharpe": agg["sharpe"],
                    "stops": agg["stops"], "expires": agg["expires"],
                })
                print(f"    [{w}pt × {n}, {otm*100:.2f}% OTM]  trades={agg['trades']:4d}  total=${agg['total_pnl']:>10,.0f}  avg=${agg['avg_pnl']:>7,.2f}  sharpe={agg['sharpe']:.2f}  stops={agg['stops']}")
    df = pd.DataFrame(rows).sort_values("total_pnl", ascending=False)
    df.to_csv(OUT / "width_otm_heatmap.csv", index=False)
    return df, all_trades

def analysis_2_vix_regime(trades, n):
    df = aggregate_with_scaling(trades, n)["df"]
    bins = [0, 12, 15, 20, 25, 30, 100]
    labels = ["<12", "12-15", "15-20", "20-25", "25-30", ">30"]
    df["regime"] = pd.cut(df["vix"], bins=bins, labels=labels)
    out = df.groupby("regime").agg(
        trades=("net_pnl", "count"),
        win_rate=("win", "mean"),
        total_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
        worst_day=("net_pnl", "min"),
        stops=("exit_reason", lambda x: (x == "stop_proxy").sum()),
    ).round(2)
    out.to_csv(OUT / "vix_regime.csv")
    return out

def analysis_3_event_days(trades, n):
    df = aggregate_with_scaling(trades, n)["df"]
    rows = []
    for col in ["is_fomc", "is_cpi", "is_nfp", "is_opex", "is_triple_witching"]:
        ev = df[df[col]]
        non = df[~df[col]]
        if len(ev) == 0: continue
        rows.append({
            "event": col.replace("is_", ""),
            "event_days": len(ev),
            "non_event_days": len(non),
            "event_avg_pnl": ev["net_pnl"].mean(),
            "non_event_avg_pnl": non["net_pnl"].mean(),
            "event_win_rate": ev["win"].mean(),
            "non_event_win_rate": non["win"].mean(),
            "event_worst_day": ev["net_pnl"].min(),
            "non_event_worst_day": non["net_pnl"].min(),
            "event_total_pnl": ev["net_pnl"].sum(),
        })
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "event_day_breakdown.csv", index=False)
    return out

def analysis_4_tail_risk(trades, n):
    df = aggregate_with_scaling(trades, n)["df"]
    df = df.sort_values("net_pnl").head(30).copy()
    df["spx_drop_pct"] = None  # filled below from trade objects
    # match trades back for context
    by_date = {t.date: t for t in trades}
    df["spx_open"] = df["date"].map(lambda d: by_date[d].spx_open if d in by_date else None)
    df["spx_close"] = df["date"].map(lambda d: by_date[d].spx_close if d in by_date else None)
    df["spx_low"] = df["date"].map(lambda d: by_date[d].spx_low if d in by_date else None)
    df["short_strike"] = df["date"].map(lambda d: by_date[d].short_strike if d in by_date else None)
    df["spx_drop_pct"] = ((df["spx_low"] - df["spx_open"]) / df["spx_open"] * 100).round(2)
    df.to_csv(OUT / "tail_risk_top30.csv", index=False)
    return df

def analysis_5_seasonality(trades, n):
    df = aggregate_with_scaling(trades, n)["df"]
    dow_names = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
    df["dow"] = df["weekday"].map(dow_names)
    dow = df.groupby("dow").agg(
        trades=("net_pnl","count"), win_rate=("win","mean"),
        total_pnl=("net_pnl","sum"), avg_pnl=("net_pnl","mean"),
    ).round(2)
    mo = df.groupby("month").agg(
        trades=("net_pnl","count"), win_rate=("win","mean"),
        total_pnl=("net_pnl","sum"), avg_pnl=("net_pnl","mean"),
    ).round(2)
    dow.to_csv(OUT / "seasonality_dow.csv")
    mo.to_csv(OUT / "seasonality_month.csv")
    return dow, mo

# ── MAIN ────────────────────────────────────────────────────────────────
def main():
    print("="*70)
    print(f"SPX 0DTE 12-YEAR ANALYSIS — output to {OUT}")
    print("="*70)
    print()

    print("STEP 1: Load SPX/VIX daily data")
    spx = load_spx()
    print()

    print("STEP 2: Load option chains (12 years of databento daily OHLCV)")
    opts = load_opts_all()
    if opts:
        first = min(opts.keys())
        last = max(opts.keys())
        print(f"  date range: {first} → {last}")
    print()

    print("STEP 3: Width × OTM grid (24 configs)")
    grid_df, all_trades = analysis_1_width_otm_grid(opts, spx)
    print()
    print("  TOP 5 by total P&L:")
    print(grid_df.head(5).to_string(index=False))
    print()

    # Pick the BEST config for downstream analyses
    best_row = grid_df.iloc[0]
    best_w, best_otm, best_n = int(best_row["width"]), float(best_row["otm_pct"]), int(best_row["n_contracts"])
    best_trades = all_trades[(best_w, best_otm)]
    print(f"  Best config: width={best_w}pt × {best_n} contracts, OTM={best_otm*100:.2f}%")
    print()

    print("STEP 4: VIX regime breakdown (using best config)")
    vix_out = analysis_2_vix_regime(best_trades, best_n)
    print(vix_out)
    print()

    print("STEP 5: Event-day blackout analysis")
    event_out = analysis_3_event_days(best_trades, best_n)
    print(event_out.to_string(index=False))
    print()

    print("STEP 6: Tail-risk top-30 worst days")
    tail_out = analysis_4_tail_risk(best_trades, best_n)
    print(tail_out[["date","net_pnl","vix","spx_drop_pct","is_fomc","is_cpi","is_nfp","is_opex"]].head(10).to_string(index=False))
    print()

    print("STEP 7: Seasonality (DoW + month)")
    dow_out, mo_out = analysis_5_seasonality(best_trades, best_n)
    print("  Day of week:")
    print(dow_out)
    print("  Month:")
    print(mo_out)
    print()

    # Write summary.md
    with open(OUT / "summary.md", "w") as f:
        f.write(f"# SPX 0DTE 12-Year Analysis Summary\n\n")
        f.write(f"**Date range**: {min(opts.keys()) if opts else 'n/a'} → {max(opts.keys()) if opts else 'n/a'}\n")
        f.write(f"**Total trading days analyzed**: {len(opts)}\n\n")
        f.write(f"## Top 5 Configs by Total P&L\n\n")
        f.write(grid_df.head(5).to_markdown(index=False))
        f.write(f"\n\n## Best Config: {best_w}pt × {best_n} @ {best_otm*100:.2f}% OTM\n\n")
        f.write(f"## VIX Regime Breakdown\n\n")
        f.write(vix_out.to_markdown())
        f.write(f"\n\n## Event-Day Analysis\n\n")
        f.write(event_out.to_markdown(index=False))
        f.write(f"\n\n## Tail Risk Top 30\n\n")
        f.write(tail_out[["date","net_pnl","vix","spx_drop_pct","is_fomc","is_cpi","is_nfp","is_opex"]].head(15).to_markdown(index=False))
        f.write(f"\n\n## Seasonality\n\n### Day of Week\n")
        f.write(dow_out.to_markdown())
        f.write(f"\n\n### Month\n")
        f.write(mo_out.to_markdown())

    print()
    print("="*70)
    print(f"COMPLETE — outputs in {OUT}")
    print(f"  summary.md  width_otm_heatmap.csv  vix_regime.csv")
    print(f"  event_day_breakdown.csv  tail_risk_top30.csv")
    print(f"  seasonality_dow.csv  seasonality_month.csv")
    print("="*70)

if __name__ == "__main__":
    main()
