#!/usr/bin/env python3
"""STAGE 2 of PREREG_LIVESCORE.md — what does Kalshi CHARGE for each state?

Joins score tapes to Kalshi price candles by player name + date, then for
each pre-registered state finds the first timestamped occurrence and reads
the market price at that minute. Edge = win rate - (price + fee).

OBSERVED tapes only (stage 1 proved reconstructed tapes are unusable).
"""
import json
import math
import os
import unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
import screen_states as S


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c for c in s.lower()
                            if c.isalnum() or c.isspace()).split())


def fee(p):
    return 0.07 * p * (1 - p)


def wilson_lo(k, n, z=1.96):
    if not n:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h)


# ---------- Kalshi side ----------
names = {}
for line in open(os.path.join(HERE, "data", "kalshi_wta_names.jsonl")):
    r = json.loads(line)
    key = frozenset({norm(r["name_yes"]), norm(r["name_no"])})
    names[key] = r

candles = {}
for line in open(os.path.join(HERE, "data", "games_KXWTAMATCH.jsonl")):
    r = json.loads(line)
    seq = []
    for c in r["candles"]:
        try:
            b = float(c["yes_bid"]["close_dollars"])
            a = float(c["yes_ask"]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= b <= a <= 1:
            seq.append((c["end_period_ts"], b, a))
    if seq:
        candles[r["ticker"]] = (sorted(seq), r["result"])


def price_at(ticker, ts):
    """(bid, ask) for the YES side of `ticker` at the candle covering ts."""
    d = candles.get(ticker)
    if not d:
        return None
    seq, _ = d
    best = None
    for t, b, a in seq:
        if t <= ts + 60:
            best = (t, b, a)
        else:
            break
    if not best or ts - best[0] > 300:      # no quote within 5 min
        return None
    return best[1], best[2]


# ---------- join + price the states ----------
rows = [json.loads(l)
        for l in open(os.path.join(HERE, "data", "tape_wta.jsonl"))]
obs = [r for r in rows
       if (r.get("meta") or {}).get("point_source") == "observed"
       and (r.get("match") or {}).get("winner") and (r.get("tape") or [])]

joined = 0
recs = {}
for r in obs:
    pl = r["match"]["players"]
    n1, n2 = norm(pl["p1"].get("name")), norm(pl["p2"].get("name"))
    km = names.get(frozenset({n1, n2}))
    if not km:
        continue
    # which Kalshi ticker is player1?
    t_p1 = (km["ticker_yes"] if norm(km["name_yes"]) == n1 else km["ticker_no"])
    t_p2 = (km["ticker_no"] if t_p1 == km["ticker_yes"] else km["ticker_yes"])
    if t_p1 not in candles:
        continue
    joined += 1
    winner = int(r["match"]["winner"]) - 1
    seen = set()
    for row in r["tape"]:
        ts_s = row.get("timestamp")
        if not ts_s:
            continue
        ts = int(datetime.fromisoformat(
            ts_s.replace("Z", "+00:00")).timestamp())
        for st, i in S.states_for(row).items():
            if st in seen:
                continue
            tk = t_p1 if i == 0 else t_p2
            q = price_at(tk, ts)
            if not q:
                continue
            seen.add(st)
            bid, ask = q                      # YES side of that player
            recs.setdefault(st, []).append(
                (ts, ask, i == winner, bid))

print(f"observed tapes: {len(obs)} | joined to Kalshi prices: {joined}\n")
print(f"{'state':<34} {'n':>4} {'won':>4} {'winrate':>8} {'avg ask':>8} "
      f"{'edge/ct':>8} {'net@ask':>8} {'LB edge':>8}")
for st in sorted(recs, key=lambda s: -len(recs[s])):
    v = recs[st]
    n = len(v)
    if n < 10:
        continue
    k = sum(1 for _, _, w, _ in v if w)
    wr = k / n
    avg = sum(a for _, a, _, _ in v) / n
    net = sum(((1 - a) - fee(a) if w else -a - fee(a))
              for _, a, w, _ in v) / n * 100
    lo = wilson_lo(k, n)
    print(f"{st:<34} {n:>4} {k:>4} {wr*100:>7.1f}% {avg*100:>7.1f}c "
          f"{(wr-avg)*100:>+7.1f} {net:>+7.2f}c {(lo-avg)*100:>+7.1f}")
