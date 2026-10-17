#!/usr/bin/env python3
"""Pull 1-min perp candles (bid/ask OHLC) for the cross-product study.
Endpoint serves <=60 candles per call (1h windows). Public, no auth."""
import json, urllib.request, time, os, sys

BASE = "https://external-api.kalshi.com/trade-api/v2/margin/markets"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DAYS = 42

def get(url, retries=5):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.2 * (i + 1))

if __name__ == "__main__":
    now = int(time.time()) // 3600 * 3600
    start_all = now - DAYS * 86400
    for tkr in sys.argv[1:]:
        path = os.path.join(DATA, f"perp_candles_oi_{tkr}.jsonl")
        done_ts = set()
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    try:
                        done_ts.add(json.loads(line)["h"])
                    except Exception:
                        pass
        with open(path, "a") as f:
            n_calls = 0
            for h in range(start_all, now, 3600):
                if h in done_ts:
                    continue
                d = get(f"{BASE}/{tkr}/candlesticks?start_ts={h}&end_ts={h+3600}"
                        f"&period_interval=1")
                cs = d.get("candlesticks", [])
                compact = [{"t": c["end_period_ts"],
                            "b": c["bid"].get("close"), "a": c["ask"].get("close"),
                            "bl": c["bid"].get("low"), "ah": c["ask"].get("high"), "oi": c.get("open_interest")}
                           for c in cs]
                f.write(json.dumps({"h": h, "c": compact}) + "\n")
                n_calls += 1
                if n_calls % 100 == 0:
                    f.flush()
                    print(f"{tkr}: {n_calls} hours pulled", flush=True)
                time.sleep(0.12)
        print(f"{tkr} DONE", flush=True)
    print("ALL DONE", flush=True)
