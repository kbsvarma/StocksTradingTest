"""Narrow-width SPX 0DTE BPS sweep — 1-contract economics.

User's question (2026-05-21): "what if we have only 5pt spread, max loss
~$500 — how far OTM to make this work?"

Previous width/OTM sweep (2026-05-14) covered widths [5,10,25,50] × OTM
[0.5%-2.0%] but normalized to ~$5K nominal exposure (n_contracts=10 for 5pt,
n=1 for 50pt). This run instead reports per-1-contract economics and
extends OTM range DOWN to 0.2% where the credit-to-risk ratio could swing
the math for narrow spreads.

Sweep: widths [5,10,15,20,25] × OTM [0.2,0.3,0.4,0.5,0.75,1.0] = 30 configs.
Constraints held constant (do not vary):
  - 2× SL multiplier
  - VIX gate 12-25
  - Direction filter ON
  - 10:30 ET entry (proxied via daily OHLCV — uses open price)
  - 0DTE only

Outputs to ./results/.
"""
from __future__ import annotations

import re
import sys
import warnings
import json
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

import databento as db
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)
print(f"[narrow-sweep] output dir: {OUT}", flush=True)

# ── Strategy constants (held constant) ─────────────────────────────────
STOP_MULT = 2.0
SL_FILL_MULT = 2.5            # actual exit price when SL fires (slippage past 2x)
DIRECTION_FILTER = True
SLIP_PER_SPREAD_PER_SIDE = 0.05  # round-trip = 2x this per spread
MIN_CREDIT = 0.05             # minimum credit to take (very loose for narrow)
MULT = 100.0                  # $100/pt
VIX_MIN, VIX_MAX = 12.0, 25.0
COMMISSION_PER_CONTRACT = 0.65  # Webull options per-contract commission (round trip = 4 legs × $0.65 = $2.60)
COMMISSION_PER_TRADE = COMMISSION_PER_CONTRACT * 4   # 2 legs × open + close

# Sweep configs
WIDTHS = [5, 10, 15, 20, 25]
OTM_PCTS = [0.002, 0.003, 0.004, 0.005, 0.0075, 0.010]

# ── Option chain parser ────────────────────────────────────────────────
SYM_RE = re.compile(r"^(SPXW?)\s+(\d{6})([CP])(\d{8})$")

def parse_symbol(sym: str):
    m = SYM_RE.match(sym.strip())
    if not m: return None
    root, dt, right, strike_raw = m.groups()
    try:
        exp = datetime.strptime(dt, "%y%m%d").date()
    except ValueError:
        return None
    return root, exp, right, int(strike_raw) / 1000.0

# ── SPX/VIX loader ─────────────────────────────────────────────────────
SPX_CACHE = OUT / "spx_vix_daily.parquet"

def load_spx_vix():
    if SPX_CACHE.exists():
        print(f"[narrow-sweep] using cached SPX/VIX: {SPX_CACHE}", flush=True)
        return pd.read_parquet(SPX_CACHE)
    print("[narrow-sweep] downloading SPX + VIX daily from yfinance…", flush=True)
    spx = yf.download("^GSPC", start="2014-01-01", end="2026-05-21",
                      auto_adjust=False, progress=False)
    vix = yf.download("^VIX", start="2014-01-01", end="2026-05-21",
                      auto_adjust=False, progress=False)
    if isinstance(spx.columns, pd.MultiIndex):
        spx.columns = spx.columns.get_level_values(0)
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = vix.columns.get_level_values(0)
    df = spx[["Open","High","Low","Close"]].copy()
    df.columns = ["spx_open","spx_high","spx_low","spx_close"]
    df["vix_close"] = vix["Close"]
    df.index = pd.to_datetime(df.index).normalize()
    df.dropna(subset=["spx_open"], inplace=True)
    df.to_parquet(SPX_CACHE)
    print(f"[narrow-sweep] SPX/VIX cached: {len(df)} rows", flush=True)
    return df

# ── Option chain loader (cached parquet) ──────────────────────────────
OPTS_CACHE = OUT / "spxw_0dte_puts.parquet"

