"""Pull settled game markets + 1-min in-game candles for the top sports
families. Resume-safe, 429-backoff. Output: data/games_<series>.jsonl"""
import json, time, urllib.request, calendar, os, sys

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
SERIES = ["KXGOLDD", "KXSILVERD", "KXCOPPERD"]

T0 = int(time.time()) - 55 * 86400

def get(u, tries=8):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "research/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=15))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (i + 1))
                continue
            raise
    raise RuntimeError("429 wall")

def ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

for series in SERIES:
    out_path = os.path.join(HERE, "data", f"games_{series}.jsonl")
    seen = set()
    if os.path.exists(out_path):
        for l in open(out_path):
            try: seen.add(json.loads(l)["ticker"])
            except Exception: pass
    mkts, cur = [], ""
    while True:
        u = (f"{BASE}/markets?series_ticker={series}&status=settled&limit=100"
             f"&min_close_ts={T0}" + (f"&cursor={cur}" if cur else ""))
        d = get(u)
        mkts += d.get("markets", [])
        cur = d.get("cursor") or ""
        if not cur: break
        time.sleep(0.25)
    print(f"{series}: {len(mkts)} settled markets ({len(seen)} already pulled)",
          flush=True)
    out = open(out_path, "a")
    n = 0
    for m in mkts:
        tkr = m["ticker"]
        if tkr in seen or m.get("result") not in ("yes", "no"):
            continue
        try:
            o, c = ts(m["open_time"]), ts(m["close_time"])
        except Exception:
            continue
        if c - o < 600:
            continue
        w0 = max(o, c - 6 * 3600)      # markets list days early; game action
        try:                           # lives in the final hours before close
            cd = get(f"{BASE}/series/{series}/markets/{tkr}/candlesticks?"
                     f"start_ts={w0}&end_ts={c}&period_interval=1")
        except Exception as e:
            print("SKIP", tkr, e, flush=True)
            continue
        out.write(json.dumps({"ticker": tkr, "series": series,
                              "open_ts": o, "close_ts": c,
                              "result": m["result"],
                              "volume_fp": m.get("volume_fp"),
                              "candles": cd.get("candlesticks", [])}) + "\n")
        out.flush()
        n += 1
        if n % 100 == 0:
            print(f"  {series}: {n} pulled", flush=True)
        time.sleep(0.5)
    out.close()
    print(f"{series} DONE (+{n})", flush=True)
print("ALL DONE", flush=True)
