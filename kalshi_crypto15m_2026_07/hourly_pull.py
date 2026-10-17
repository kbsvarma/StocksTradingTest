#!/usr/bin/env python3
"""Pull KXBTCD hourly above/below: settled markets 70d + 1-min candles for
liquid strikes (volume >= 5000) over each market's final 65 minutes."""
import urllib.request, urllib.error, json, time, calendar, os

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
START_TS = calendar.timegm(time.strptime("2026-05-18T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def get(url, retries=5):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))

def to_ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

# enumerate settled markets
out, cursor = [], None
while True:
    url = f"{BASE}/markets?series_ticker=KXBTCD&status=settled&min_close_ts={START_TS}&limit=1000"
    if cursor:
        url += f"&cursor={cursor}"
    d = get(url)
    ms = d.get("markets", [])
    for m in ms:
        out.append({"ticker": m["ticker"], "open_time": m["open_time"],
                    "close_time": m["close_time"], "result": m.get("result"),
                    "floor_strike": m.get("floor_strike"),
                    "volume_fp": m.get("volume_fp")})
    cursor = d.get("cursor")
    print(f"markets: {len(out)}", flush=True)
    if not cursor or not ms:
        break
    time.sleep(0.2)
with open(os.path.join(DATA, "markets_KXBTCD.jsonl"), "w") as f:
    for m in out:
        f.write(json.dumps(m) + "\n")

liquid = [m for m in out if float(m.get("volume_fp") or 0) >= 5000
          and m.get("result") in ("yes", "no")]
print(f"{len(out)} settled, {len(liquid)} liquid (vol>=5k)", flush=True)

# candles: final 65 min per market; greedy chunks n*(span+2) <= 9000
liquid.sort(key=lambda m: m["close_time"])
chunks, cur = [], []
def span_min(ch):
    s = min(to_ts(m["close_time"]) for m in ch) - 65 * 60
    e = max(to_ts(m["close_time"]) for m in ch) + 60
    return (e - s) // 60, s, e
for m in liquid:
    trial = cur + [m]
    sp, _, _ = span_min(trial)
    if cur and len(trial) * sp > 9000:
        chunks.append(cur); cur = [m]
    else:
        cur = trial
if cur:
    chunks.append(cur)
print(f"{len(chunks)} candle chunks", flush=True)

path = os.path.join(DATA, "candles_KXBTCD.jsonl")
with open(path, "w") as f:
    for i, ch in enumerate(chunks):
        sp, s, e = span_min(ch)
        tickers = ",".join(m["ticker"] for m in ch)
        d = get(f"{BASE}/markets/candlesticks?market_tickers={tickers}"
                f"&start_ts={s}&end_ts={e}&period_interval=1")
        got = {g["market_ticker"]: g["candlesticks"] for g in d.get("markets", [])}
        for m in ch:
            cs = got.get(m["ticker"], [])
            compact = [{"t": c["end_period_ts"],
                        "bo": c["yes_bid"].get("open_dollars"), "bc": c["yes_bid"].get("close_dollars"),
                        "ao": c["yes_ask"].get("open_dollars"), "ac": c["yes_ask"].get("close_dollars"),
                        "pc": (c.get("price") or {}).get("close_dollars"),
                        "v": c.get("volume_fp")} for c in cs]
            f.write(json.dumps({"ticker": m["ticker"], "candles": compact}) + "\n")
        f.flush()
        if i % 25 == 0:
            print(f"chunk {i+1}/{len(chunks)}", flush=True)
        time.sleep(0.25)
print("HOURLY DONE", flush=True)
