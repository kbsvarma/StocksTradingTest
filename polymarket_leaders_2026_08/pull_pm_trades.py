"""Polymarket leader study: pull wallet-attributed trades for closed sports
markets (last ~60d). Self-reports per-market history depth."""
import json, time, urllib.request, os

HERE = os.path.dirname(os.path.abspath(__file__))
def get(u, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "research/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=15))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None

# 1) enumerate closed sports events via gamma (tags) — last 60 days
TAGS = ["mlb", "wnba", "tennis", "nba", "soccer", "ufc", "baseball"]
markets = {}
for tag in TAGS:
    off = 0
    while off < 2000:
        ev = get(f"https://gamma-api.polymarket.com/events?tag_slug={tag}"
                 f"&closed=true&limit=100&offset={off}&order=endDate&ascending=false")
        if not isinstance(ev, list) or not ev:
            break
        stale = False
        for e in ev:
            end = e.get("endDate") or ""
            if end and end < "2026-06-05":
                stale = True
                continue
            for m in e.get("markets", []):
                cid = m.get("conditionId")
                if cid and m.get("question"):
                    markets[cid] = {"q": m["question"][:90], "tag": tag,
                                    "end": end, "slug": e.get("slug", ""),
                                    "outcomes": m.get("outcomes")}
        off += 100
        if stale:
            break
        time.sleep(0.3)
    print(f"{tag}: cumulative markets {len(markets)}", flush=True)
json.dump(markets, open(os.path.join(HERE, "data", "pm_markets.json"), "w"))

# 2) pull trades per market (paged), record depth
out = open(os.path.join(HERE, "data", "pm_trades.jsonl"), "a")
seen = set()
p = os.path.join(HERE, "data", "pm_trades.jsonl")
for l in open(p):
    try: seen.add(json.loads(l)["cid"])
    except Exception: pass
def is_moneyline(q):
    ql = q.lower()
    if not ("beat" in ql or "win" in ql or " vs" in ql):
        return False
    bad = ("by more than", "combined", "total", "points", "spread",
           "score", "o/u", "home run", "strikeout", "assists", "rebounds")
    return not any(b in ql for b in bad)
markets = {c: m for c, m in markets.items() if is_moneyline(m["q"])}
print(f"moneyline-filtered: {len(markets)} markets", flush=True)
n = 0
for cid, meta in markets.items():
    if cid in seen:
        continue
    rows, off = [], 0
    while off < 20000:
        tr = get(f"https://data-api.polymarket.com/trades?market={cid}"
                 f"&limit=500&offset={off}")
        if not isinstance(tr, list) or not tr:
            break
        rows += [{"w": t.get("proxyWallet"), "s": t.get("side"),
                  "sz": t.get("size"), "px": t.get("price"),
                  "ts": t.get("timestamp"), "o": t.get("outcome")}
                 for t in tr]
        if len(tr) < 500:
            break
        off += 500
        time.sleep(0.2)
    out.write(json.dumps({"cid": cid, **meta, "n": len(rows),
                          "trades": rows}) + "\n")
    out.flush()
    n += 1
    if n % 100 == 0:
        print(f"trades pulled for {n} markets", flush=True)
    time.sleep(0.25)
print("ALL DONE", n, flush=True)
