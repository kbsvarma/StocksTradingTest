"""Long-history robustness — is Faber-style SMA timing a post-2007 artifact?

The real out-of-sample test: does SMA timing also protect in crashes NOT in
our 2007+ sample — specifically the 2000-2002 dotcom crash?

Uses long-history Vanguard funds (total-return adjusted):
  - VFINX (S&P500, 1980), VTRIX (intl value, 1983), VUSTX (LT treasury, 1986),
    VGSIX (REIT, 1996).
4-asset Faber from 1997 (when all 4 exist) — covers dotcom 2000-02, 2008, 2020, 2022.
Also a 2-asset (stocks+treasury) version from 1987 to reach the dotcom + earlier.
"""
import pandas as pd, numpy as np
from pathlib import Path
DL=Path(__file__).parent/"data_long"

def lm(t):
    s=pd.read_parquet(DL/f"{t}.parquet")["Close"]; s.index=pd.to_datetime(s.index)
    return s.resample("ME").last()

def faber(px, sleeves, cash_col, sma_len=10):
    ret=px.pct_change(); sma=px.rolling(sma_len).mean(); idx=px.index
    eq=[1.0]; dts=[idx[sma_len]]; w=1.0/len(sleeves)
    for i in range(sma_len,len(idx)-1):
        c=0.0
        for a in sleeves:
            if pd.isna(px[a].iloc[i]) or pd.isna(sma[a].iloc[i]):
                r=0
            else:
                on=px[a].iloc[i]>sma[a].iloc[i]
                r=ret[a].iloc[i+1] if on else (ret[cash_col].iloc[i+1] if cash_col else 0)
            c+=w*(0 if pd.isna(r) else r)
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def bah(px,t):
    s=px[t].dropna(); return s/s.iloc[0]

def stats(eq,label,years_focus=()):
    eq=eq/eq.iloc[0]; yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    vol=eq.pct_change().dropna().std()*np.sqrt(12); sharpe=(cagr-0.02)/vol if vol>0 else 0
    extra=""
    for y0,y1,nm in years_focus:
        seg=eq[(eq.index.year>=y0)&(eq.index.year<=y1)]
        if len(seg)>1:
            sdd=((seg-seg.cummax())/seg.cummax()).min()*100
            extra+=f"  {nm}DD:{sdd:.0f}%"
    print(f"  {label:<26} CAGR {cagr*100:+5.1f}%  maxDD {dd*100:6.1f}%  Sharpe {sharpe:.2f}{extra}")
    return eq

# 4-asset from 1997
px4=pd.DataFrame({t:lm(t) for t in ["VFINX","VTRIX","VUSTX","VGSIX"]}).dropna().loc["1996-12-31":]
# add a cash proxy: use VUSTX? no—use 0 (conservative) for 'cash' sleeve when out
print("="*88)
print(f"4-ASSET Faber (stocks/intl/LTtreasury/REIT), {px4.index[0].date()} to {px4.index[-1].date()}")
print("  (cash sleeve = 0% when an asset is below its SMA — conservative)")
print("="*88)
foc=[(2000,2002,"dotcom"),(2008,2009,"GFC"),(2020,2020,"covid"),(2022,2022,"'22")]
for L in (6,10,12):
    stats(faber(px4,["VFINX","VTRIX","VUSTX","VGSIX"],None,L),f"Faber-{L} (4-asset)",foc)
stats(bah(px4,"VFINX"),"VFINX buy & hold",foc)

# 2-asset stocks+LTtreasury from 1987 (reaches dotcom + 1990, 2000)
px2=pd.DataFrame({t:lm(t) for t in ["VFINX","VUSTX"]}).dropna().loc["1986-12-31":]
print(f"\n2-ASSET Faber (S&P500 + LT Treasury), {px2.index[0].date()} to {px2.index[-1].date()}")
print("  (each 50%; below-SMA sleeve -> the OTHER asset's treasury, i.e. defensive tilt)")
print("-"*88)
foc2=[(2000,2002,"dotcom"),(2008,2009,"GFC")]
for L in (6,10,12):
    stats(faber(px2,["VFINX","VUSTX"],"VUSTX",L),f"Faber-{L} (2-asset)",foc2)
stats(bah(px2,"VFINX"),"VFINX buy & hold",foc2)

print("\n--- Per-year, 4-asset Faber-10 vs VFINX B&H (dotcom is the OOS-in-time test) ---")
f10=faber(px4,["VFINX","VTRIX","VUSTX","VGSIX"],None,10); f10=f10/f10.iloc[0]
b=bah(px4,"VFINX"); b=b/b.iloc[0]
print(f"  {'Year':<6}{'Faber-10':<12}{'VFINX B&H':<12}")
for yr in range(px4.index[0].year+1, px4.index[-1].year+1):
    def yr_ret(s):
        sy=s[s.index.year==yr]
        if len(sy)<2: return np.nan
        prev=s[s.index<sy.index[0]]; base=prev.iloc[-1] if len(prev) else sy.iloc[0]
        return (sy.iloc[-1]/base-1)*100
    fr,br=yr_ret(f10),yr_ret(b)
    fmt=lambda x:f"{x:+.1f}%" if not np.isnan(x) else "  -"
    star=" <-- dotcom" if yr in (2000,2001,2002) else (" <-- GFC" if yr in (2008,) else "")
    print(f"  {yr:<6}{fmt(fr):<12}{fmt(br):<12}{star}")
