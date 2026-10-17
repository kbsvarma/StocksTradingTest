#!/usr/bin/env python3
"""Replicate Codex's liquidation-unwind bot on the 42d archive (BTC/ETH).

Signal (hourly scan): |3h return| >= 1% AND OI change over same 3h < 0
-> fade the move at next-minute mid. Hold 6h, exit at mid. One position per
asset; no overlap. Controls: same signal with OI RISING (should lose per
Codex), and no-OI-condition fade.
Costs reported at THREE fee assumptions per side: taker 12bp (standard),
taker 80bp (our current quote), maker 5bp (post-only entries/exits).
Plus half-spread 1.5bp each way.
"""
import json, statistics, math, sys

def load(t):
    mids={}; oi={}
    for line in open(f"data/perp_candles_oi_{t}.jsonl"):
        rec=json.loads(line)
        for c in rec["c"]:
            b,a=c.get("b"),c.get("a")
            if b and a: mids[c["t"]]=(float(b)+float(a))/2
            if c.get("oi") is not None:
                try: oi[c["t"]]=float(c["oi"])
                except (TypeError,ValueError): pass
    return mids, oi

def run(tkr, oi_cond):
    mids,oi=load(tkr)
    ts=sorted(mids)
    t0,t1=ts[0],ts[-1]
    trades=[]
    t=t0+3*3600
    busy_until=0
    while t<=t1-6*3600:
        if t<busy_until or t not in mids or t-3*3600 not in mids \
           or t not in oi or t-3*3600 not in oi:
            t+=3600; continue
        r3=(mids[t]-mids[t-3*3600])/mids[t-3*3600]
        doi=oi[t]-oi[t-3*3600]
        cond = (doi<0) if oi_cond=="down" else ((doi>0) if oi_cond=="up" else True)
        if abs(r3)>=0.01 and cond:
            m_in=mids.get(t+60) or mids[t]
            m_out=mids.get(t+6*3600+60)
            if m_out:
                side=-1 if r3>0 else 1          # fade
                gross=side*(m_out-m_in)/m_in*1e4
                trades.append({"ts":t,"gross":gross})
                busy_until=t+6*3600
        t+=3600
    return trades

OOS=1783468800
for oi_cond,label in (("down","OI FALLING (the signal)"),("up","OI RISING (control)"),("any","no OI condition (control)")):
    allt=[]
    per={}
    for tkr in ("KXBTCPERP","KXETHPERP"):
        tr=run(tkr,oi_cond); per[tkr]=tr; allt+=tr
    n=len(allt)
    if n<10:
        print(f"{label}: n={n} too few"); continue
    g=[t["gross"] for t in allt]
    mu=statistics.mean(g); sd=statistics.stdev(g)
    IS=[t["gross"] for t in allt if t["ts"]<OOS]; OO=[t["gross"] for t in allt if t["ts"]>=OOS]
    print(f"\n=== {label} ===")
    print(f"pooled n={n} (BTC {len(per['KXBTCPERP'])}/ETH {len(per['KXETHPERP'])}) "
          f"gross={mu:+.1f}bps/trade t={mu/(sd/math.sqrt(n)):+.2f} "
          f"IS={statistics.mean(IS) if IS else float('nan'):+.1f} OOS={statistics.mean(OO) if OO else float('nan'):+.1f}")
    for fees,fl in ((27.0,"taker12+spread"),(163.0,"taker80+spread"),(13.0,"maker5+spread")):
        print(f"  net @ {fl:>15}: {mu-fees:+.1f}bps/trade")
