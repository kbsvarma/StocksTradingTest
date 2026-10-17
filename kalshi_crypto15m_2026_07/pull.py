#!/usr/bin/env python3
"""Pull settled 15-min crypto markets + 1-min candles from Kalshi public API.

Usage: python3 pull.py KXBTC15M [KXETH15M ...]
Writes data/markets_{series}.jsonl and data/candles_{series}.jsonl
Public endpoints only (no auth). Polite rate limiting.
"""
import urllib.request, urllib.error, json, time, calendar, sys, os

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
START_TS = calendar.timegm(time.strptime("2026-05-18T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def get(url, retries=5):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if i == retries - 1:
                raise
            time.sleep(1.5 * (2 ** i) if code in (429, 500, 502, 503) else 1.0)
    raise RuntimeError("unreachable")

def to_ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

def pull_markets(series):
    out, cursor = [], None
    while True:
        url = (f"{BASE}/markets?series_ticker={series}&status=settled"
               f"&min_close_ts={START_TS}&limit=1000")
        if cursor:
            url += f"&cursor={cursor}"
        d = get(url)
        ms = d.get("markets", [])
        for m in ms:
            out.append({
                "ticker": m["ticker"],
                "open_time": m["open_time"],
                "close_time": m["close_time"],
                "result": m.get("result"),
                "floor_strike": m.get("floor_strike"),
                "expiration_value": m.get("expiration_value"),
                "volume_fp": m.get("volume_fp"),
                "settlement_value_dollars": m.get("settlement_value_dollars"),
            })
        cursor = d.get("cursor")
        print(f"  {series}: {len(out)} markets so far", flush=True)
        if not cursor or not ms:
            break
        time.sleep(0.15)
    path = os.path.join(DATA, f"markets_{series}.jsonl")
    with open(path, "w") as f:
        for m in out:
            f.write(json.dumps(m) + "\n")
    return out

def pull_candles(series, markets):
    # chunk into groups of 24 consecutive markets (API cap: tickers*window_minutes <= 10000)
    markets = sorted(markets, key=lambda m: m["open_time"])
    path = os.path.join(DATA, f"candles_{series}.jsonl")
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["ticker"])
                except Exception:
                    pass
    todo = [m for m in markets if m["ticker"] not in done]
    print(f"  {series}: {len(todo)} markets need candles ({len(done)} cached)", flush=True)
    # greedy chunks respecting API cap: n_tickers * window_minutes <= 10000
    chunks, cur = [], []
    for m in todo:
        trial = cur + [m]
        span = (max(to_ts(x["close_time"]) for x in trial) + 60
                - (min(to_ts(x["open_time"]) for x in trial) - 60)) // 60
        if cur and len(trial) * span > 9000:
            chunks.append(cur)
            cur = [m]
        else:
            cur = trial
    if cur:
        chunks.append(cur)
    with open(path, "a") as f:
        for i, chunk in enumerate(chunks):
            tickers = ",".join(m["ticker"] for m in chunk)
            start = min(to_ts(m["open_time"]) for m in chunk) - 60
            end = max(to_ts(m["close_time"]) for m in chunk) + 60
            d = get(f"{BASE}/markets/candlesticks?market_tickers={tickers}"
                    f"&start_ts={start}&end_ts={end}&period_interval=1")
            got = {g["market_ticker"]: g["candlesticks"] for g in d.get("markets", [])}
            for m in chunk:
                cs = got.get(m["ticker"], [])
                compact = [{
                    "t": c["end_period_ts"],
                    "bo": c["yes_bid"].get("open_dollars"), "bc": c["yes_bid"].get("close_dollars"),
                    "bl": c["yes_bid"].get("low_dollars"), "bh": c["yes_bid"].get("high_dollars"),
                    "ao": c["yes_ask"].get("open_dollars"), "ac": c["yes_ask"].get("close_dollars"),
                    "al": c["yes_ask"].get("low_dollars"), "ah": c["yes_ask"].get("high_dollars"),
                    "pc": (c.get("price") or {}).get("close_dollars"),
                    "v": c.get("volume_fp"),
                } for c in cs]
                f.write(json.dumps({"ticker": m["ticker"], "candles": compact}) + "\n")
            f.flush()
            if i % 40 == 0:
                print(f"  {series}: chunk {i + 1}/{len(chunks)}", flush=True)
            time.sleep(0.25)

if __name__ == "__main__":
    for series in sys.argv[1:]:
        print(f"== {series}", flush=True)
        mpath = os.path.join(DATA, f"markets_{series}.jsonl")
        if os.path.exists(mpath):
            markets = [json.loads(l) for l in open(mpath)]
            print(f"  {series}: {len(markets)} markets (cached)", flush=True)
        else:
            markets = pull_markets(series)
        pull_candles(series, markets)
    print("DONE", flush=True)
