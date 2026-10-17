#!/usr/bin/env python3
"""Late-favorite calibration across timely non-crypto domains.
For each settled market: at H hours-to-close, favorite side/price from hourly
candle closes; realized WR vs price band; taker EV net of exact fees.
Groups: weather-high, weather-low, index, gas, rain. IS/OOS split 2026-07-07."""
import json, os, sys, math, calendar, time
from collections import defaultdict

DATA=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
GROUP={"KXHIGHLAX":"wx_high","KXHIGHNY":"wx_high","KXHIGHMIA":"wx_high",
"KXHIGHCHI":"wx_high","KXHIGHTPHX":"wx_high","KXHIGHTSEA":"wx_high",
"KXHIGHTSFO":"wx_high","KXHIGHTATL":"wx_high","KXHIGHTDAL":"wx_high",
"KXHIGHAUS":"wx_high","KXLOWTPHX":"wx_low","KXLOWTNYC":"wx_low",
"KXINXDUD":"index","KXNASDAQDUD":"index","KXAAAGASD":"gas","KXRAINNYC":"rain"}
OOS=calendar.timegm(time.strptime("2026-07-07T00:00:00Z","%Y-%m-%dT%H:%M:%SZ"))
def ts(s): return calendar.timegm(time.strptime(s,"%Y-%m-%dT%H:%M:%SZ"))
def fee(p): return 0.07*p*(1-p)

obs=[]
for series,grp in GROUP.items():
    try:
        mk={json.loads(l)["ticker"]:json.loads(l) for l in open(f"{DATA}/markets_{series}.jsonl")}
        cf=open(f"{DATA}/candles_{series}.jsonl")
    except FileNotFoundError:
        continue
    for line in cf:
        r=json.loads(line)
        m=mk.get(r["ticker"])
        if not m or m.get("result") not in ("yes","no"): continue
        close=ts(m["close_time"])
        by_h={}
        for c in r["candles"]:
            h=(close-c["t"])//3600
            if c.get("b") and c.get("a"):
                by_h[h]=(float(c["b"]),float(c["a"]))
        for H in (12,8,6,4,3,2,1):
            q=by_h.get(H)
            if not q: continue
            b,a=q
            if a<=0 or a>1 or b<0 or b>a: continue
            mid=(b+a)/2
            side,fb,fa = ("yes",b,a) if mid>0.5 else ("no",1-a,1-b)
            fm=max(mid,1-mid)
            if fa-fb>0.04 or fa>0.99: continue
            win=(m["result"]==side)
            pnl=(1-fa)-fee(fa) if win else -fa-fee(fa)
            obs.append({"grp":grp,"H":H,"fm":fm,"fa":fa,"win":win,"pnl":pnl,
                        "oos":close>=OOS,"series":series})

print(f"{len(obs)} observations")
def band(fm):
    if fm>=0.95: return "95-99"
    if fm>=0.90: return "90-95"
    if fm>=0.80: return "80-90"
    return None
cells=defaultdict(list)
for o in obs:
    bd=band(o["fm"])
    if bd: cells[(o["grp"],o["H"],bd)].append(o)
print(f"{'group':>8} {'H':>3} {'band':>6} {'n':>5} {'WR':>6} {'avg_ask':>7} {'EV¢':>7} {'t':>6} {'IS¢':>7} {'OOS¢':>7}")
rows=[]
for k in sorted(cells):
    rs=cells[k]; n=len(rs)
    if n<80: continue
    w=sum(r["win"] for r in rs); ev=sum(r["pnl"] for r in rs)/n*100
    sd=(sum((r["pnl"]*100-ev)**2 for r in rs)/(n-1))**0.5
    ist=[r["pnl"] for r in rs if not r["oos"]]; oot=[r["pnl"] for r in rs if r["oos"]]
    ise=sum(ist)/len(ist)*100 if ist else float("nan")
    oose=sum(oot)/len(oot)*100 if oot else float("nan")
    t_=ev/(sd/math.sqrt(n)) if sd else 0
    aa=sum(r["fa"] for r in rs)/n
    print(f"{k[0]:>8} {k[1]:>3} {k[2]:>6} {n:>5} {w/n:.4f} {aa:>7.4f} {ev:>+7.3f} {t_:>+6.2f} {ise:>+7.3f} {oose:>+7.3f}")