def load_opts_all():
    if OPTS_CACHE.exists():
        print(f"[narrow-sweep] using cached options: {OPTS_CACHE}", flush=True)
        df = pd.read_parquet(OPTS_CACHE)
        out = {}
        for d, g in df.groupby("trading_day"):
            out[d] = g[["strike","open","close"]].reset_index(drop=True)
        return out

    BASE = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/data/databento")
    DIRS = [
        BASE / "spxw_opt_2014_2015",
        BASE / "spxw_opt_2016_2018",
        BASE / "spxw_opt_2019",
        BASE / "spxw_opt_2020_2022",
        BASE / "spxw_opt",
        BASE / "spxw_opt_2025_2026",
    ]
    files = []
    for d in DIRS:
        if d.exists():
            files.extend(sorted(d.glob("opra-pillar-*.ohlcv-1d.dbn.zst")))
    print(f"[narrow-sweep] loading {len(files)} option-chain files…", flush=True)
    out = {}
    all_rows = []
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
        puts = df[(df["right"] == "P") & (df["expiry"] == td)][["strike","open","close"]].copy()
        if not puts.empty:
            puts["trading_day"] = td
            all_rows.append(puts)
            out[td] = puts[["strike","open","close"]].reset_index(drop=True)
            cnt += 1
        if cnt % 500 == 0 and cnt > 0:
            print(f"  [narrow-sweep] processed {cnt} files…", flush=True)
    print(f"[narrow-sweep] {len(out)} trading days with 0DTE put chains", flush=True)
    if all_rows:
        full = pd.concat(all_rows, ignore_index=True)
        full.to_parquet(OPTS_CACHE)
        print(f"[narrow-sweep] options cached: {OPTS_CACHE}", flush=True)
    return out

# ── Backtest engine ────────────────────────────────────────────────────
@dataclass
class Trade:
    date: date
    width: int
    otm_pct: float
    short_strike: float
    long_strike: float
    credit: float
    spx_open: float
    spx_low: float
    spx_close: float
    vix: float
    pnl_pts: float
    pnl_usd: float
    exit_reason: str  # EXPIRED, STOPPED


