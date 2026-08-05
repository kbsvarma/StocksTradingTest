#!/usr/bin/env python3
"""Step 1 of the live-score study (PREREG_LIVESCORE.md): resolve full
player names / tournament / round for every WTA match in the archive.

Kalshi tickers carry only 3-letter codes; the join to a score feed needs
real names. Settled-market metadata is retained ~70d and the archive is
~55d, so this should resolve nearly everything. Resume-safe.

Output: data/kalshi_wta_names.jsonl
  {event, ticker_yes, name_yes, name_no, tournament, round, close_ts}
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "kalshi_wta_names.jsonl")


def get(u, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "research/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (i + 1))
                continue
            if e.code == 404:
                return None
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    return None


def parse_meta(m):
    """Pull tournament + round out of the rules text.
    e.g. '...in the 2026 WTA Toronto Round Of 32 match...'"""
    txt = m.get("rules_primary") or ""
    tour = rnd = ""
    mt = re.search(r"(20\d\d)\s+(WTA|ATP)\s+([A-Za-z .'-]+?)\s+"
                   r"(Round Of \d+|Quarterfinal\w*|Semifinal\w*|Final\w*)",
                   txt, re.I)
    if mt:
        tour = f"{mt.group(1)} {mt.group(2)} {mt.group(3)}".strip()
        rnd = mt.group(4)
    else:
        mt2 = re.search(r"(20\d\d)\s+(WTA|ATP)\s+([A-Za-z .'-]+?)\s+match", txt, re.I)
        if mt2:
            tour = f"{mt2.group(1)} {mt2.group(2)} {mt2.group(3)}".strip()
    return tour, rnd


if __name__ == "__main__":
    events = {}
    with open(os.path.join(HERE, "data", "games_KXWTAMATCH.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            events.setdefault(r["ticker"].rsplit("-", 1)[0], []).append(r)
    done = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["event"])
                except Exception:
                    pass
    todo = [e for e in events if e not in done]
    print(f"{len(events)} archived events, {len(done)} resolved, "
          f"{len(todo)} to fetch", flush=True)
    out = open(OUT, "a")
    ok = miss = 0
    for n, ev in enumerate(todo, 1):
        mkts = events[ev]
        names = {}
        tour = rnd = ""
        close_ts = mkts[0].get("close_ts")
        for r in mkts:
            d = get(f"{BASE}/markets/{r['ticker']}")
            m = (d or {}).get("market") or {}
            sub = m.get("yes_sub_title") or ""
            if sub:
                names[r["ticker"]] = sub
                if not tour:
                    tour, rnd = parse_meta(m)
            time.sleep(0.12)
        if len(names) >= 2:
            ts = sorted(names)
            out.write(json.dumps({
                "event": ev, "ticker_yes": ts[0], "name_yes": names[ts[0]],
                "ticker_no": ts[1], "name_no": names[ts[1]],
                "tournament": tour, "round": rnd, "close_ts": close_ts}) + "\n")
            out.flush()
            ok += 1
        else:
            miss += 1
        if n % 50 == 0:
            print(f"  {n}/{len(todo)} — resolved {ok}, unresolved {miss}",
                  flush=True)
    out.close()
    print(f"DONE — resolved {ok}, unresolved {miss}", flush=True)
