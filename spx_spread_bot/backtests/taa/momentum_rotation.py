"""Cross-sectional DUAL MOMENTUM rotation over a broad ETF universe.

The documented path to double-digit returns (Faber relative-strength /
Antonacci dual momentum, generalized):
  - Universe: ~22 ETFs (9 sectors, US size/style, intl, bonds, gold, commodities, REIT).
  - Momentum score = average of 3/6/12-month total returns (robust blend).
  - Each month: rank universe by momentum, hold the TOP-N equal weight.
  - ABSOLUTE filter (dual momentum): a held slot only stays invested if its
    momentum beats cash (SHY); else that slot -> cash. This is the crash defense —
    when everything is falling, slots rotate to cash automatically.
  - Bonds/gold are IN the universe, so in a stock crash they rise in the ranking
    and you rotate into them. Defense is built into the ranking, not bolted on.

Reports top-3 / top-5, with vs without the absolute filter, vs SPY, per-year.
"""
import pandas as pd, numpy as np
from pathlib import Path
DATA=Path(__file__).parent/"data_broad"

def lm(t):
    s=pd.read_parquet(DATA/f"{t}.parquet")["Close"]; s.index=pd.to_datetime(s.index)
    return s.resample("ME").last()

ALL=sorted(p.stem for p in DATA.glob("*.parquet"))
CASH="SHY"
UNI=[t for t in ALL if t!=CASH]
px=pd.DataFrame({t:lm(t) for t in ALL})
ret=px.pct_change()

def momentum(i):
    """blended 3/6/12-mo momentum at month index i for each ticker (NaN if short)."""
    out={}
    for t in UNI+[CASH]:
        s=px[t]
        if i<12 or pd.isna(s.iloc[i]): continue
        vals=[]
        for lb in (3,6,12):
            if not pd.isna(s.iloc[i-lb]) and s.iloc[i-lb]>0:
                vals.append(s.iloc[i]/s.iloc[i-lb]-1)
        if vals: out[t]=np.mean(vals)
    return out

def run(n, absolute=True):
    idx=px.index; eq=[1.0]; dts=[idx[12]]
    for i in range(12,len(idx)-1):
        m=momentum(i)
        ranked=sorted([t for t in UNI if t in m], key=lambda t:m[t], reverse=True)
        held=ranked[:n]
        cash_m=m.get(CASH,0.0)
        c=0.0
        for slot in range(n):
            if slot<len(held):
                t=held[slot]
                use_cash = absolute and (m[t] <= cash_m)
                tk = CASH if use_cash else t
            else:
                tk = CASH
            r=ret[tk].iloc[i+1]
            c += (1.0/n)*(0 if pd.isna(r) else r)
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def bah(t="SPY"):
    s=px[t].dropna().loc[px.index[12]:]; return s/s.iloc[0]

def stats(eq,label):
    eq=eq/eq.iloc[0]; yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    vol=eq.pct_change().dropna().std()*np.sqrt(12); sharpe=(cagr-0.02)/vol if vol>0 else 0
    print(f"  {label:<34} CAGR {cagr*100:+5.1f}%  maxDD {dd*100:6.1f}%  Sharpe {sharpe:.2f}")
    return eq

print("="*86)
print(f"DUAL MOMENTUM ROTATION — {len(UNI)} ETFs, {px.index[12].date()} to {px.index[-1].date()}")
print("="*86)
res={}
for n in (3,5):
    res[(n,True)]=stats(run(n,True),f"Top-{n} + absolute filter (dual mom)")
for n in (3,5):
    stats(run(n,False),f"Top-{n} relative-only (NO crash filter)")
spy=stats(bah("SPY"),"SPY buy & hold")

print("\n--- Per-year: Top-5 dual momentum vs SPY (crash years starred) ---")
r5=res[(5,True)]; r5=r5/r5.iloc[0]; b=bah("SPY"); b=b/b.iloc[0]
print(f"  {'Year':<6}{'Top5-dual':<12}{'SPY':<10}")
for yr in range(px.index[12].year+1, px.index[-1].year+1):
    def yr_ret(s):
        sy=s[s.index.year==yr]
        if len(sy)<2: return np.nan
        prev=s[s.index<sy.index[0]]; base=prev.iloc[-1] if len(prev) else sy.iloc[0]
        return (sy.iloc[-1]/base-1)*100
    fr,br=yr_ret(r5),yr_ret(b)
    fmt=lambda x:f"{x:+.1f}%" if not np.isnan(x) else "  -"
    star=" <--" if yr in (2000,2001,2002,2008,2020,2022) else ""
    print(f"  {yr:<6}{fmt(fr):<12}{fmt(br):<10}{star}")