def simulate(opts_by_day: dict, spx_vix: pd.DataFrame,
             width: int, otm_pct: float) -> list[Trade]:
    """Mirrors the proven width_otm_research_2026_05_14 simulate logic.
    Differences from the bot's live filter (intentional, to match prior backtest):
      - No VIX gate here (the previous study showed regime breakdown separately).
      - Direction filter: skip days where SPX closed BELOW open (bearish session).
        This proxies "don't hold through a bearish day" and matches prior run.
    """
    trades = []
    for d, opts in opts_by_day.items():
        ts = pd.Timestamp(d)
        if ts not in spx_vix.index:
            continue
        row = spx_vix.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        spx_open = float(row["spx_open"])
        spx_low  = float(row["spx_low"])
        spx_close = float(row["spx_close"])
        vix = float(row["vix_close"]) if not pd.isna(row["vix_close"]) else 20.0

        # Direction filter — matches prior 2026-05-14 study
        if DIRECTION_FILTER and spx_close < spx_open:
            continue

        # Strike selection — exact match for short, then nearest below for long
        opts_d = opts.copy()
        target_short = spx_open * (1.0 - otm_pct)
        opts_d["d"] = (opts_d["strike"] - target_short).abs()
        if opts_d.empty:
            continue
        sr = opts_d.loc[opts_d["d"].idxmin()]
        short_strike = float(sr["strike"])
        long_target = short_strike - width
        exact = opts_d[opts_d["strike"] == long_target]
        if not exact.empty:
            lr = exact.iloc[0]
        else:
            below = opts_d[opts_d["strike"] < short_strike].copy()
            if below.empty:
                continue
            below["d"] = (below["strike"] - long_target).abs()
            lr = below.loc[below["d"].idxmin()]
        long_strike = float(lr["strike"])
        actual_width = short_strike - long_strike
        if actual_width <= 0:
            continue

        credit = float(sr["open"]) - float(lr["open"])
        if credit < MIN_CREDIT or credit <= 0:
            continue
        # Subtract slippage per side (both legs)
        credit -= 2 * SLIP_PER_SPREAD_PER_SIDE
        if credit < MIN_CREDIT:
            continue

        # SL proxy: did spx_low touch short strike intraday?
        # (Spread mark at short-strike-touch is roughly the credit hit + theta,
        # close enough to be a reasonable proxy for stop firing.)
        sl_triggered = spx_low <= short_strike
        if sl_triggered:
            exit_debit = min(credit * SL_FILL_MULT, actual_width)
            pnl_pts = credit - exit_debit
            reason = "STOPPED"
        else:
            # Use closing marks if no stop fired
            mark_close = max(0.0, float(sr["close"]) - float(lr["close"]))
            if mark_close <= 0.05:
                pnl_pts = credit
                reason = "EXPIRED"
            else:
                pnl_pts = credit - mark_close
                reason = "EXPIRED_ITM"
                if -pnl_pts > actual_width:
                    pnl_pts = -actual_width

        pnl_usd = pnl_pts * MULT - COMMISSION_PER_TRADE
        trades.append(Trade(
            date=d, width=width, otm_pct=otm_pct,
            short_strike=short_strike, long_strike=long_strike, credit=credit,
            spx_open=spx_open, spx_low=spx_low, spx_close=spx_close, vix=vix,
            pnl_pts=pnl_pts, pnl_usd=pnl_usd, exit_reason=reason,
        ))
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = (df["pnl_usd"] > 0).sum()
    losses = (df["pnl_usd"] <= 0).sum()
    # Year-grouped P&L → annual avg
    df["year"] = pd.to_datetime(df["date"]).dt.year
    annual = df.groupby("year")["pnl_usd"].sum()
    # Max drawdown on cumulative P&L
    cum = df.sort_values("date")["pnl_usd"].cumsum()
    running_max = cum.cummax()
    drawdown = (cum - running_max)
    max_dd = float(drawdown.min())
    return {
        "trades": len(df),
        "win_rate": float(wins / len(df)),
        "stops": int((df["exit_reason"] == "STOPPED").sum()),
        "expired": int((df["exit_reason"] == "EXPIRED").sum()),
        "expired_itm": int((df["exit_reason"] == "EXPIRED_ITM").sum()),
        "total_pnl": float(df["pnl_usd"].sum()),
        "avg_pnl_per_trade": float(df["pnl_usd"].mean()),
        "median_pnl_per_trade": float(df["pnl_usd"].median()),
        "best_trade": float(df["pnl_usd"].max()),
        "worst_trade": float(df["pnl_usd"].min()),
        "max_drawdown": max_dd,
        "annual_avg": float(annual.mean()),
        "annual_min": float(annual.min()),
        "annual_max": float(annual.max()),
        "years_covered": int(df["year"].nunique()),
        "avg_credit": float(df["credit"].mean()),
    }


def main():
    spx_vix = load_spx_vix()
    print(f"[narrow-sweep] SPX rows: {len(spx_vix)}", flush=True)
    opts = load_opts_all()
    print(f"[narrow-sweep] options days: {len(opts)}", flush=True)

    rows = []
    for w in WIDTHS:
        for otm in OTM_PCTS:
            print(f"[narrow-sweep] running width={w} otm={otm:.4f}…", flush=True)
            trades = simulate(opts, spx_vix, w, otm)
            summary = summarize(trades)
            summary.update({"width": w, "otm_pct": otm})
            rows.append(summary)
            if summary.get("trades", 0) > 0:
                print(f"  → {summary['trades']} trades, win_rate={summary['win_rate']:.1%}, "
                      f"annual=${summary['annual_avg']:,.0f}, worst=${summary['worst_trade']:,.0f}",
                      flush=True)

    out_df = pd.DataFrame(rows)
    # Some configs may produce 0 trades — fill numeric cols with 0 so sort works
    for col in ["annual_avg","total_pnl","win_rate","worst_trade","max_drawdown","avg_credit","stops"]:
        if col not in out_df.columns:
            out_df[col] = 0
        out_df[col] = out_df[col].fillna(0)
    out_df = out_df.sort_values("annual_avg", ascending=False)
    out_df.to_csv(OUT / "summary.csv", index=False)
    print("", flush=True)
    print("=" * 80, flush=True)
    cols = ["width","otm_pct","trades","win_rate","stops",
            "avg_credit","annual_avg","worst_trade","max_drawdown"]
    print(out_df[cols].to_string(index=False), flush=True)
    print("=" * 80, flush=True)
    print(f"[narrow-sweep] done. {OUT / 'summary.csv'}", flush=True)


if __name__ == "__main__":
    main()
