#!/usr/bin/env python3
"""Pull score tapes for the PREREG_LIVESCORE study.

Enumerates WTA singles per day over the Kalshi archive window, then
pulls each match's point tape. Records the coverage/point_source flags
verbatim so the analysis can separate OBSERVED (~Jul 28 onward) from
RECONSTRUCTED (older) rather than silently mixing data qualities.

Resume-safe. Basic tier: 60 req/min, 10k/day -> sleep 1.1s.
Output: data/tape_index.jsonl (per day), data/tape_wta.jsonl (per match)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

BASE = "https://api.livetennisapi.com/api/public/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
IDX = os.path.join(DATA, "tape_index.jsonl")
OUT = os.path.join(DATA, "tape_wta.jsonl")
START, END = date(2026, 5, 25), date(2026, 8, 6)


def key():
    p = os.path.expanduser("~/.livetennis_key")
    return (os.environ.get("LIVETENNIS_KEY") or open(p).read()).strip()


K = key()


def get(path, params=None, tries=4):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "X-API-Key": K, "User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5.0 * (i + 1))
                continue
            return {"_http": e.code}
        except Exception:
            if i == tries - 1:
                return {"_err": 1}
            time.sleep(2.0 * (i + 1))
    return {"_err": 1}


def index_days():
    done = set()
    if os.path.exists(IDX):
        for line in open(IDX):
            try:
                done.add(json.loads(line)["day"])
            except Exception:
                pass
    d = START
    days = []
    while d <= END:
        if d.isoformat() not in done:
            days.append(d.isoformat())
        d += timedelta(days=1)
    print(f"indexing {len(days)} days", flush=True)
    with open(IDX, "a") as out:
        for n, day in enumerate(days, 1):
            nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            ms, off = [], 0
            while True:
                r = get("/history/matches", {"from": day, "to": nxt,
                                             "tour": "wta", "limit": 50,
                                             "offset": off})
                batch = (r or {}).get("data") or []
                ms += [m for m in batch if not m.get("is_doubles")
                       and (m.get("scheduled_time") or "").startswith(day)]
                if len(batch) < 50 or off > 400:
                    break
                off += 50
                time.sleep(1.1)
            out.write(json.dumps({"day": day, "matches": [
                {"id": m["id"], "sched": m.get("scheduled_time"),
                 "tournament": m.get("tournament"), "round": m.get("round_code"),
                 "p1": (m.get("players") or {}).get("p1", {}).get("name"),
                 "p2": (m.get("players") or {}).get("p2", {}).get("name"),
                 "winner": m.get("winner")} for m in ms]}) + "\n")
            out.flush()
            if n % 10 == 0:
                print(f"  {n}/{len(days)} days", flush=True)
            time.sleep(1.1)


def pull_tapes():
    ids = {}
    for line in open(IDX):
        r = json.loads(line)
        for m in r["matches"]:
            ids[m["id"]] = (r["day"], m)
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    todo = [i for i in ids if i not in done]
    print(f"{len(ids)} WTA singles indexed, {len(done)} pulled, "
          f"{len(todo)} to go", flush=True)
    with open(OUT, "a") as out:
        for n, mid in enumerate(todo, 1):
            d = get(f"/history/matches/{mid}")
            if not d or "_http" in d or "_err" in d:
                time.sleep(1.1)
                continue
            day, meta_m = ids[mid]
            out.write(json.dumps({
                "id": mid, "day": day, "meta_kalshi_join": meta_m,
                "match": d.get("match"), "meta": d.get("meta"),
                "tape": d.get("tape")}) + "\n")
            out.flush()
            if n % 50 == 0:
                print(f"  {n}/{len(todo)} tapes", flush=True)
            time.sleep(1.1)
    print("TAPES DONE", flush=True)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("index", "all"):
        index_days()
    if cmd in ("tapes", "all"):
        pull_tapes()
