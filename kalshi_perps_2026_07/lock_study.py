#!/usr/bin/env python3
"""Settlement-lock study — REPRODUCIBLE version (addresses audit gap).
Exact logic that produced the Q4 z>=1.96 result on 2026-07-27:
final-minute (rem=1 candle close) entries on 15M markets, ask<=0.994,
fav mid>=0.90, spread<=1.5c; perp z = strike-margin / (1-min robust sigma);
quartile analysis + threshold extraction. Run: python3 lock_study.py"""
import json, os, sys, math, statistics
R="/Users/varmakammili/Documents/GitHub/StocksTradingTest/kalshi_crypto15m_2026_07"
sys.path.insert(0,R); os.chdir(R)
import backtest as bt
import portfolio as pf
P=os.path.dirname(os.path.abspath(__file__))
PERP={"KXBTC15M":"KXBTCPERP","KXETH15M":"KXETHPERP","KXSOL15M":"KXSOLPERP","KXXRP15M":"KXXRPPERP"}
def load_perp(t):
    mids={}
    for line in open(os.path.join(P,"data",f"perp_candles_{t}.jsonl")):
        rec=json.loads(line)
        for c in rec["c"]:
            b,a=c.get("b"),c.get("a")
            if b and a: mids[c["t"]]=(float(b)+float(a))/2
    return mids
def near(mids,ts,tol=180):
    for d in range(0,tol+60,60):
        if ts-d in mids: return mids[ts-d]
    return None
strikes={}
for s in PERP:
    for line in open(f"data/markets_{s}.jsonl"):
        m=json.loads(line)
        if m.get("floor_strike"): strikes[m["ticker"]]=float(m["floor_strike"])
rows=[]
for s,ptkr in PERP.items():
    mids=load_perp(ptkr); tmin,tmax=min(mids),max(mids)
    for m in bt.load(s):
        if not (tmin<m["close_ts"]-900 and m["close_ts"]<tmax): continue
        strike=strikes.get(m["ticker"])
        if not strike: continue
        c1=m["by_rem"].get(1)
        if not c1: continue
        q=bt.fav_quotes(c1)
        if not q: continue
        side,fbid,fask,fmid=q
        if fask>0.994 or fask-fbid>0.015 or fmid<0.90: continue
        t1=m["close_ts"]-60
        pm=near(mids,t1); pm3=near(mids,t1-180)
        if pm is None: continue
        k=10**round(math.log10(strike/pm)); idx=pm*k
        sgn=1 if side=="yes" else -1
        margin=sgn*(idx-strike)/strike
        vals=[near(mids,t) for t in range(t1-900,t1+60,60)]
        vals=[v for v in vals if v]
        rets=[abs(b-a)/a for a,b in zip(vals,vals[1:])]
        if len(rets)<5: continue
        sig1=statistics.median(rets)*1.4826
        if sig1<=0: continue
        win=(m["result"]==side)
        pnl=(1-fask)-bt.fee(fask) if win else -fask-bt.fee(fask)
        rows.append({"z":margin/sig1,"pnl":pnl,"win":win,"entry":fask,
                     "oos":m["close_ts"]>=pf.OOS_TS})
zs=sorted(r["z"] for r in rows)
q4=zs[3*len(zs)//4]
print(f"n={len(rows)}  Q4 threshold={q4:.3f}")
top=[r for r in rows if r["z"]>=q4]
n=len(top); w=sum(r["win"] for r in top)
ev=sum(r["pnl"] for r in top)/n*100
sd=(sum((r["pnl"]*100-ev)**2 for r in top)/(n-1))**0.5
ist=[r["pnl"] for r in top if not r["oos"]]; oot=[r["pnl"] for r in top if r["oos"]]
print(f"Q4: n={n} WR={w/n:.4f} EV={ev:+.3f}c t={ev/(sd/math.sqrt(n)):+.2f} "
      f"IS={sum(ist)/len(ist)*100:+.3f} OOS={sum(oot)/len(oot)*100:+.3f}")
