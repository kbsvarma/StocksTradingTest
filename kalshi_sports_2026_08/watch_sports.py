"""Sports lock-lane PAPER watcher — read-only, no credentials, no orders.

Tracks live game markets across tennis/WNBA series, detects the
pre-registered 97-99c held-favorite cells (H=5 and H=10) in real time,
logs each would-be entry WITH an orderbook depth snapshot, then logs the
settlement. Output: state/signals.jsonl, state/settles.jsonl
"""
import json, time, os, urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
os.makedirs(STATE, exist_ok=True)
SERIES = ["KXWTAMATCH", "KXWNBAGAME", "KXWTACHALLENGERMATCH",
          "KXITFMATCH", "KXITFWMATCH", "KXATPMATCH", "KXATPCHALLENGERMATCH",
          "KXLIGAMXGAME", "KXMLSGAME"]   # soccer: paper audition (roadmap step 2)
BANDS = [(0.85,0.90),(0.90,0.93),(0.93,0.95),(0.95,0.97),(0.97,0.99)]
MAX_SPREAD, HOLDS = 0.02, (3, 5, 10)   # 97-99 = validated; rest exploratory paper

def get(u, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "paperwatch/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=10))
        except Exception:
            time.sleep(1.0 * (i + 1))
    return {}

def log(path, rec):
    with open(os.path.join(STATE, path), "a") as f:
        f.write(json.dumps(rec) + "\n")

def fav_of(b, a):
    m = (b + a) / 2
    return ("yes", b, a, m) if m > 0.5 else ("no", 1 - a, 1 - b, 1 - m)

# restart-safe: don't re-signal tickers already logged for a given H
signaled = set()
sig_path = os.path.join(STATE, "signals.jsonl")
if os.path.exists(sig_path):
    for l in open(sig_path):
        try:
            r = json.loads(l)
            signaled.add((r["ticker"], r.get("lo", 0.97), r["H"]))
        except Exception:
            pass
settled_done = set()
set_path = os.path.join(STATE, "settles.jsonl")
if os.path.exists(set_path):
    for l in open(set_path):
        try:
            settled_done.add(json.loads(l)["ticker"])
        except Exception:
            pass

closes = {}          # ticker -> list[(minute_ts, side, fav_mid, fav_bid, fav_ask)]
last_quote = {}      # ticker -> (bid, ask)
last_minute = {}     # ticker -> minute bucket of last poll
meta = {}            # ticker -> series
last_settle_check = 0.0
print("watcher up", flush=True)

while True:
    now = time.time()
    for series in SERIES:
        d = get(f"{BASE}/markets?series_ticker={series}&status=open&limit=100")
        for m in d.get("markets", []):
            tkr = m["ticker"]
            try:
                b = float(m.get("yes_bid_dollars") or 0)
                a = float(m.get("yes_ask_dollars") or 1)
            except (TypeError, ValueError):
                continue
            if not (0 < b <= a < 1):
                continue
            meta[tkr] = series
            mb = int(now // 60)
            if tkr in last_minute and mb > last_minute[tkr] and tkr in last_quote:
                pb, pa = last_quote[tkr]
                s_, fb, fa, fm = fav_of(pb, pa)
                closes.setdefault(tkr, []).append((mb * 60, s_, fm, fb, fa))
                closes[tkr] = closes[tkr][-30:]
                seq = closes[tkr]
                i = len(seq) - 1
                _, cs, cm, cb, ca = seq[i]
                for (blo, bhi) in BANDS:
                    if not (blo <= cm < bhi) or ca - cb > MAX_SPREAD \
                            or ca > bhi + 0.005:
                        continue
                    for H in HOLDS:
                        if (tkr, blo, H) in signaled or len(seq) < H:
                            continue
                        if all(seq[j][1] == cs and seq[j][2] >= blo
                               for j in range(i - H + 1, i)):
                            ob = get(f"{BASE}/markets/{tkr}/orderbook?depth=5")
                            book = (ob.get("orderbook_fp")
                                    or ob.get("orderbook") or {})
                            log("signals.jsonl",
                                {"ts": int(now), "ticker": tkr,
                                 "series": series, "H": H, "lo": blo,
                                 "hi": bhi, "side": cs,
                                 "fav_bid": cb, "fav_ask": ca,
                                 "yes": book.get("yes_dollars"),
                                 "no": book.get("no_dollars")})
                            signaled.add((tkr, blo, H))
                            print(f"SIGNAL {tkr} {blo:.2f} H{H} {cs} "
                                  f"ask={ca:.3f}", flush=True)
            last_minute[tkr] = mb
            last_quote[tkr] = (b, a)
        time.sleep(0.3)
    # settlements for signaled markets
    if now - last_settle_check > 300:
        last_settle_check = now
        for tkr in {t for t, *_ in signaled} - settled_done:
            d = get(f"{BASE}/markets/{tkr}")
            r = (d.get("market") or {}).get("result")
            if r in ("yes", "no"):
                log("settles.jsonl", {"ts": int(now), "ticker": tkr,
                                      "result": r, "series": meta.get(tkr)})
                settled_done.add(tkr)
                print(f"SETTLED {tkr} -> {r}", flush=True)
            time.sleep(0.2)
    time.sleep(20)
