#!/usr/bin/env python3
"""Parallel-worker Polymarket updown puller: pull2.py <period> <step> <day_lo> <day_hi> <part>"""
import json, urllib.request, time, os, sys

DATA=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
def get(url, retries=4):
    for i in range(retries):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':'research/1.0'})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if i==retries-1: return None
            time.sleep(1.0*(i+1))

period, step, day_lo, day_hi, part = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
path=os.path.join(DATA,f"{period}_v2part{part}.jsonl")
done=set()
if os.path.exists(path):
    for line in open(path):
        try: done.add(json.loads(line)["ts"])
        except Exception: pass
now=int(time.time())//step*step
lo, hi = now-day_hi*86400, now-day_lo*86400
n=0
with open(path,"a") as f:
    for ts in range(lo, hi, step):
        if ts in done: continue
        d=get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{period}-{ts}")
        if not d: continue
        e=d[0]; m=(e.get("markets") or [{}])[0]
        if not e.get("closed") or not m.get("clobTokenIds"): continue
        tok=json.loads(m["clobTokenIds"])[0]
        h=get(f"https://clob.polymarket.com/prices-history?market={tok}"
              f"&startTs={ts-60}&endTs={ts+step+60}&fidelity=1")
        pts=(h or {}).get("history",[])
        try: outcome=json.loads(m.get("outcomePrices","[]"))
        except Exception: outcome=None
        f.write(json.dumps({"ts":ts,"outcome":outcome,
                            "pts":[[p["t"],p["p"]] for p in pts]})+"\n")
        n+=1
        if n%300==0: f.flush(); print(f"{period} p{part}: {n}",flush=True)
        time.sleep(0.10)
print(f"{period} part{part} DONE n={n}",flush=True)
