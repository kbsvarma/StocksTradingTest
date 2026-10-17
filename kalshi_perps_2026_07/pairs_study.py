#!/usr/bin/env python3
"""Perp pairs mean-reversion study (second pass).

All active perp pairs: hourly log-spread of normalized mids; AR(1) half-life,
oscillation amplitude, naive band-trade harvest vs 20bps pair round-trip
(2 legs x 10bps maker RT). IS/OOS split 2026-07-07.
"""
import json, math, statistics, itertools
from collections import defaultdict

ASSETS=["KXBTCPERP","KXETHPERP","KXSOLPERP","KXXRPPERP","KXZECPERP","KXNEARPERP",
        "KXSUIPERP","KXHYPEPERP","KXLTCPERP","KXBCHPERP","KXLINKPERP",
        "KXDOGEPERP","KXKSHIBPERP"]
COST=20.0
OOS=1783468800

def load_hourly(t):
    mids={}
    try:
        for line in open(f"data/perp_candles_{t}.jsonl"):
            rec=json.loads(line)
            for c in rec["c"]:
                b,a=c.get("b"),c.get("a")
                if b and a and c["t"]%3600==0:
                    mids[c["t"]]=(float(b)+float(a))/2
    except FileNotFoundError:
        return {}
    return mids

def study(a, b, ma, mb):
    ts=sorted(set(ma)&set(mb))
    if len(ts)<400: return None
    la={t:math.log(ma[t]) for t in ts}; lb={t:math.log(mb[t]) for t in ts}
    # normalize spread vs 7d rolling mean (simple, causal)
    spread={}
    win=168
    for i,t in enumerate(ts):
        if i<win: continue
        base=statistics.mean(la[ts[j]]-lb[ts[j]] for j in range(i-win,i))
        spread[t]=(la[t]-lb[t]-base)*1e4    # bps
    sts=sorted(spread)
    if len(sts)<300: return None
    vals=[spread[t] for t in sts]
    # AR(1) half-life
    x=vals[:-1]; y=vals[1:]
    mx=statistics.mean(x); my=statistics.mean(y)
    cov=sum((a1-mx)*(b1-my) for a1,b1 in zip(x,y)); var=sum((a1-mx)**2 for a1 in x)
    rho=cov/var if var else 1.0
    hl=math.log(0.5)/math.log(abs(rho)) if 0<abs(rho)<1 else float('inf')
    sd=statistics.stdev(vals)
    # naive band trade: enter when |s|>2sd, exit at 0-cross or 72h; costs 20bps
    trades=[]
    i=0
    while i<len(sts):
        s=spread[sts[i]]
        if abs(s)>2*sd:
            side=-1 if s>0 else 1
            j=i+1
            while j<len(sts) and j<i+72 and spread[sts[j]]*side<0-(-abs(s)) and spread[sts[j]]*s>0:
                j+=1
            j=min(j,len(sts)-1)
            pnl=side*(spread[sts[j]]-s)-COST
            trades.append({"ts":sts[i],"pnl":pnl})
            i=j+1
        else:
            i+=1
    if len(trades)<8: return None
    net=[t["pnl"] for t in trades]
    IS=[t["pnl"] for t in trades if t["ts"]<OOS]; OO=[t["pnl"] for t in trades if t["ts"]>=OOS]
    mu=statistics.mean(net); sdn=statistics.stdev(net) if len(net)>1 else 1
    return {"pair":f"{a[2:-4]}/{b[2:-4]}","n":len(trades),"sd_spread":sd,"hl_h":hl,
            "net":mu,"t":mu/(sdn/math.sqrt(len(net))),
            "is":statistics.mean(IS) if IS else float('nan'),
            "oos":statistics.mean(OO) if OO else float('nan')}

if __name__=="__main__":
    mids={a:load_hourly(a) for a in ASSETS}
    mids={a:m for a,m in mids.items() if m}
    print(f"assets with data: {list(mids)}")
    rows=[]
    for a,b in itertools.combinations(mids,2):
        r=study(a,b,mids[a],mids[b])
        if r: rows.append(r)
    rows.sort(key=lambda r:-r["t"])
    print(f"{'pair':>14} {'n':>3} {'sd':>6} {'hl_h':>6} {'net_bps':>8} {'t':>6} {'IS':>7} {'OOS':>7}")
    for r in rows[:20]:
        print(f"{r['pair']:>14} {r['n']:>3} {r['sd_spread']:>6.0f} {min(r['hl_h'],999):>6.0f} "
              f"{r['net']:>+8.1f} {r['t']:>+6.2f} {r['is']:>+7.1f} {r['oos']:>+7.1f}")
    pos=[r for r in rows if r["net"]>0 and r["is"]>0 and r["oos"]>0 and r["t"]>=2]
    print(f"\npairs passing (net>0, IS&OOS>0, t>=2): {len(pos)}")
