#!/usr/bin/env python3
"""Pull Polymarket btc-updown 5m/15m markets + minute price history (60d)."""
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

def run(period, step, days):
    path=os.path.join(DATA,f"{period}.jsonl")
    done=set()
    if os.path.exists(path):
        for line in open(path):
            try: done.add(json.loads(line)["ts"])
            except Exception: pass
    now=int(time.time())//step*step
    start=now-days*86400
    n=miss=0
    with open(path,"a") as f:
        for ts in range(start, now-step, step):
            if ts in done: continue
            d=get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-{period}-{ts}")
            if not d:
                miss+=1; continue
            e=d[0]; m=(e.get("markets") or [{}])[0]
            if not e.get("closed") or not m.get("clobTokenIds"):
                continue
            tok=json.loads(m["clobTokenIds"])[0]
            h=get(f"https://clob.polymarket.com/prices-history?market={tok}"
                  f"&startTs={ts-step-60}&endTs={ts+60}&fidelity=1")
            pts=(h or {}).get("history",[])
            try: outcome=json.loads(m.get("outcomePrices","[]"))
            except Exception: outcome=None
            f.write(json.dumps({"ts":ts,"slug":e["slug"],"outcome":outcome,
                                "pts":[[p["t"],p["p"]] for p in pts]})+"\n")
            n+=1
            if n%200==0:
                f.flush(); print(f"{period}: {n} pulled ({miss} missing)",flush=True)
            time.sleep(0.12)
    print(f"{period} DONE: {n} new, {miss} missing",flush=True)

if __name__=="__main__":
    run("5m",300,int(sys.argv[1]) if len(sys.argv)>1 else 60)
    run("15m",900,int(sys.argv[2]) if len(sys.argv)>2 else 60)
    print("ALL DONE",flush=True)
