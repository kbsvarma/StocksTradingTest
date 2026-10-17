"""Stress the dual-momentum Top-5 result: is it luck or real? + vol targeting.

Four "is it overfit/luck" tests:
  A. Split-half — works independently in 2000-2013 AND 2013-2026?
  B. N-robustness — top 3/4/5/6/7/8 all work, or only 5?
  C. Lookback-robustness — 3 / 6 / 12 / blends all work, or only the chosen blend?
  D. RANDOM benchmark (the key test) — pick 5 at random each month (same cash
     filter), 300 seeds. If momentum-top-5 isn't near the top of the random
     distribution, the "signal" is just luck.

Then: volatility targeting overlay to smooth the -29% drawdown.
"""
import pandas as pd, numpy as np
from pathlib import Path
DATA=Path(__file__).parent/"data_broad"
def lm(t):
    s=pd.read_parquet(DATA/f"{t}.parquet")["Close"]; s.index=pd.to_datetime(s.index)
    return s.resample("ME").last()
ALL=sorted(p.stem for p in DATA.glob("*.parquet")); CASH="SHY"
UNI=[t for t in ALL if t!=CASH]
px=pd.DataFrame({t:lm(t) for t in ALL}); ret=px.pct_change()

def mom_at(i, lbs):
    out={}
    for t in UNI+[CASH]:
        s=px[t]
        if i<max(lbs) or pd.isna(s.iloc[i]): continue
        vals=[s.iloc[i]/s.iloc[i-lb]-1 for lb in lbs if not pd.isna(s.iloc[i-lb]) and s.iloc[i-lb]>0]
        if vals: out[t]=np.mean(vals)
    return out

def run(n, lbs=(3,6,12), absolute=True, rng=None, lo=None, hi=None):
    idx=px.index; lo=lo or 12; hi=hi or len(idx)-1
    eq=[1.0]; dts=[idx[lo]]; rets=[]
    for i in range(lo,hi):
        m=mom_at(i,lbs)
        cands=[t for t in UNI if t in m]
        if rng is not None:
            picks=list(rng.choice(cands, size=min(n,len(cands)), replace=False)) if cands else []
        else:
            picks=sorted(cands,key=lambda t:m[t],reverse=True)[:n]
        cash_m=m.get(CASH,0.0); c=0.0
        for slot in range(n):
            if slot<len(picks):
                t=picks[slot]; tk=CASH if (absolute and m[t]<=cash_m) else t
            else: tk=CASH
            r=ret[tk].iloc[i+1]; c+=(1.0/n)*(0 if pd.isna(r) else r)
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1]); rets.append(c)
    return pd.Series(eq[1:],index=dts[1:]), pd.Series(rets,index=dts[1:])

def metr(eq):
    eq=eq/eq.iloc[0]; yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    vol=eq.pct_change().dropna().std()*np.sqrt(12); sh=(cagr-0.02)/vol if vol>0 else 0
    return cagr,dd,sh

print("="*78); print("A. SPLIT-HALF (does Top-5 dual work in BOTH halves independently?)"); print("="*78)
mid=12+(len(px)-13)//2
for lo,hi,lbl in [(12,mid,"1st half ~2000-2013"),(mid,len(px)-1,"2nd half ~2013-2026")]:
    eq,_=run(5,lo=lo,hi=hi); c,d,s=metr(eq)
    print(f"  {lbl:<24} CAGR {c*100:+5.1f}%  maxDD {d*100:6.1f}%  Sharpe {s:.2f}")

print("\n"+"="*78); print("B. N-ROBUSTNESS (top-N dual momentum)"); print("="*78)
for n in (3,4,5,6,7,8):
    eq,_=run(n); c,d,s=metr(eq)
    print(f"  Top-{n:<2}  CAGR {c*100:+5.1f}%  maxDD {d*100:6.1f}%  Sharpe {s:.2f}")

print("\n"+"="*78); print("C. LOOKBACK-ROBUSTNESS (Top-5, different momentum windows)"); print("="*78)
for lbs in [(3,),(6,),(12,),(3,6,12),(1,3,6,12)]:
    eq,_=run(5,lbs=lbs); c,d,s=metr(eq)
    print(f"  lookbacks {str(lbs):<14} CAGR {c*100:+5.1f}%  maxDD {d*100:6.1f}%  Sharpe {s:.2f}")

print("\n"+"="*78); print("D. RANDOM BENCHMARK — momentum vs picking 5 at random (300 seeds)"); print("="*78)
mom_eq,_=run(5); mc,md,ms=metr(mom_eq)
rand_cagrs=[]; rand_sharpes=[]
for seed in range(300):
    rng=np.random.default_rng(seed)
    eq,_=run(5,rng=rng); c,d,s=metr(eq)
    rand_cagrs.append(c); rand_sharpes.append(s)
rc=np.array(rand_cagrs); rs=np.array(rand_sharpes)
pct_cagr=(rc<mc).mean()*100; pct_sharpe=(rs<ms).mean()*100
print(f"  Momentum Top-5:    CAGR {mc*100:+.1f}%   Sharpe {ms:.2f}")
print(f"  Random 5 (mean):   CAGR {rc.mean()*100:+.1f}%   Sharpe {rs.mean():.2f}")
print(f"  Random 5 (95th pct): CAGR {np.percentile(rc,95)*100:+.1f}%   Sharpe {np.percentile(rs,95):.2f}")
print(f"  -> Momentum beats {pct_cagr:.0f}% of random on CAGR, {pct_sharpe:.0f}% on Sharpe")
print(f"  -> {'REAL SIGNAL' if pct_sharpe>90 else 'WEAK/LUCK' if pct_sharpe<70 else 'MODERATE'}: "
      f"momentum {'clearly beats' if pct_sharpe>90 else 'barely beats' if pct_sharpe<70 else 'beats'} random selection")

print("\n"+"="*78); print("VOLATILITY TARGETING — scale Top-5 exposure to 10% target vol (cap 1.5x)"); print("="*78)
_,r5=run(5)
realized=r5.rolling(6).std()*np.sqrt(12)
target=0.10
scale=(target/realized).clip(upper=1.5).shift(1).fillna(1.0)
vt_ret=r5*scale + (1-scale)*ret[CASH].reindex(r5.index).fillna(0)
vt_eq=(1+vt_ret).cumprod()
for label,eq in [("Top-5 dual (raw)",mom_eq),("Top-5 + vol-target 10%",vt_eq)]:
    c,d,s=metr(eq); print(f"  {label:<26} CAGR {c*100:+5.1f}%  maxDD {d*100:6.1f}%  Sharpe {s:.2f}")
