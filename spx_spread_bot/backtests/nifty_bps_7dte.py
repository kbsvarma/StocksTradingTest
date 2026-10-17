"""
Nifty 50 Weekly BPS — 7DTE Backtest (2014-2026)
=================================================
Enter Thursday open with NEXT Thursday expiry (7DTE / 5 trading days).
Monitor at END OF DAY each day — stop/profit decisions on closing prices only.
This matches how weekly options are actually managed.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
import yfinance as yf
import pandas as pd
import numpy as np

# ── Black-Scholes ─────────────────────────────────────────────────────────────
def bs_put(S: float, K: float, T: float, sigma: float, r: float = 0.065) -> float:
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    Nd1 = 0.5*(1 + math.erf(-d1/math.sqrt(2)))
    Nd2 = 0.5*(1 + math.erf(-d2/math.sqrt(2)))
    return K*math.exp(-r*T)*Nd2 - S*Nd1

def spread_value(S, short_put, long_put, T, sigma):
    return max(bs_put(S, short_put, T, sigma) - bs_put(S, long_put, T, sigma), 0.0)

# ── Config ────────────────────────────────────────────────────────────────────
OTM_PCTS   = [0.010, 0.015, 0.020]
WIDTHS     = [300, 500]
STRIKE_INT = 50
LOT_SIZE   = 25
MIN_CREDIT = 20.0
STOP_MULT  = 2.0      # close position if EOD spread value >= 2× initial credit
PROFIT_PCT = 0.50     # take profit if EOD spread value <= 50% of initial credit

VARIANTS = [
    dict(name="vN-A", vix_max=30, vix_rise_max=0),
    dict(name="vN-B", vix_max=25, vix_rise_max=0),
    dict(name="vN-C", vix_max=20, vix_rise_max=0),
    dict(name="vN-D", vix_max=30, vix_rise_max=3),
    dict(name="vN-E", vix_max=25, vix_rise_max=3),
    dict(name="vN-F", vix_max=20, vix_rise_max=3),
]

# ── Load data ─────────────────────────────────────────────────────────────────
print("Downloading Nifty 50 and India VIX …")
nifty_raw = yf.download("^NSEI",     start="2014-01-01", end="2026-05-09",
                         auto_adjust=True, progress=False)
ivix_raw  = yf.download("^INDIAVIX", start="2014-01-01", end="2026-05-09",
                         auto_adjust=True, progress=False)
nifty_raw.columns = nifty_raw.columns.get_level_values(0)
ivix_raw.columns  = ivix_raw.columns.get_level_values(0)

all_dates = nifty_raw.index
thursdays = all_dates[all_dates.dayofweek == 3]

ivix_prev_close = ivix_raw["Close"].shift(1)
vix_rise        = (ivix_raw["Open"] - ivix_prev_close).fillna(0)

print(f"Total Thursdays: {len(thursdays)}")

def nearest_strike(price, step=STRIKE_INT):
    return int(round(price / step) * step)

# ── Trade simulation (EOD monitoring) ────────────────────────────────────────
@dataclass
class Trade:
    year: int; entry_date: str; expiry_date: str
    entry_price: float; short_put: float; long_put: float
    credit: float; pnl: float; win: bool; exit_reason: str; days_held: int

def simulate_trade(entry_dt, expiry_dt, short_put, long_put, credit):
    width = short_put - long_put
    mask  = (all_dates > entry_dt) & (all_dates <= expiry_dt)
    trade_days = all_dates[mask]

    for i, dt in enumerate(trade_days):
        if dt not in nifty_raw.index:
            continue

        S_close = float(nifty_raw.loc[dt, "Close"])
        vix_eod = float(ivix_raw.loc[dt, "Close"]) if dt in ivix_raw.index else 0.18
        sigma   = max(vix_eod / 100.0, 0.08)

        # Remaining trading days AFTER today
        remaining = len(trade_days) - i - 1
        T_rem = max(remaining / 252, 0.5/252)   # minimum half-day

        is_expiry = (dt == expiry_dt) or (remaining == 0)

        if is_expiry:
            # Cash settlement at close
            if S_close <= long_put:
                pnl = credit - width
            elif S_close <= short_put:
                pnl = credit - (short_put - S_close)
            else:
                pnl = credit
            return pnl, pnl > 0, "expiry", i + 1

        # EOD spread value using closing price
        sv = spread_value(S_close, short_put, long_put, T_rem, sigma)

        # Stop: spread has doubled in value (loss = initial credit)
        if sv >= credit * STOP_MULT:
            pnl = -(sv - credit)   # pay to close at current value
            return pnl, False, "stop", i + 1

        # Profit target: spread lost 50% of its value (keep 50% of credit)
        if sv <= credit * (1 - PROFIT_PCT):
            pnl = credit - sv
            return pnl, True, "profit_target", i + 1

    # Fallback to expiry
    S_last = float(nifty_raw.iloc[-1]["Close"])
    if S_last <= long_put:   pnl = credit - width
    elif S_last <= short_put: pnl = credit - (short_put - S_last)
    else:                     pnl = credit
    return pnl, pnl > 0, "expiry", len(trade_days)


def run_variant(otm_pct, width, vix_max, vix_rise_max):
    trades = []
    T_entry = 5 / 252

    for i, entry_dt in enumerate(thursdays[:-1]):
        expiry_dt = thursdays[i + 1]
        if entry_dt not in nifty_raw.index: continue

        S_open = float(nifty_raw.loc[entry_dt, "Open"])
        if S_open <= 0: continue
        if entry_dt not in ivix_raw.index: continue

        vix = float(ivix_raw.loc[entry_dt, "Close"])
        if pd.isna(vix) or vix > vix_max: continue
        if vix_rise_max > 0:
            rise = float(vix_rise.loc[entry_dt]) if entry_dt in vix_rise.index else 0.0
            if rise > vix_rise_max: continue

        sigma     = vix / 100.0
        short_put = nearest_strike(S_open * (1 - otm_pct))
        long_put  = short_put - width
        if long_put <= 0: continue

        credit = max(
            bs_put(S_open, short_put, T_entry, sigma) -
            bs_put(S_open, long_put,  T_entry, sigma), 0.0)
        if credit < MIN_CREDIT: continue

        pnl, win, reason, days = simulate_trade(entry_dt, expiry_dt,
                                                 short_put, long_put, credit)
        trades.append(Trade(
            year=entry_dt.year, entry_date=str(entry_dt.date()),
            expiry_date=str(expiry_dt.date()), entry_price=S_open,
            short_put=short_put, long_put=long_put,
            credit=credit, pnl=pnl, win=win, exit_reason=reason, days_held=days,
        ))
    return trades

# ── Run ───────────────────────────────────────────────────────────────────────
print("Running 7DTE EOD-monitor backtest …\n")
results = {}
for otm in OTM_PCTS:
    for w in WIDTHS:
        for v in VARIANTS:
            results[(otm, w, v["name"])] = run_variant(otm, w, v["vix_max"], v["vix_rise_max"])

# ── Metrics & display ─────────────────────────────────────────────────────────
def metrics(trades):
    if not trades: return dict(n=0, n_yr=0, wr=0, pnl_yr=0, maxdd=0, sharpe=0)
    df   = pd.DataFrame([t.__dict__ for t in trades])
    pnl_yr = df["pnl"].sum() / 12
    wr     = df["win"].mean() * 100
    cum    = df["pnl"].cumsum()
    dd     = (cum - cum.cummax()).min()
    yr_pnl = df.groupby("year")["pnl"].sum()
    sharpe = yr_pnl.mean() / yr_pnl.std() if yr_pnl.std() > 0 else 0
    return dict(n=len(df), n_yr=round(len(df)/12), wr=wr,
                pnl_yr=pnl_yr, maxdd=dd, sharpe=sharpe)

print("="*90)
print("NIFTY 50 WEEKLY BPS — 7DTE (EOD monitoring) | P&L in index points")
print("="*90)
print(f"  {'Config':<10} {'OTM':>5} {'Width':>6} {'Trades/yr':>10} {'WR%':>7} "
      f"{'Pts/yr':>10} {'MaxDD':>9} {'Sharpe':>8}")
print("  " + "─"*78)

best_per_variant = []
for v in VARIANTS:
    best_sharpe, best_row = -999, None
    for otm in OTM_PCTS:
        for w in WIDTHS:
            m = metrics(results[(otm, w, v["name"])])
            if m["sharpe"] > best_sharpe:
                best_sharpe, best_row = m["sharpe"], (v["name"], otm, w, m)
    if best_row:
        best_per_variant.append(best_row)
        name, otm, w, m = best_row
        mark = " ◀" if name == "vN-E" else ""
        print(f"  {name:<10} {otm*100:>4.1f}% {w:>6}pt {m['n_yr']:>10} {m['wr']:>7.1f}% "
              f"{m['pnl_yr']:>10.0f} {m['maxdd']:>9.0f} {m['sharpe']:>8.2f}{mark}")

# Best vN-E config
best_vne = max(
    [(otm, w, results[(otm, w, "vN-E")]) for otm in OTM_PCTS for w in WIDTHS],
    key=lambda x: metrics(x[2])["sharpe"])
otm_b, w_b, trades_b = best_vne
df_b = pd.DataFrame([t.__dict__ for t in trades_b])

print(f"\n{'='*70}")
print(f"YEAR-BY-YEAR — vN-E ({otm_b*100:.1f}% OTM / {w_b}pt) — 10 lots")
print(f"{'='*70}")
print(f"  {'Year':>6} {'Trades':>7} {'WR%':>7} {'Pts/yr':>10} {'₹ (10 lots)':>14}  Exits")
print("  " + "─"*65)
annual = []
for yr in range(2014, 2027):
    ydf = df_b[df_b["year"]==yr]
    if len(ydf)==0: continue
    p   = ydf["pnl"].sum()
    wr  = ydf["win"].mean()*100
    pt  = (ydf["exit_reason"]=="profit_target").sum()
    st  = (ydf["exit_reason"]=="stop").sum()
    ex  = ydf["exit_reason"].str.startswith("expiry").sum()
    flag= "  ⚠️" if p < 0 else ""
    print(f"  {yr:>6} {len(ydf):>7} {wr:>7.1f}% {p:>10.0f} "
          f"₹{p*LOT_SIZE*10:>10,.0f}  PT:{pt} Stop:{st} Exp:{ex}{flag}")
    annual.append(p)
print("  "+"─"*65)
avg = np.mean(annual)
print(f"  Avg/yr: {avg:.0f} pts = ₹{avg*LOT_SIZE:,.0f}/lot = ₹{avg*LOT_SIZE*10:,.0f} (10 lots)")

print(f"\n{'='*70}")
print("PER-TRADE — vN-E best config (10 lots)")
print(f"{'='*70}")
wins  = df_b[df_b["win"]]
loses = df_b[~df_b["win"]]
ac = df_b["credit"].mean()
aw = wins["pnl"].mean()  if len(wins)  else 0
al = loses["pnl"].mean() if len(loses) else 0
pt_pct  = (df_b["exit_reason"]=="profit_target").mean()*100
st_pct  = (df_b["exit_reason"]=="stop").mean()*100
ex_pct  = df_b["exit_reason"].str.startswith("expiry").mean()*100
avg_days= df_b["days_held"].mean()
print(f"  Avg credit collected : {ac:.0f} pts = ₹{ac*LOT_SIZE*10:,.0f} (10 lots)")
print(f"  Avg winning trade    : +{aw:.0f} pts = +₹{aw*LOT_SIZE*10:,.0f}")
print(f"  Avg losing trade     : {al:.0f} pts = -₹{-al*LOT_SIZE*10:,.0f}")
print(f"  Avg days held        : {avg_days:.1f} days")
print(f"  Exit via profit tgt  : {pt_pct:.0f}%")
print(f"  Exit via stop        : {st_pct:.0f}%")
print(f"  Exit at expiry       : {ex_pct:.0f}%")
print(f"\n  Compare to 0DTE (Thursday same-day):")
print(f"  0DTE avg/yr: 873 pts/lot = ₹{873*LOT_SIZE:,.0f}/lot = ₹{873*LOT_SIZE*10:,.0f} (10 lots)")
print(f"  7DTE avg/yr: {avg:.0f} pts/lot = ₹{avg*LOT_SIZE:,.0f}/lot = ₹{avg*LOT_SIZE*10:,.0f} (10 lots)")
