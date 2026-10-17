#!/usr/bin/env python3
"""Watch open 15M crypto markets; snapshot orderbook whenever the signal
condition is near: 5.5-8.5 min to close and favorite ask >= 0.90.
Public endpoints only. Runs for --minutes then exits."""
import urllib.request, json, time, calendar, sys, os

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_snapshots.jsonl")

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def to_ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

def main(minutes):
    end = time.time() + minutes * 60
    seen_logged = {}
    while time.time() < end:
        for s in SERIES:
            try:
                d = get(f"{BASE}/markets?series_ticker={s}&status=open&limit=5")
            except Exception:
                continue
            for m in d.get("markets", []):
                tkr = m["ticker"]
                ttc = to_ts(m["close_time"]) - time.time()
                if not (330 <= ttc <= 510):
                    continue
                yb = float(m.get("yes_bid_dollars") or 0)
                ya = float(m.get("yes_ask_dollars") or 1)
                fav_ask = ya if (yb + ya) / 2 > 0.5 else 1 - yb
                # log book once per market per minute bucket while in window
                bucket = (tkr, int(ttc // 60))
                if fav_ask >= 0.90 and bucket not in seen_logged:
                    seen_logged[bucket] = 1
                    try:
                        ob = get(f"{BASE}/markets/{tkr}/orderbook?depth=6")
                    except Exception:
                        ob = None
                    rec = {"ts": int(time.time()), "ticker": tkr, "ttc_sec": int(ttc),
                           "yes_bid": yb, "yes_ask": ya, "book": ob}
                    with open(OUT, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    print(f"logged {tkr} ttc={int(ttc)}s bid/ask={yb}/{ya}", flush=True)
        time.sleep(20)

if __name__ == "__main__":
    mins = float(sys.argv[1]) if len(sys.argv) > 1 else 45
    main(mins)
