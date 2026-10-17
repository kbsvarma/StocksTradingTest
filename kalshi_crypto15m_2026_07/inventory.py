#!/usr/bin/env python3
"""Inventory every Kalshi Crypto-category series: structure, history, liquidity."""
import urllib.request, urllib.error, json, time, os, sys

BASE = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "universe_inventory.jsonl")

def get(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(1.2 * (i + 1))

series = get(f"{BASE}/series?category=Crypto")["series"]
print(f"{len(series)} crypto series", flush=True)

with open(OUT, "w") as f:
    for i, s in enumerate(series):
        tkr = s["ticker"]
        rec = {"ticker": tkr, "title": s.get("title"), "frequency": s.get("frequency"),
               "fee_type": s.get("fee_type"), "fee_multiplier": s.get("fee_multiplier")}
        d = get(f"{BASE}/markets?series_ticker={tkr}&status=settled&limit=100")
        ms = (d or {}).get("markets", [])
        rec["recent_settled"] = len(ms)
        if ms:
            vols = sorted(float(m.get("volume_fp") or 0) for m in ms)
            rec["median_vol"] = vols[len(vols) // 2]
            rec["max_vol"] = vols[-1]
            m0 = ms[0]
            rec["sample_ticker"] = m0["ticker"]
            rec["strike_type"] = m0.get("strike_type")
            rec["last_close"] = m0.get("close_time")
            rec["open_to_close_min"] = None
            try:
                import calendar
                ot = calendar.timegm(time.strptime(m0["open_time"], "%Y-%m-%dT%H:%M:%SZ"))
                ct = calendar.timegm(time.strptime(m0["close_time"], "%Y-%m-%dT%H:%M:%SZ"))
                rec["open_to_close_min"] = (ct - ot) // 60
            except Exception:
                pass
            # events per day proxy: distinct event_tickers in the 100
            rec["distinct_events"] = len({m.get("event_ticker") for m in ms})
        f.write(json.dumps(rec) + "\n")
        if i % 25 == 0:
            print(f"{i}/{len(series)} {tkr}", flush=True)
        time.sleep(0.30)
print("INVENTORY DONE", flush=True)
