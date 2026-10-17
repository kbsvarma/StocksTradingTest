#!/usr/bin/env python3
"""Full calibration surface per domain — no inherited parameters.
Minute series: rem-groups {1-3,4-8,9-15,16-30} min.
Hourly series: hour-groups {1,2,3,4,6,8,12,18,24} h.
Bands: 50-70,70-80,80-90,90-95,95-99 (favorite mid).
Cells: n, WR, implied, excess, tradable EV (ask+fee), IS/OOS, per-domain BH-FDR."""
import json, math, calendar, time, sys
from collections import defaultdict
OOS=calendar.timegm(time.strptime("2026-07-07T00:00:00Z","%Y-%m-%dT%H:%M:%SZ"))
def ts(s): return calendar.timegm(time.strptime(s,"%Y-%m-%dT%H:%M:%SZ"))
def fee(p): return 0.07*p*(1-p)
def quotes(c):
    b=c.get("bc") or c.get("b"); a=c.get("ac") or c.get("a")
    if not b or not a: return None
    b,a=float(b),float(a)
    if a<=0 or a>1 or b<0 or b>a: return None
    mid=(b+a)/2
    return ("yes",b,a,mid) if mid>0.5 else ("no",1-a,1-b,1-mid)
def band(fm):
    for lo,hi,l in ((0.95,0.99,"95-99"),(0.90,0.95,"90-95"),(0.80,0.90,"80-90"),
                    (0.70,0.80,"70-80"),(0.50,0.70,"50-70")):
        if lo<=fm<hi: return l
    return None
def bh(ps,q=0.10):
    idx=sorted(range(len(ps)),key=lambda i:ps[i]); m=len(ps); kmax=-1
    for r,i in enumerate(idx,1):
        if ps[i]<=q*r/m: kmax=r
    return {i for r,i in enumerate(idx,1) if r<=kmax}

DOMAINS={
 "SPX_hourly":  (["KXINXU"], "min"),
 "NDX_hourly":  (["KXNASDAQ100U"], "min"),
 "gas_daily":   (["KXAAAGASD"], "hr"),
 "wti_daily":   (["KXWTI"], "hr"),
 "brent_daily": (["KXBRENTD"], "hr"),
 "gold_daily":  (["KXGOLDD"], "hr"),
 "usdjpy_daily":(["KXUSDJPY"], "hr"),
 "spotify":     (["KXSPOTIFYD"], "hr"),
 "spx_ladder":  (["KXINX"], "hr"),
 "ndx_ladder":  (["KXNASDAQ100"], "hr"),
 "idx_updown":  (["KXINXDUD","KXNASDAQDUD"], "hr"),
}
MINGRP=[(1,3,"1-3m"),(4,8,"4-8m"),(9,15,"9-15m"),(16,30,"16-30m")]
HRGRP=[(1,1,"1h"),(2,2,"2h"),(3,3,"3h"),(4,5,"4-5h"),(6,7,"6-7h"),(8,11,"8-11h"),
       (12,17,"12-17h"),(18,30,"18-30h")]

for dom,(series_list,gran) in DOMAINS.items():
    cells=defaultdict(list)
    tot=0
    for series in series_list:
        try:
            mk={json.loads(l)["ticker"]:json.loads(l) for l in open(f"data/markets_{series}.jsonl")}
            cf=open(f"data/candles_{series}.jsonl")
        except FileNotFoundError: continue
        for line in cf:
            r=json.loads(line); m=mk.get(r["ticker"])
            if not m or m.get("result") not in ("yes","no"): continue
            close=ts(m["close_time"]); seen=set()
            for c in r["candles"]:
                if gran=="min":
                    u=(close-c["t"])//60; grp=next((l for lo,hi,l in MINGRP if lo<=u<=hi),None)
                else:
                    u=(close-c["t"])//3600; grp=next((l for lo,hi,l in HRGRP if lo<=u<=hi),None)
                if not grp or grp in seen: continue
                q=quotes(c)
                if not q: continue
                side,fb,fa,fm=q
                bd=band(fm)
                if not bd or fa>0.995 or fa-fb>0.04: continue
                seen.add(grp)
                win=(m["result"]==side)
                pnl=(1-fa)-fee(fa) if win else -fa-fee(fa)
                cells[(grp,bd)].append({"pnl":pnl,"win":win,"mid":fm,"oos":close>=OOS})
                tot+=1
    rows=[]
    for k,rs in cells.items():
        n=len(rs)
        if n<60: continue
        w=sum(x["win"] for x in rs); ev=sum(x["pnl"] for x in rs)/n*100
        mid=sum(x["mid"] for x in rs)/n
        var=sum(x["mid"]*(1-x["mid"]) for x in rs)
        z=(w-sum(x["mid"] for x in rs))/math.sqrt(var) if var>0 else 0
        p=0.5*math.erfc(z/math.sqrt(2))
        ist=[x["pnl"] for x in rs if not x["oos"]]; oot=[x["pnl"] for x in rs if x["oos"]]
        rows.append({"k":k,"n":n,"wr":w/n,"mid":mid,"ev":ev,"z":z,"p":p,
                     "is":sum(ist)/len(ist)*100 if ist else float("nan"),
                     "oos":sum(oot)/len(oot)*100 if oot else float("nan")})
    if not rows:
        print(f"\n=== {dom}: no cells with n>=60 ==="); continue
    pos=[r for r in rows if r["ev"]>0]
    passed=bh([r["p"] for r in pos]) if pos else set()
    print(f"\n=== {dom}: {tot} obs, {len(rows)} cells, {len(pos)} EV>0, {len(passed)} FDR-pass ===")
    print(f"{'cell':>14} {'n':>5} {'mid':>6} {'WR':>6} {'EV¢':>7} {'z':>6} {'IS¢':>7} {'OOS¢':>7}  flag")
    for r in sorted(rows,key=lambda r:-r["z"])[:10]:
        i=pos.index(r) if r in pos else None
        fl=("FDR" if (i is not None and i in passed) else "")
        both="+both" if (r["is"]>0 and r["oos"]>0) else ""
        print(f"{str(r['k']):>14} {r['n']:>5} {r['mid']:.3f} {r['wr']:.3f} {r['ev']:>+7.3f} "
              f"{r['z']:>+6.2f} {r['is']:>+7.3f} {r['oos']:>+7.3f}  {fl}{both}")
