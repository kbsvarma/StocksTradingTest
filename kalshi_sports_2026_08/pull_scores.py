#!/usr/bin/env python3
"""Steps 2-4 of the live-score study (PREREG_LIVESCORE.md).

  probe : verify auth + discover the real response shapes (run first)
  index : list completed matches per archive date -> data/score_index.jsonl
  join  : fuzzy-match Kalshi names to feed matches -> data/score_join.jsonl
  pbp   : pull point-by-point for joined matches -> data/score_pbp.jsonl

Key: env LIVETENNIS_KEY, or ~/.livetennis_key (mode 600).
Docs were read, not exercised — probe adapts before we trust anything.
"""
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://api.livetennisapi.com/api/public/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def key():
    k = os.environ.get("LIVETENNIS_KEY")
    if k:
        return k.strip()
    p = os.path.expanduser("~/.livetennis_key")
    if os.path.exists(p):
        return open(p).read().strip()
    sys.exit("no API key: set LIVETENNIS_KEY or write ~/.livetennis_key")


def get(path, params=None, tries=5):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "X-API-Key": key(), "Authorization": f"Bearer {key()}",
                "User-Agent": "research/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode("utf-8", "replace")
            if e.code == 429:
                time.sleep(3.0 * (i + 1))
                continue
            return {"_http": e.code, "_body": body, "_url": url}
        except Exception as e:
            if i == tries - 1:
                return {"_err": str(e)[:150], "_url": url}
            time.sleep(1.5 * (i + 1))
    return {"_err": "retries exhausted"}


def norm(s):
    """Fold accents, lowercase, drop punctuation — for name matching."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c for c in s.lower()
                            if c.isalnum() or c.isspace()).split())


def name_keys(full):
    """Keys a feed might use: 'iga swiatek', 'swiatek i', 'swiatek'."""
    n = norm(full)
    parts = n.split()
    out = {n}
    if len(parts) >= 2:
        out.add(f"{parts[-1]} {parts[0][0]}")
        out.add(parts[-1])
        out.add(f"{parts[0]} {parts[-1]}")
    return out


def archive_dates():
    ds = set()
    with open(os.path.join(DATA, "kalshi_wta_names.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            ts = r.get("close_ts")
            if ts:
                d = datetime.fromtimestamp(ts, timezone.utc)
                for off in (-1, 0):
                    ds.add((d + timedelta(days=off)).strftime("%Y-%m-%d"))
    return sorted(ds)


def cmd_probe():
    print("== auth / shape probe ==")
    for label, path, params in (
            ("live matches", "/matches", {"status": "live"}),
            ("history list", "/history/matches", {"date": "2026-07-15"}),
            ("history list alt", "/history/matches",
             {"start_date": "2026-07-15", "end_date": "2026-07-15"}),
            ("rankings", "/rankings", None)):
        d = get(path, params)
        s = json.dumps(d)[:400] if not isinstance(d, dict) or "_http" not in d \
            else f"HTTP {d['_http']}: {d['_body'][:160]}"
        print(f"\n-- {label}  ({path} {params})\n   {s}")


def cmd_index():
    out_p = os.path.join(DATA, "score_index.jsonl")
    done = set()
    if os.path.exists(out_p):
        for line in open(out_p):
            try:
                done.add(json.loads(line)["_date"])
            except Exception:
                pass
    dates = [d for d in archive_dates() if d not in done]
    print(f"{len(dates)} dates to index", flush=True)
    with open(out_p, "a") as out:
        for n, d in enumerate(dates, 1):
            r = get("/history/matches", {"date": d, "tour": "WTA"})
            out.write(json.dumps({"_date": d, "resp": r}) + "\n")
            out.flush()
            if n % 10 == 0:
                print(f"  {n}/{len(dates)}", flush=True)
            time.sleep(1.1)          # 60/min tier
    print("INDEX DONE", flush=True)


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    {"probe": cmd_probe, "index": cmd_index}.get(cmd, cmd_probe)()
