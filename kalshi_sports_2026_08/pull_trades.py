import json, time, urllib.request, os
BASE = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
FAMS = ["KXMLBGAME", "KXWNBAGAME"]
def get(u, tries=8):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "research/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=15))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (i + 1)); continue
            raise
    raise RuntimeError("wall")
for fam in FAMS:
    src = os.path.join(HERE, "data", f"games_{fam}.jsonl")
    dst = os.path.join(HERE, "data", f"trades_{fam}.jsonl")
    done = set()
    if os.path.exists(dst):
        for l in open(dst):
            try: done.add(json.loads(l)["ticker"])
            except Exception: pass
    games = [json.loads(l) for l in open(src)]
    print(f"{fam}: {len(games)} games, {len(done)} already pulled", flush=True)
    out = open(dst, "a")
    n = 0
    for g in games:
        tkr = g["ticker"]
        if tkr in done: continue
        trades, cur = [], ""
        while True:
            u = (f"{BASE}/markets/trades?ticker={tkr}&limit=1000"
                 + (f"&cursor={cur}" if cur else ""))
            try: d = get(u)
            except Exception as e:
                print("SKIP", tkr, e, flush=True); trades = None; break
            trades += d.get("trades", [])
            cur = d.get("cursor") or ""
            if not cur: break
            time.sleep(0.25)
        if trades is None: continue
        slim = [{"t": t["created_time"], "c": t.get("count_fp"),
                 "yp": t.get("yes_price_dollars"), "ts_": t.get("taker_side"),
                 "blk": t.get("is_block_trade")} for t in trades]
        out.write(json.dumps({"ticker": tkr, "result": g["result"],
                              "close_ts": g["close_ts"], "trades": slim}) + "\n")
        out.flush()
        n += 1
        if n % 50 == 0: print(f"  {fam}: {n} games", flush=True)
        time.sleep(0.3)
    out.close()
    print(f"{fam} DONE (+{n})", flush=True)
print("ALL DONE", flush=True)
