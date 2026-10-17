#!/usr/bin/env python3
"""Pass 1: exhaustive trend-signal sweep for Kalshi crypto perps.
Daily closes, next-day execution, costs 15bp/side turnover, funding drag
from measured Kalshi funding (BTC long pays 1.1bp/day; ETH long receives
0.7bp/day — shorts mirror). IS <=2021-12-31, OOS 2022+."""
import json, math, statistics

COST_SIDE=15.0/1e4
FUND_DAILY={"btc": 1.11/1e4, "eth": -0.69/1e4}   # long pays positive

def load(a):
    rows=json.load(open(f"data/{a}_daily.json"))
    return [r[0] for r in rows],[r[1] for r in rows]

def metrics(dates, px, pos, asset):
    fund=FUND_DAILY[asset]
    rets=[]; turn=0
    for i in range(1,len(px)):
        r=(px[i]-px[i-1])/px[i-1]
        p=pos[i-1]
        c=abs(pos[i]-pos[i-1])*COST_SIDE
        turn+=abs(pos[i]-pos[i-1])
        rets.append(p*r - c - p*fund)
    def agg(rs):
        if not rs: return (0,0,0)
        eq=1.0; pk=1.0; dd=0
        for r in rs:
            eq*=(1+r); pk=max(pk,eq); dd=min(dd,eq/pk-1)
        yrs=len(rs)/365
        cagr=eq**(1/yrs)-1 if yrs>0 and eq>0 else -1
        mu=statistics.mean(rs); sd=statistics.pstdev(rs)
        sharpe=mu/sd*math.sqrt(365) if sd else 0
        return (cagr, sharpe, dd)
    split=next(i for i,d in enumerate(dates) if d>="2022-01-01")
    full=agg(rets); IS=agg(rets[:split-1]); OOS=agg(rets[split-1:])
    return {"cagr":full[0],"sharpe":full[1],"dd":full[2],
            "is_sh":IS[1],"oos_sh":OOS[1],"oos_cagr":OOS[0],"oos_dd":OOS[2],
            "trades_yr":turn/2/(len(px)/365)}

def donchian(px,n,mode):
    pos=[0]*len(px)
    for i in range(len(px)):
        if i<n: continue
        hi=max(px[i-n:i]); lo=min(px[i-n:i])
        hi2=max(px[i-n//2:i]); lo2=min(px[i-n//2:i])
        p=pos[i-1]
        if mode in("ls","lo") and px[i]>=hi: p=1
        if mode in("ls","so") and px[i]<=lo: p=-1
        if p==1 and px[i]<=lo2: p=0
        if p==-1 and px[i]>=hi2: p=0
        if mode=="lo" and p<0: p=0
        if mode=="so" and p>0: p=0
        pos[i]=p
    return pos

def macross(px,f,s,mode):
    pos=[0]*len(px)
    def ma(i,n): return sum(px[i-n+1:i+1])/n
    for i in range(len(px)):
        if i<s: continue
        p=1 if ma(i,f)>ma(i,s) else -1
        if mode=="lo": p=max(p,0)
        pos[i]=p
    return pos

def pvma(px,n,mode):
    pos=[0]*len(px)
    for i in range(len(px)):
        if i<n: continue
        p=1 if px[i]>sum(px[i-n+1:i+1])/n else -1
        if mode=="lo": p=max(p,0)
        pos[i]=p
    return pos

def tsmom(px,ks,mode,rule):
    pos=[0]*len(px)
    for i in range(len(px)):
        if i<max(ks): continue
        votes=[1 if px[i]>px[i-k] else -1 for k in ks]
        s=sum(votes)
        if rule=="maj": p=1 if s>0 else (-1 if s<0 else 0)
        else: p=1 if s==len(ks) else (-1 if s==-len(ks) else 0)
        if mode=="lo": p=max(p,0)
        pos[i]=p
    return pos

results=[]
for asset in ("btc","eth"):
    dates,px=load(asset)
    for n in (10,20,40,55,90,120,180,252):
        for mode in ("ls","lo"):
            results.append((f"donchian{n}_{mode}",asset,metrics(dates,px,donchian(px,n,mode),asset)))
    for f,s in ((10,50),(20,100),(50,200),(20,60),(50,120),(100,300)):
        for mode in ("ls","lo"):
            results.append((f"macross{f}/{s}_{mode}",asset,metrics(dates,px,macross(px,f,s,mode),asset)))
    for n in (50,100,200):
        for mode in ("ls","lo"):
            results.append((f"pvma{n}_{mode}",asset,metrics(dates,px,pvma(px,n,mode),asset)))
    for ks,rule,tag in (((60,120,252),"maj","codexvote"),((60,120,252),"unan","codexunan"),
                        ((30,90,180),"maj","vote30_90_180"),((30,60,90),"maj","vote30_60_90")):
        for mode in ("ls","lo"):
            results.append((f"tsmom_{tag}_{mode}",asset,metrics(dates,px,tsmom(px,ks,mode,rule),asset)))
    # buy & hold reference
    results.append((f"BUYHOLD",asset,metrics(dates,px,[1]*len(px),asset)))

with open("data/sweep_results.json","w") as f:
    json.dump([(n,a,m) for n,a,m in results],f)
print(f"{'strategy':>24} {'asset':>4} {'CAGR':>7} {'Sharpe':>6} {'maxDD':>7} {'IS_sh':>6} {'OOS_sh':>6} {'OOS_cagr':>8} {'tr/yr':>5}")
for n,a,m in sorted(results,key=lambda r:-(min(r[2]["is_sh"],r[2]["oos_sh"]))):
    print(f"{n:>24} {a:>4} {m['cagr']:>6.1%} {m['sharpe']:>6.2f} {m['dd']:>7.1%} "
          f"{m['is_sh']:>6.2f} {m['oos_sh']:>6.2f} {m['oos_cagr']:>8.1%} {m['trades_yr']:>5.1f}")
