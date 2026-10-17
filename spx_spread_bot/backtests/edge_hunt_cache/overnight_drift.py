"""Overnight drift anomaly test on SPY 2013-2026.

Decompose total return into:
  - Overnight: Close[t-1] -> Open[t]   (hold through the night)
  - Intraday:  Open[t]    -> Close[t]  (hold through the day)
  - Buy&Hold:  Close[t-1] -> Close[t]  (always invested)

Compare cumulative growth, annualized return, Sharpe, max drawdown.
"""
import pandas as pd
import numpy as np

spy = pd.read_parquet("/tmp/spy_ohlc.parquet")
spy = spy[["Open","Close"]].copy()
spy["prev_close"] = spy["Close"].shift(1)
spy = spy.dropna()

# Daily returns for each leg
spy["r_overnight"] = spy["Open"] / spy["prev_close"] - 1
spy["r_intraday"]  = spy["Close"] / spy["Open"] - 1
spy["r_bh"]        = spy["Close"] / spy["prev_close"] - 1

def stats(returns, label, ann_factor=252):
    r = returns.dropna()
    n = len(r)
    total = (1 + r).prod() - 1
    yrs = n / ann_factor
    cagr = (1 + total) ** (1/yrs) - 1
    vol = r.std() * np.sqrt(ann_factor)
    sharpe = (r.mean() * ann_factor) / vol if vol > 0 else 0
    # max drawdown
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    win = (r > 0).mean()
    print(f"  {label:12s}: total={total*100:+8.1f}%  CAGR={cagr*100:+6.2f}%  "
          f"vol={vol*100:5.1f}%  Sharpe={sharpe:5.2f}  maxDD={dd*100:6.1f}%  win={win*100:.1f}%  avg={r.mean()*10000:+.1f}bp")
    return {"label":label, "total":total, "cagr":cagr, "sharpe":sharpe, "dd":dd, "n":n}

print("="*110)
print("OVERNIGHT DRIFT ANOMALY — SPY 2013-2026 (price-only, no dividends, no costs)")
print("="*110)
print(f"\n{len(spy)} trading days\n")
print("OVERALL:")
stats(spy["r_overnight"], "Overnight")
stats(spy["r_intraday"],  "Intraday")
stats(spy["r_bh"],        "Buy&Hold")

print("\nPER-YEAR cumulative return (%):")
spy["year"] = spy.index.year
print(f"  {'year':<6}{'overnight':>12}{'intraday':>12}{'buy&hold':>12}")
for y, g in spy.groupby("year"):
    on = ((1+g['r_overnight']).prod()-1)*100
    intr = ((1+g['r_intraday']).prod()-1)*100
    bh = ((1+g['r_bh']).prod()-1)*100
    print(f"  {y:<6}{on:>11.1f}%{intr:>11.1f}%{bh:>11.1f}%")

# Friction analysis for tradeable overnight strategy
print("\n" + "="*110)
print("FRICTION CHECK — overnight strategy requires daily round-trip (buy MOC, sell MOO)")
print("="*110)
n = len(spy)
on_total = (1+spy["r_overnight"]).prod()-1
yrs = n/252
on_cagr = (1+on_total)**(1/yrs)-1
for cost_bp in [0, 0.5, 1.0, 2.0]:
    # 2 transactions per day (buy + sell), cost_bp each
    daily_cost = 2 * cost_bp / 10000
    r_net = spy["r_overnight"] - daily_cost
    total_net = (1+r_net).prod()-1
    cagr_net = (1+total_net)**(1/yrs)-1
    print(f"  cost={cost_bp}bp/side: net CAGR={cagr_net*100:+6.2f}%  (gross {on_cagr*100:+.2f}%)")
