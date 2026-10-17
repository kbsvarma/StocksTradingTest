#!/usr/bin/env python3
"""Polymarket btc-updown analysis. Data: per-market trade-price series
(token0=Up), outcome. 15m: crypto-frozen replication (unbroken fav 90-99 @
rem 4-8) + own surface. 5m: own surface (rem 1-4 x bands).
Fills: entry at last trade price +1c conservative half-spread; fees 0
(Polymarket) — flagged. IS/OOS split 2026-07-07."""
import json, glob, math, statistics
from collections import defaultdict
OOS=1783468800
def load(period):
    rows=[]
    for f in glob.glob(f"data/{period}_part*.jsonl")+glob.glob(f"data/{period}.jsonl"):
        for line in open(f):
            try: rows.append(json.loads(line))
            except Exception: pass
    return rows
def minute_closes(pts, close_ts, life):
    by={}
    for t,p in pts:
        rem=(close_ts-t)//60
        if 0<=rem<=life: by[rem]=p     # last point in each minute wins
    return by
def surface(rows, life, rem_groups, label):
    cells=defaultdict(list)
    for r in rows:
        out=r.get("outcome")
        if not out or len(out)<2: continue
        try: up_won = float(out[0])>0.5
        except Exception: continue
        by=minute_closes(r["pts"], r["ts"], life)
        seen=set()
        for rem,p in sorted(by.items()):
            grp=next((l for lo,hi,l in rem_groups if lo<=rem<=hi),None)
            if not grp or grp in seen: continue
            fav_up = p>0.5
            fm=max(p,1-p)
            bd=("95-99" if fm>=0.95 else "90-95" if fm>=0.90 else
                "80-90" if fm>=0.80 else "70-80" if fm>=0.70 else None)
            if not bd: continue
            seen.add(grp)
            entry=min(fm+0.01,0.995)     # +1c conservative
            win = (up_won==fav_up)
            pnl=(1-entry) if win else -entry
            cells[(grp,bd)].append({"pnl":pnl,"win":win,"mid":fm,"oos":r["ts"]>=OOS})
    print(f"\n=== {label} ===")
    print(f"{'cell':>16} {'n':>5} {'WR':>6} {'mid':>6} {'EV¢':>7} {'z':>6} {'IS¢':>7} {'OOS¢':>7}")
    for k in sorted(cells):
        rs=cells[k]; n=len(rs)
        if n<80: continue
        w=sum(x["win"] for x in rs); mid=sum(x["mid"] for x in rs)/n
        var=sum(x["mid"]*(1-x["mid"]) for x in rs)
        z=(w-sum(x["mid"] for x in rs))/math.sqrt(var) if var else 0
        ev=sum(x["pnl"] for x in rs)/n*100
        ist=[x["pnl"] for x in rs if not x["oos"]]; oot=[x["pnl"] for x in rs if x["oos"]]
        print(f"{str(k):>16} {n:>5} {w/n:.4f} {mid:.3f} {ev:>+7.3f} {z:>+6.2f} "
              f"{(sum(ist)/len(ist)*100 if ist else float('nan')):>+7.3f} "
              f"{(sum(oot)/len(oot)*100 if oot else float('nan')):>+7.3f}")

m15=load("15m"); m5=load("5m")
print(f"15m markets: {len(m15)}  5m markets: {len(m5)}")
surface(m15, 16, [(1,3,"1-3m"),(4,8,"4-8m"),(9,15,"9-15m")], "POLYMARKET 15m surface")
surface(m5, 6, [(1,1,"1m"),(2,2,"2m"),(3,4,"3-4m")], "POLYMARKET 5m surface")
