#!/usr/bin/env python3
"""Funding-persistence carry study (second pass, pre-registered).

Rule: after a funding print <= -1bp on asset X, go LONG at the next minute
mid (maker-assumed; costs charged separately), hold until the first
NON-negative print (ride the persistent regime), exit at mid. Costs 10bps
round trip. Return = funding collected (long receives -rate) + price drift.
Also the SHORT mirror after prints >= +1bp.
IS/OOS split at 2026-07-07. Gate: pooled net > 0 with t >= 2 in IS, OOS same
sign. Report per asset + pooled.
"""
import json, datetime as dt, statistics, math, sys
from collections import defaultdict

def load_mids(t):
    mids={}
    try:
        for line in open(f"data/perp_candles_{t}.jsonl"):
            rec=json.loads(line)
            for c in rec["c"]:
                b,a=c.get("b"),c.get("a")
                if b and a: mids[c["t"]]=(float(b)+float(a))/2
    except FileNotFoundError:
        return {}
    return mids

def mid_near(mids, ts, tol=600):
    for d in range(0, tol+60, 60):
        if ts-d in mids: return mids[ts-d]
        if ts+d in mids: return mids[ts+d]
    return None

def main():
    rates=json.load(open("data/funding_history.json"))
    by=defaultdict(list)
    for r in rates:
        t=dt.datetime.fromisoformat(r["funding_time"].replace("Z","+00:00"))
        by[r["market_ticker"]].append((int(t.timestamp()), float(r["funding_rate"])))
    for k in by: by[k].sort()
    OOS=1783468800  # 2026-07-07
    COST=10.0

    for direction in ("LONG_after_neg","SHORT_after_pos"):
        print(f"\n=== {direction} (ride regime to first opposite print, cost {COST}bps RT) ===")
        print(f"{'asset':>12} {'n':>3} {'hold_periods':>12} {'fund':>6} {'drift':>7} {'net':>7} {'IS':>7} {'OOS':>7}")
        pooled=[]
        for tkr, rs in sorted(by.items()):
            mids=load_mids(tkr)
            if not mids: continue
            episodes=[]
            i=0
            while i < len(rs)-1:
                ts,v=rs[i]
                trig = (v<=-1e-4) if direction=="LONG_after_neg" else (v>=1e-4)
                if not trig: i+=1; continue
                # enter now; ride until first print of opposite sign (or data end)
                j=i+1; fund=0.0
                while j<len(rs):
                    rj=rs[j][1]
                    ok = (rj<0) if direction=="LONG_after_neg" else (rj>0)
                    fund += (-rj if direction=="LONG_after_neg" else rj)*1e4
                    if not ok: break
                    j+=1
                m0=mid_near(mids, ts); m1=mid_near(mids, rs[min(j,len(rs)-1)][0])
                if m0 and m1:
                    drift=(m1-m0)/m0*1e4*(1 if direction=="LONG_after_neg" else -1)
                    episodes.append({"ts":ts,"hold":j-i,"fund":fund,"drift":drift,
                                     "net":fund+drift-COST})
                i=j
            if len(episodes)<5: continue
            n=len(episodes)
            mu=statistics.mean(e["net"] for e in episodes)
            IS=[e["net"] for e in episodes if e["ts"]<OOS]
            OO=[e["net"] for e in episodes if e["ts"]>=OOS]
            print(f"{tkr:>12} {n:>3} {statistics.mean(e['hold'] for e in episodes):>12.1f} "
                  f"{statistics.mean(e['fund'] for e in episodes):>6.1f} "
                  f"{statistics.mean(e['drift'] for e in episodes):>+7.1f} {mu:>+7.1f} "
                  f"{statistics.mean(IS) if IS else float('nan'):>+7.1f} "
                  f"{statistics.mean(OO) if OO else float('nan'):>+7.1f}")
            pooled+=[e["net"] for e in episodes]
        if pooled:
            mu=statistics.mean(pooled); sd=statistics.stdev(pooled)
            print(f"{'POOLED':>12} {len(pooled):>3} net={mu:+.1f}bps/episode "
                  f"t={mu/(sd/math.sqrt(len(pooled))):+.2f}")

if __name__=="__main__":
    main()
