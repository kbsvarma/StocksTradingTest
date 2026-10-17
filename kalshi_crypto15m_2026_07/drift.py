#!/usr/bin/env python3
"""Analysis B: conditional calibration / martingale violations, 15M family.

Conditions the favorite's excess-win-rate on path features observable at
decision time (tradable next minute):
  - persistence: consecutive minute-closes as favorite (1-2, 3-5, 6+)
  - flip recency: favorite changed within last 2 minutes
  - jump: |Δ fav mid| over last minute (>=5c) — continuation vs reversal
  - whipsaw count: number of favorite flips seen so far in the interval
  - drift direction: fav mid rising vs falling over last 2 minutes
Each bucket: n, implied, realized, excess, z, tradable EV (worse-fill, fees).
Decision minutes pooled rem 4..12, split into high band (>=0.93) and
mid band (0.70-0.93) to keep interpretation clean. IS/OOS split reported.
"""
import math, calendar, time
from collections import defaultdict
import backtest as bt

OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def collect(markets):
    rows = []
    for m in markets:
        br = m["by_rem"]
        # build per-minute favorite series from rem 14 down to 1
        seq = {}
        for rem in range(14, 0, -1):
            c = br.get(rem)
            if not c:
                continue
            q = bt.fav_quotes(c)
            if q:
                seq[rem] = q  # (side, fbid, fask, fmid)
        for rem in range(12, 3, -1):
            if rem not in seq:
                continue
            side, fbid, fask, fmid = seq[rem]
            if fask - fbid > 0.02 or fask > 0.995:
                continue
            # persistence
            persist = 0
            r = rem
            while r in seq and seq[r][0] == side:
                persist += 1
                r += 1
            # flips so far and flip recency
            flips = 0
            prev = None
            for r2 in range(14, rem - 1, -1):
                if r2 in seq:
                    if prev is not None and seq[r2][0] != prev:
                        flips += 1
                    prev = seq[r2][0]
            flipped_recent = persist <= 2 and flips >= 1
            # last-minute jump in fav mid (from this side's perspective)
            jump = None
            if rem + 1 in seq:
                pm = seq[rem + 1][3] if seq[rem + 1][0] == side else 1 - seq[rem + 1][3]
                jump = fmid - pm
            # drift over 2 minutes
            drift2 = None
            if rem + 2 in seq:
                pm2 = seq[rem + 2][3] if seq[rem + 2][0] == side else 1 - seq[rem + 2][3]
                drift2 = fmid - pm2
            # tradable entry
            nxt = br.get(rem - 1)
            entry = fask
            if nxt is not None:
                na = bt.fav_ask_open(nxt, side)
                if na is not None:
                    entry = max(entry, na)
            if entry > 0.995:
                continue
            win = (m["result"] == side)
            ev = (1 - entry) - bt.fee(entry) if win else -entry - bt.fee(entry)
            rows.append({"rem": rem, "band": "high" if fmid >= 0.93 else
                         ("mid" if fmid >= 0.70 else "low"),
                         "mid": fmid, "win": win, "ev": ev,
                         "persist": persist, "flips": flips,
                         "flipped_recent": flipped_recent,
                         "jump": jump, "drift2": drift2,
                         "oos": m["close_ts"] >= OOS_TS})
    return rows

def bucket_report(rows, keyfn, label):
    b = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is not None:
            b[k].append(r)
    print(f"\n--- {label} ---")
    print(f"{'bucket':>18} {'n':>6} {'mid':>6} {'WR':>6} {'exc':>7} {'z':>6} "
          f"{'EV¢':>6} {'IS¢':>6} {'OOS¢':>7}")
    for k in sorted(b):
        rs = b[k]
        n = len(rs)
        if n < 150:
            continue
        w = sum(r["win"] for r in rs)
        mid = sum(r["mid"] for r in rs) / n
        var = sum(r["mid"] * (1 - r["mid"]) for r in rs)
        z = (w - sum(r["mid"] for r in rs)) / math.sqrt(var) if var else 0
        ev = sum(r["ev"] for r in rs) / n * 100
        is_rs = [r for r in rs if not r["oos"]]; oos_rs = [r for r in rs if r["oos"]]
        is_ev = sum(r["ev"] for r in is_rs) / len(is_rs) * 100 if is_rs else float("nan")
        oos_ev = sum(r["ev"] for r in oos_rs) / len(oos_rs) * 100 if oos_rs else float("nan")
        print(f"{str(k):>18} {n:>6} {mid:.3f} {w/n:.3f} {(w/n-mid)*100:+.2f} "
              f"{z:+.2f} {ev:+.2f} {is_ev:+.2f} {oos_ev:+.2f}")

if __name__ == "__main__":
    markets = []
    for s in bt.SERIES:
        markets += bt.load(s)
    rows = collect(markets)
    print(f"{len(rows)} decision-point observations (rem 4-12)")
    for band in ("high", "mid"):
        sub = [r for r in rows if r["band"] == band]
        print(f"\n================ BAND {band} ({len(sub)} obs) ================")
        bucket_report(sub, lambda r: ("1-2" if r["persist"] <= 2 else
                                      "3-5" if r["persist"] <= 5 else "6+"),
                      "persistence (consecutive mins as favorite)")
        bucket_report(sub, lambda r: r["flipped_recent"], "flipped within ~2 min")
        bucket_report(sub, lambda r: ("none" if r["flips"] == 0 else
                                      "1-2" if r["flips"] <= 2 else "3+"),
                      "whipsaw count so far")
        bucket_report(sub, lambda r: (None if r["jump"] is None else
                                      "+5c+" if r["jump"] >= 0.05 else
                                      "-5c+" if r["jump"] <= -0.05 else "small"),
                      "last-minute fav-mid jump")
        bucket_report(sub, lambda r: (None if r["drift2"] is None else
                                      "rising" if r["drift2"] > 0.01 else
                                      "falling" if r["drift2"] < -0.01 else "flat"),
                      "2-min drift direction")
