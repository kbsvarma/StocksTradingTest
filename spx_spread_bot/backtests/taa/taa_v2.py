"""TAA v2 — dig deeper on the Faber/GEM result.

Theory-driven variants (NOT a blind overfit sweep — each tweak addresses a
specific finding):
  1. GEM-CASH: replace AGG (agg bonds) with SHY (cash) as the defensive asset.
     Rationale: GEM failed in 2022 because bonds fell WITH stocks. Cash can't.
  2. Faber SMA-length robustness: 6/8/10/12-month. If only 10 works -> overfit.
     If all work -> robust. (anti-overfit check)
  3. GEM-cash + Faber 50/50 combo: does blending smooth both?
Reports per-year with 2022 highlighted (the wart we're trying to fix).
"""
import pandas as pd, numpy as np
from pathlib import Path
DATA = Path(__file__).parent / "data"

def load_monthly(t):
    s = pd.read_parquet(DATA/f"{t}.parquet")["Close"]; s.index=pd.to_datetime(s.index)
    return s.resample("ME").last()

tickers=["SPY","EFA","IEF","AGG","DBC","VNQ","SHY"]
px=pd.DataFrame({t:load_monthly(t) for t in tickers}).loc["2006-06-30":]
ret=px.pct_change()
mom12=px/px.shift(12)-1

def gem(defensive="AGG"):
    idx=px.index; eq=[1.0]; dts=[idx[12]]
    for i in range(12,len(idx)-1):
        spy,cash,efa=mom12["SPY"].iloc[i],mom12["SHY"].iloc[i],mom12["EFA"].iloc[i]
        if pd.isna(spy): h="SHY"
        elif spy>cash: h="SPY" if spy>=efa else "EFA"
        else: h=defensive
        r=ret[h].iloc[i+1]; eq.append(eq[-1]*(1+(0 if pd.isna(r) else r))); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def faber(sma_len=10):
    sleeves=["SPY","EFA","IEF","DBC","VNQ"]; sma=px.rolling(sma_len).mean()
    idx=px.index; eq=[1.0]; dts=[idx[sma_len]]
    for i in range(sma_len,len(idx)-1):
        c=0.0
        for a in sleeves:
            on = px[a].iloc[i]>sma[a].iloc[i] if not pd.isna(sma[a].iloc[i]) else False
            r=ret[a].iloc[i+1] if on else ret["SHY"].iloc[i+1]
            c+=0.2*(0 if pd.isna(r) else r)
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def bah(t="SPY"):
    s=px[t].dropna().loc[px.index[12]:]; return s/s.iloc[0]

def stats(eq,label):
    eq=eq/eq.iloc[0]; yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    vol=eq.pct_change().dropna().std()*np.sqrt(12)
    sharpe=(cagr-0.02)/vol if vol>0 else 0
    yr22=None
    s22=eq[eq.index.year==2022]
    if len(s22)>1:
        prev=eq[eq.index<s22.index[0]]; base=prev.iloc[-1] if len(prev) else s22.iloc[0]
        yr22=(s22.iloc[-1]/base-1)*100
    print(f"  {label:<28} CAGR {cagr*100:+5.1f}%  maxDD {dd*100:6.1f}%  vol {vol*100:4.0f}%  Sharpe {sharpe:.2f}  2022:{yr22:+5.1f}%")
    return eq

print("="*92)
print("TAA v2 — variant tests, 2007-2026 (2022 = the wart we're attacking)")
print("="*92)
g_bonds=stats(gem("AGG"),"GEM (bonds defensive)")
g_cash=stats(gem("SHY"),"GEM-CASH (cash defensive)")
print()
for L in (6,8,10,12):
    stats(faber(L),f"Faber GTAA SMA-{L}")
print()
f10=faber(10)
# 50/50 combo of GEM-cash and Faber-10 (rebalanced monthly on returns)
common=g_cash.index.intersection(f10.index)
gc=g_cash.loc[common]/g_cash.loc[common].iloc[0]; ff=f10.loc[common]/f10.loc[common].iloc[0]
combo_ret=0.5*gc.pct_change().fillna(0)+0.5*ff.pct_change().fillna(0)
combo=(1+combo_ret).cumprod()
stats(combo,"50/50 GEM-cash + Faber")
stats(bah("SPY"),"SPY buy & hold")

print("\n--- Per-year: does GEM-CASH fix 2022 vs GEM-bonds? ---")
print(f"  {'Year':<6}{'GEM-bonds':<12}{'GEM-cash':<12}{'Faber-10':<12}{'Combo':<10}{'SPY':<10}")
print("  "+"-"*56)
gb=g_bonds/g_bonds.iloc[0]; b=bah("SPY")
for yr in range(2007,2027):
    def yr_ret(s):
        sy=s[s.index.year==yr]
        if len(sy)<2: return np.nan
        prev=s[s.index<sy.index[0]]; base=prev.iloc[-1] if len(prev) else sy.iloc[0]
        return (sy.iloc[-1]/base-1)*100
    vals=[yr_ret(gb),yr_ret(gc),yr_ret(ff),yr_ret(combo),yr_ret(b/b.iloc[0])]
    fmt=lambda x: f"{x:+.1f}%" if not np.isnan(x) else "  -"
    star=" <--" if yr in (2008,2022) else ""
    print(f"  {yr:<6}"+"".join(f"{fmt(v):<12}" if i<4 else f"{fmt(v):<10}" for i,v in enumerate(vals))+star)
