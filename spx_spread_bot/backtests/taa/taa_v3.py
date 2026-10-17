"""TAA v3 — (a) deployment spec, (b) momentum-rank overlay, (c) leverage sleeve.

(b) Top-N overlay: among the 5 assets above their SMA, hold only the top-N by
    momentum (Faber's later 'GTAA aggressive' idea) to claw back bull-market lag.
(c) Leverage: apply 1.0/1.5/2.0x to the low-drawdown equal-weight Faber, with a
    realistic borrow cost (cash rate), to see if it recovers SPY-like return.
(a) Current state: each ETF's price vs 10-mo SMA + momentum -> what to hold NOW
    on $25k, using LIVE prices.
"""
import pandas as pd, numpy as np
from pathlib import Path
DATA=Path(__file__).parent/"data"
SLEEVES=["SPY","EFA","IEF","DBC","VNQ"]

def lm(t):
    s=pd.read_parquet(DATA/f"{t}.parquet")["Close"]; s.index=pd.to_datetime(s.index)
    return s.resample("ME").last()

px=pd.DataFrame({t:lm(t) for t in SLEEVES+["SHY"]}).loc["2006-06-30":]
ret=px.pct_change(); sma=px.rolling(10).mean(); mom=px/px.shift(6)-1  # 6-mo momentum

def run_topn(n, sma_len=10):
    s=px.rolling(sma_len).mean(); idx=px.index; eq=[1.0]; dts=[idx[12]]
    for i in range(12,len(idx)-1):
        on=[a for a in SLEEVES if not pd.isna(s[a].iloc[i]) and px[a].iloc[i]>s[a].iloc[i]]
        if on:
            ranked=sorted(on,key=lambda a:mom[a].iloc[i] if not pd.isna(mom[a].iloc[i]) else -9,reverse=True)
            held=ranked[:n]; w=1.0/n
            c=sum(w*(0 if pd.isna(ret[a].iloc[i+1]) else ret[a].iloc[i+1]) for a in held)
            # unfilled sleeves -> cash
            c+=((n-len(held))/n)*(0 if pd.isna(ret["SHY"].iloc[i+1]) else ret["SHY"].iloc[i+1])
        else:
            c=0 if pd.isna(ret["SHY"].iloc[i+1]) else ret["SHY"].iloc[i+1]
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def run_faber_ew(sma_len=10):
    s=px.rolling(sma_len).mean(); idx=px.index; eq=[1.0]; dts=[idx[12]]
    for i in range(12,len(idx)-1):
        c=0.0
        for a in SLEEVES:
            on=px[a].iloc[i]>s[a].iloc[i] if not pd.isna(s[a].iloc[i]) else False
            r=ret[a].iloc[i+1] if on else ret["SHY"].iloc[i+1]
            c+=0.2*(0 if pd.isna(r) else r)
        eq.append(eq[-1]*(1+c)); dts.append(idx[i+1])
    return pd.Series(eq[1:],index=dts[1:])

def levered(base_eq, L):
    """Apply leverage L with borrow cost = cash (SHY) monthly rate on (L-1)."""
    r=base_eq.pct_change().dropna()
    cash=ret["SHY"].reindex(r.index).fillna(0)
    lr=L*r-(L-1)*cash
    return (1+lr).cumprod()

def bah(t="SPY"):
    s=px[t].dropna().loc[px.index[12]:]; return s/s.iloc[0]

def stats(eq,label):
    eq=eq/eq.iloc[0]; yrs=(eq.index[-1]-eq.index[0]).days/365.25
    cagr=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=((eq-eq.cummax())/eq.cummax()).min()
    vol=eq.pct_change().dropna().std()*np.sqrt(12); sharpe=(cagr-0.02)/vol if vol>0 else 0
    print(f"  {label:<30} CAGR {cagr*100:+5.1f}%  maxDD {dd*100:6.1f}%  Sharpe {sharpe:.2f}")
    return eq

print("="*84)
print("(b) MOMENTUM-RANK OVERLAY — hold top-N of 5 assets above SMA, by 6mo momentum")
print("="*84)
ew=run_faber_ew(10)
stats(run_topn(1),"Top-1 (most aggressive)")
stats(run_topn(2),"Top-2")
stats(run_topn(3),"Top-3")
stats(ew,"Equal-weight all (base Faber)")
stats(bah("SPY"),"SPY buy & hold")

print("\n"+"="*84)
print("(c) LEVERAGE SLEEVE — on Top-2 (best risk-adj), borrow cost = cash rate")
print("="*84)
t2=run_topn(2)
for L in (1.0,1.5,2.0):
    stats(levered(t2,L),f"Top-2 @ {L:.1f}x leverage")
stats(bah("SPY"),"SPY buy & hold (ref)")

# ---------------- (a) LIVE DEPLOYMENT SPEC ----------------
print("\n"+"="*84)
print("(a) CURRENT $25k DEPLOYMENT — what to hold NOW (live prices)")
print("="*84)
import yfinance as yf
ACCT=25000
live={}
for t in SLEEVES:
    try:
        fi=yf.Ticker(t).fast_info; live[t]=float(fi.get("lastPrice") or fi.get("last_price"))
    except Exception:
        live[t]=float(px[t].iloc[-1])
# current 10-mo SMA & 6-mo momentum from monthly series (through latest)
cur_sma={a:px[a].rolling(10).mean().iloc[-1] for a in SLEEVES}
cur_mom={a:(px[a].iloc[-1]/px[a].iloc[-7]-1) for a in SLEEVES}
print(f"{'ETF':<6}{'LivePx':<10}{'10mo SMA':<11}{'State':<8}{'6mo mom':<10}")
print("-"*48)
on_assets=[]
for a in SLEEVES:
    state="ABOVE (ON)" if live[a]>cur_sma[a] else "below(off)"
    if live[a]>cur_sma[a]: on_assets.append(a)
    print(f"{a:<6}${live[a]:<8.2f} ${cur_sma[a]:<9.2f} {state:<8} {cur_mom[a]*100:<+8.1f}%")

print(f"\nAssets currently ON (price > 10mo SMA): {on_assets if on_assets else 'NONE -> all cash'}")
# Base (equal-weight) allocation
print(f"\n--- BASE plan (equal-weight all 5 sleeves; off-sleeves -> cash/SHY/BIL) ---")
per=ACCT/5
for a in SLEEVES:
    if a in on_assets:
        sh=int(per//live[a]); print(f"  {a}: ${per:,.0f} -> {sh} shares (${sh*live[a]:,.0f})")
    else:
        print(f"  {a}: ${per:,.0f} -> HOLD CASH (below SMA)")
# Top-2 aggressive allocation
ranked=sorted(on_assets,key=lambda a:cur_mom[a],reverse=True)[:2]
print(f"\n--- TOP-2 plan (higher return: split 50/50 into 2 strongest ON assets) ---")
if ranked:
    half=ACCT/2
    for a in ranked:
        sh=int(half//live[a]); print(f"  {a}: ${half:,.0f} -> {sh} shares (${sh*live[a]:,.0f})  [6mo mom {cur_mom[a]*100:+.1f}%]")
    if len(ranked)<2: print(f"  remainder -> CASH")
else:
    print("  all assets below SMA -> 100% CASH")
print("\nRule: re-check on the last trading day of each month; rebalance only on changes.")
