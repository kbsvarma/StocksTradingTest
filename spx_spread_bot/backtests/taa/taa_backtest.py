"""GEM (Antonacci) + Faber GTAA — backtest of two PUBLISHED, documented strategies.

These aren't my inventions — they have real published track records, which is
exactly why they're worth testing: if our honest backtest replicates the
documented low-drawdown profile (esp. 2008), that's genuine validation, not a
curve-fit. If it doesn't, that's honest too.

GEM (Global Equities Momentum), monthly:
  - 12-month total return is the signal.
  - Absolute momentum: if SPY 12mo return > cash(SHY) 12mo return -> equities ON.
      then relative momentum: hold max(SPY, EFA) by 12mo return.
  - else -> hold AGG (bonds).

Faber GTAA (5-asset), monthly:
  - assets: SPY, EFA, IEF, DBC, VNQ. Each sleeve = 1/5.
  - if asset price > its 10-month SMA -> hold sleeve in asset, else in cash (SHY).
  - equal weight, monthly rebalance.

Benchmarks: SPY buy & hold. Reports CAGR, max DD, Sharpe, per-year (esp crashes).
"""
import pandas as pd, numpy as np
from pathlib import Path

DATA = Path(__file__).parent / "data"

def load_monthly(t):
    s = pd.read_parquet(DATA / f"{t}.parquet")["Close"]
    s.index = pd.to_datetime(s.index)
    return s.resample("ME").last()  # month-end

# build monthly panel
tickers = ["SPY","EFA","IEF","AGG","DBC","VNQ","SHY"]
px = pd.DataFrame({t: load_monthly(t) for t in tickers}).dropna(how="all")
ret = px.pct_change()

# common start = when all GEM+Faber assets exist (VNQ 2004-09, DBC 2006-02)
start = "2006-06-30"
px = px.loc[start:]; ret = ret.loc[start:]
mom12 = px / px.shift(12) - 1   # trailing 12-month return
sma10 = px.rolling(10).mean()

def run_gem():
    eq = [1.0]; dates = [px.index[12]]; holdings=[]
    idx = px.index
    for i in range(12, len(idx)-1):
        # decide at month i (using data through i), earn return over i->i+1
        spy_m = mom12["SPY"].iloc[i]; cash_m = mom12["SHY"].iloc[i]
        efa_m = mom12["EFA"].iloc[i]
        if pd.isna(spy_m): hold="SHY"
        elif spy_m > cash_m:
            hold = "SPY" if spy_m >= efa_m else "EFA"
        else:
            hold = "AGG"
        r = ret[hold].iloc[i+1]
        eq.append(eq[-1]*(1+ (0 if pd.isna(r) else r)))
        dates.append(idx[i+1]); holdings.append(hold)
    return pd.Series(eq[1:], index=dates[1:]), holdings

def run_faber():
    sleeves = ["SPY","EFA","IEF","DBC","VNQ"]
    eq=[1.0]; dates=[px.index[10]]; idx=px.index
    for i in range(10, len(idx)-1):
        contrib=0.0
        for a in sleeves:
            on = px[a].iloc[i] > sma10[a].iloc[i] if not pd.isna(sma10[a].iloc[i]) else False
            r = ret[a].iloc[i+1] if on else ret["SHY"].iloc[i+1]
            r = 0 if pd.isna(r) else r
            contrib += 0.2*r
        eq.append(eq[-1]*(1+contrib)); dates.append(idx[i+1])
    return pd.Series(eq[1:], index=dates[1:])

def run_bah(t="SPY"):
    s = px[t].dropna()
    s = s.loc[px.index[12]:]
    return s/s.iloc[0]

def stats(eq, label):
    yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    mret=eq.pct_change().dropna()
    vol=mret.std()*np.sqrt(12); sharpe=(cagr-0.02)/vol if vol>0 else 0
    print(f"  {label:<26} CAGR {cagr*100:+5.1f}%/yr   maxDD {dd*100:6.1f}%   vol {vol*100:4.0f}%   Sharpe {sharpe:.2f}")
    return eq

gem,holds = run_gem()
faber = run_faber()
bah = run_bah("SPY")
# align
common = gem.index.intersection(faber.index).intersection(bah.index)
gem=gem.loc[common]/gem.loc[common].iloc[0]
faber=faber.loc[common]/faber.loc[common].iloc[0]
bah=bah.loc[common]/bah.loc[common].iloc[0]

print("="*80)
print(f"PUBLISHED TAA STRATEGIES — monthly, {common[0].date()} to {common[-1].date()}")
print("="*80)
stats(gem,"GEM (Antonacci)")
stats(faber,"Faber GTAA 5-asset")
stats(bah,"SPY buy & hold")

print("\n--- Per-year total return (the crash years are the point) ---")
print(f"  {'Year':<6}{'GEM':<10}{'Faber':<10}{'SPY B&H':<10}")
print("  "+"-"*36)
for yr in range(common[0].year, common[-1].year+1):
    def yrret(s):
        sy=s[s.index.year==yr]
        if len(sy)<2: return np.nan
        prev=s[s.index<sy.index[0]]
        base=prev.iloc[-1] if len(prev) else sy.iloc[0]
        return (sy.iloc[-1]/base-1)*100
    g,f,b=yrret(gem),yrret(faber),yrret(bah)
    fmt=lambda x: f"{x:+.1f}%" if not np.isnan(x) else "  -"
    star = " <-- crash" if yr in (2008,2020,2022) else ""
    print(f"  {yr:<6}{fmt(g):<10}{fmt(f):<10}{fmt(b):<10}{star}")

# what GEM held over time (sanity)
from collections import Counter
print(f"\n  GEM holding distribution: {dict(Counter(holds))}")
