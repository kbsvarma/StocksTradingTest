#!/usr/bin/env python3
"""Pull settled daily-cadence markets + HOURLY candles for late-favorite study."""
import json, urllib.request, time, calendar, sys, os
BASE="https://api.elections.kalshi.com/trade-api/v2"
DATA=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
START=calendar.timegm(time.strptime("2026-05-18T00:00:00Z","%Y-%m-%dT%H:%M:%SZ"))
def get(url, retries=4):
    for i in range(retries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r: return json.load(r)
        except Exception:
            if i==retries-1: raise
            time.sleep(1.2*(i+1))
def ts(s): return calendar.timegm(time.strptime(s,"%Y-%m-%dT%H:%M:%SZ"))

for series in sys.argv[1:]:
    mpath=os.path.join(DATA,f"markets_{series}.jsonl")
    if not os.path.exists(mpath):
        out=[]; cur=None
        while True:
            url=f"{BASE}/markets?series_ticker={series}&status=settled&min_close_ts={START}&limit=1000"
            if cur: url+=f"&cursor={cur}"
            d=get(url); ms=d.get("markets",[])
            for m in ms:
                out.append({k:m.get(k) for k in ("ticker","open_time","close_time",
                    "result","floor_strike","cap_strike","strike_type","volume_fp","event_ticker")})
            cur=d.get("cursor")
            if not cur or not ms: break
            time.sleep(0.15)
        with open(mpath,"w") as f:
            for m in out: f.write(json.dumps(m)+"\n")
        print(f"{series}: {len(out)} settled markets",flush=True)
    markets=[json.loads(l) for l in open(mpath)]
    cpath=os.path.join(DATA,f"candles_{series}.jsonl")
    done=set()
    if os.path.exists(cpath):
        for line in open(cpath):
            try: done.add(json.loads(line)["ticker"])
            except Exception: pass
    todo=[m for m in markets if m["ticker"] not in done and m.get("result") in ("yes","no")]
    todo.sort(key=lambda m:m["open_time"])
    # greedy chunks: candles = n * span_hours <= 9000, url len cap 60 tickers
    i=0
    with open(cpath,"a") as f:
        while i<len(todo):
            chunk=[todo[i]]; j=i+1
            while j<len(todo) and len(chunk)<60:
                trial=chunk+[todo[j]]
                span_h=(max(ts(x["close_time"]) for x in trial)-min(ts(x["open_time"]) for x in trial))//3600+2
                if len(trial)*span_h>9000: break
                chunk=trial; j+=1
            i=j
            t0=min(ts(x["open_time"]) for x in chunk)-3600
            t1=max(ts(x["close_time"]) for x in chunk)+3600
            tk=",".join(x["ticker"] for x in chunk)
            try:
                d=get(f"{BASE}/markets/candlesticks?market_tickers={tk}&start_ts={t0}&end_ts={t1}&period_interval=60")
            except Exception as e:
                print(f"{series} chunk ERR {e}",flush=True); continue
            got={g["market_ticker"]:g["candlesticks"] for g in d.get("markets",[])}
            for m in chunk:
                cs=got.get(m["ticker"],[])
                f.write(json.dumps({"ticker":m["ticker"],"candles":[
                    {"t":c["end_period_ts"],"b":c["yes_bid"].get("close_dollars"),
                     "a":c["yes_ask"].get("close_dollars"),"v":c.get("volume_fp")} for c in cs]})+"\n")
            f.flush(); time.sleep(0.2)
    print(f"{series} candles done",flush=True)
print("TIMELY PULL DONE",flush=True)
