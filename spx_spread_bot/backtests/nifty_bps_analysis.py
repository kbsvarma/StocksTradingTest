"""
Nifty 50 Weekly BPS Backtest (2014-2026)
=========================================
Mirrors the SPX vG analysis for India:
  - Sell weekly put 1%/1.5%/2% OTM, buy 300/500pt lower
  - Entry: Thursday open | Exit: Thursday close (weekly settlement)
  - Credit estimated via Black-Scholes with India VIX as IV proxy
  - India VIX filters identical to SPX vG logic
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
import yfinance as yf
import pandas as pd
import numpy as np

# ── Black-Scholes put price ───────────────────────────────────────────────────
def bs_put(S: float, K: float, T: float, sigma: float, r: float = 0.065) -> float:
    """India risk-free ≈ 6.5% (10yr G-Sec average)."""
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    Nd1 = 0.5 * (1 + math.erf(-d1 / math.sqrt(2)))   # N(-d1)
    Nd2 = 0.5 * (1 + math.erf(-d2 / math.sqrt(2)))   # N(-d2)
    return K * math.exp(-r * T) * Nd2 - S * Nd1

def bps_credit(S: float, short_put: float, long_put: float,
               T: float, sigma: float) -> float:
    short_val = bs_put(S, short_put, T, sigma)
    long_val  = bs_put(S, long_put,  T, sigma)
    return max(short_val - long_val, 0.0)

# ── Config ────────────────────────────────────────────────────────────────────
OTM_PCTS   = [0.010, 0.015, 0.020]   # 1%, 1.5%, 2% OTM
WIDTHS     = [300, 500]               # points (50pt strike intervals on NSE)
STRIKE_INT = 50                       # NSE Nifty strike step
LOT_SIZE   = 25                       # 1 lot = 25 units, ₹25/point
MIN_CREDIT = 10.0                     # min credit in points (~₹250/lot)
STOP_MULT  = 2.0                      # exit at 2× credit
PROFIT_PCT = 0.50                     # take-profit at 50% of credit

# India VIX filter variants (parallels SPX vA-vG)
VARIANTS = [
    dict(name="vN-A", vix_max=30, vix_rise_max=0),    # no filter
    dict(name="vN-B", vix_max=25, vix_rise_max=0),    # VIX<25
    dict(name="vN-C", vix_max=20, vix_rise_max=0),    # VIX<20
    dict(name="vN-D", vix_max=30, vix_rise_max=3),    # not rising >3
    dict(name="vN-E", vix_max=25, vix_rise_max=3),    # VIX<25 + not rising >3 (≈ SPX vG)
    dict(name="vN-F", vix_max=20, vix_rise_max=3),    # VIX<20 + not rising >3
]

# ── Load data ─────────────────────────────────────────────────────────────────
print("Downloading Nifty 50 and India VIX …")
nifty_raw = yf.download("^NSEI",     start="2014-01-01", end="2026-05-09",
                         auto_adjust=True, progress=False)
ivix_raw  = yf.download("^INDIAVIX", start="2014-01-01", end="2026-05-09",
                         auto_adjust=True, progress=False)

nifty_raw.columns = nifty_raw.columns.get_level_values(0)
ivix_raw.columns  = ivix_raw.columns.get_level_values(0)

# Keep only Thursdays (weekly expiry), align
nifty = nifty_raw[nifty_raw.index.dayofweek == 3].copy()
ivix  = ivix_raw.reindex(nifty.index, method="ffill")

# VIX daily change (open – prev close, proxy for intraday rise)
ivix_prev = ivix_raw["Close"].shift(1).reindex(nifty.index, method="ffill")
vix_rise  = (ivix["Open"] - ivix_prev).fillna(0)

print(f"Nifty Thursdays: {len(nifty)}")

# ── Backtest loop ─────────────────────────────────────────────────────────────
@dataclass
class Trade:
    year:        int
    date:        str
    nifty_open:  float
    nifty_close: float
    short_put:   float
    long_put:    float
    credit:      float
    pnl:         float
    win:         bool

def nearest_strike(price: float, step: int = STRIKE_INT) -> int:
    return int(round(price / step) * step)

def run_variant(otm_pct: float, width: int,
                vix_max: float, vix_rise_max: float) -> list[Trade]:
    trades: list[Trade] = []
    T = 1 / 52   # ~1 week to expiry

    for dt in nifty.index:
        row   = nifty.loc[dt]
        S_open  = float(row["Open"])
        S_close = float(row["Close"])
        if S_open <= 0 or S_close <= 0:
            continue

        vix = float(ivix.loc[dt, "Close"])
        if pd.isna(vix) or vix > vix_max:
            continue
        if vix_rise_max > 0:
            rise = float(vix_rise.loc[dt]) if dt in vix_rise.index else 0.0
            if rise > vix_rise_max:
                continue

        sigma = vix / 100.0   # annualised vol

        # Strikes
        short_target = S_open * (1 - otm_pct)
        short_put    = nearest_strike(short_target)
        long_put     = short_put - width
        if long_put <= 0:
            continue

        credit = bps_credit(S_open, short_put, long_put, T, sigma)
        if credit < MIN_CREDIT:
            continue

        # Settlement P&L
        if S_close <= long_put:
            pnl = credit - width   # max loss
        elif S_close <= short_put:
            pnl = credit - (short_put - S_close)
        else:
            pnl = credit            # full profit

        # Stop & profit-target (intraday approximation via low/high)
        day_low  = float(row["Low"])
        day_high = float(row["High"])

        stop_level   = short_put - (short_put - long_put) * STOP_MULT * credit / width
        profit_level = short_put - credit * (1 - PROFIT_PCT)

        # Simplified: if low breaches stop trigger (spread widens 2×), take loss
        if day_low < stop_level:
            pnl = -credit * STOP_MULT
        elif day_high > profit_level:
            pnl = credit * PROFIT_PCT

        win = pnl > 0
        trades.append(Trade(
            year=dt.year, date=str(dt.date()),
            nifty_open=S_open, nifty_close=S_close,
            short_put=short_put, long_put=long_put,
            credit=credit, pnl=pnl, win=win,
        ))
    return trades

# ── Run all combos ────────────────────────────────────────────────────────────
print("\nRunning backtest across all configurations …\n")

results = {}
for otm in OTM_PCTS:
    for w in WIDTHS:
        for v in VARIANTS:
            key = (otm, w, v["name"])
            trades = run_variant(otm, w, v["vix_max"], v["vix_rise_max"])
            results[key] = trades

# ── Summary table (best combo per variant) ───────────────────────────────────
YEARS = list(range(2014, 2027))

def metrics(trades: list[Trade]):
    if not trades:
        return dict(n=0, wr=0, pnl_yr=0, maxdd=0, sharpe=0)
    df = pd.DataFrame([t.__dict__ for t in trades])
    n_yr = len(df) / 12   # 12 years
    pnl_yr = df["pnl"].sum() / 12
    wr = df["win"].mean() * 100
    # Max drawdown
    cum = df["pnl"].cumsum()
    roll_max = cum.cummax()
    dd = (cum - roll_max).min()
    # Sharpe (annual)
    yr_pnls = df.groupby("year")["pnl"].sum()
    sharpe = yr_pnls.mean() / yr_pnls.std() if yr_pnls.std() > 0 else 0
    return dict(n=len(df), n_yr=round(n_yr), wr=wr, pnl_yr=pnl_yr, maxdd=dd, sharpe=sharpe)

print("="*90)
print("NIFTY 50 WEEKLY BPS — SUMMARY (1 lot = 25 units, P&L in index points)")
print("="*90)
print(f"  {'Config':<14} {'OTM':>5} {'Width':>6} {'Trades/yr':>10} {'WR%':>7} "
      f"{'Pts/yr':>10} {'MaxDD':>9} {'Sharpe':>8}")
print("  " + "─"*80)

best_rows = []
for v in VARIANTS:
    best_sharpe = -999
    best_row = None
    for otm in OTM_PCTS:
        for w in WIDTHS:
            key = (otm, w, v["name"])
            m = metrics(results[key])
            if m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_row = (v["name"], otm, w, m)
    if best_row:
        name, otm, w, m = best_row
        best_rows.append(best_row)
        mark = " ◀" if name == "vN-E" else ""
        print(f"  {name:<14} {otm*100:>4.1f}% {w:>6}pt {m['n_yr']:>10} {m['wr']:>7.1f}% "
              f"{m['pnl_yr']:>10.0f} {m['maxdd']:>9.0f} {m['sharpe']:>8.2f}{mark}")

# ── Year-by-year for vN-E (≈ SPX vG equivalent) ──────────────────────────────
print("\n" + "="*70)
print("YEAR-BY-YEAR — vN-E (VIX<25 + not rising >3pts) — best config")
print("="*70)

best_vne = None
best_sharpe = -999
for otm in OTM_PCTS:
    for w in WIDTHS:
        key = (otm, w, "vN-E")
        m = metrics(results[key])
        if m["sharpe"] > best_sharpe:
            best_sharpe = m["sharpe"]
            best_vne = (otm, w, results[key])

otm_b, w_b, trades_b = best_vne
df_b = pd.DataFrame([t.__dict__ for t in trades_b])
print(f"  Parameters: {otm_b*100:.1f}% OTM / {w_b}pt width\n")
print(f"  {'Year':>6} {'Trades':>7} {'WR%':>7} {'P&L (pts)':>12} {'Max Loss':>10}")
print("  " + "─"*50)
annual_pnl = []
for yr in YEARS:
    yr_df = df_b[df_b["year"] == yr]
    if len(yr_df) == 0:
        continue
    yr_pnl = yr_df["pnl"].sum()
    yr_wr  = yr_df["win"].mean() * 100
    yr_dd  = yr_df["pnl"].cumsum().sub(yr_df["pnl"].cumsum().cummax()).min()
    flag = "  ⚠️" if yr_pnl < 0 else ""
    print(f"  {yr:>6} {len(yr_df):>7} {yr_wr:>7.1f}% {yr_pnl:>12.0f} {yr_dd:>10.0f}{flag}")
    annual_pnl.append(yr_pnl)

print("  " + "─"*50)
avg = np.mean(annual_pnl)
print(f"  Avg/yr: {avg:.0f} pts/lot  |  ₹{avg*25:,.0f}/lot  |  ₹{avg*25*10:,.0f} (10 lots)")

print("\n")
print("  NOTE: 1 lot = 25 units × ₹25/pt = ₹625 per point of P&L")
print(f"  At 10 lots: 1 pt = ₹250  |  Avg annual = ₹{avg*25*10:,.0f}")

# ── Key differences vs SPX ───────────────────────────────────────────────────
print("\n" + "="*70)
print("KEY DIFFERENCES vs SPX vG")
print("="*70)
print(f"  Nifty current level : ~24,000  (SPX ~5,600)")
print(f"  Option lot size     : 25 units  (SPX: 100 multiplier)")
print(f"  Strike interval     : 50 pts    (SPX: 5 pts)")
print(f"  Expiry              : Weekly Thursday  (SPX: 0DTE daily)")
print(f"  India VIX mean      : {ivix_raw['Close'].mean():.1f}    (US VIX mean ~16)")
print(f"  India VIX at 25     : {(ivix_raw['Close']<25).mean()*100:.0f}% of days below (vs SPX: ~95%)")
print(f"  Broker              : Zerodha/Upstox (not IBKR for retail)")
print(f"  Settlement          : Cash, European style")
