#!/usr/bin/env python3
"""Pre-registered hourly analog: KXBTCD liquid strikes, unbroken-favorite
continuation. Decision at minutes-remaining {20,15,10,7,5}: favorite mid in
[0.93,0.99), ask<=0.99, spread<=1c, zero flips over the observed last-30-min
closes (>=10 observed), worse-fill taker, exact fees.
Gate: pooled EV t>=2, OOS same-sign. One trade per market (first qualifying)."""
import json, math, calendar, time, os
import backtest as bt

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def to_ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

mkts = {}
for line in open(os.path.join(DATA, "markets_KXBTCD.jsonl")):
    m = json.loads(line)
    if m.get("result") in ("yes", "no"):
        mkts[m["ticker"]] = m

trades = []
n_loaded = 0
for line in open(os.path.join(DATA, "candles_KXBTCD.jsonl")):
    r = json.loads(line)
    m = mkts.get(r["ticker"])
    if not m or not r["candles"]:
        continue
    n_loaded += 1
    close_ts = to_ts(m["close_time"])
    by_rem = {}
    for c in r["candles"]:
        rem = (close_ts - c["t"]) // 60
        by_rem[rem] = c
    seq = {}
    for rem in range(30, 0, -1):
        c = by_rem.get(rem)
        if c:
            q = bt.fav_quotes(c)
            if q:
                seq[rem] = q
    for rem in (20, 15, 10, 7, 5):
        if rem not in seq:
            continue
        side, fbid, fask, fmid = seq[rem]
        if fask - fbid > 0.01 or not (0.93 <= fmid < 0.99):
            continue
        flips, obs, prev = 0, 0, None
        for r2 in range(30, rem - 1, -1):
            if r2 in seq:
                obs += 1
                if prev is not None and seq[r2][0] != prev:
                    flips += 1
                prev = seq[r2][0]
        if flips > 0 or obs < 10:
            continue
        nxt = by_rem.get(rem - 1)
        entry = fask
        if nxt is not None:
            na = bt.fav_ask_open(nxt, side)
            if na is not None:
                entry = max(entry, na)
        if entry > 0.99:
            continue
        win = (m["result"] == side)
        pnl = (1 - entry) - bt.fee(entry) if win else -entry - bt.fee(entry)
        trades.append({"ts": close_ts, "pnl": pnl, "win": win, "mid": fmid,
                       "rem": rem, "oos": close_ts >= OOS_TS})
        break

trades.sort(key=lambda t: t["ts"])
n = len(trades)
print(f"{n_loaded} liquid hourly markets with candles; {n} signals")
if n:
    w = sum(t["win"] for t in trades)
    mid = sum(t["mid"] for t in trades) / n
    var = sum(t["mid"] * (1 - t["mid"]) for t in trades)
    z = (w - sum(t["mid"] for t in trades)) / math.sqrt(var) if var else 0
    ev = sum(t["pnl"] for t in trades) / n * 100
    sd = (sum((t["pnl"] * 100 - ev) ** 2 for t in trades) / max(n - 1, 1)) ** 0.5
    ist = [t for t in trades if not t["oos"]]
    oot = [t for t in trades if t["oos"]]
    days = (trades[-1]["ts"] - trades[0]["ts"]) / 86400
    print(f"WR={w/n:.4f} vs mid={mid:.4f} z={z:+.2f} EV={ev:+.2f}c "
          f"t={ev/(sd/math.sqrt(n)):+.2f}")
    if ist:
        print(f"IS: n={len(ist)} EV={sum(t['pnl'] for t in ist)/len(ist)*100:+.2f}c")
    if oot:
        print(f"OOS: n={len(oot)} EV={sum(t['pnl'] for t in oot)/len(oot)*100:+.2f}c")
    print(f"{n/days:.1f} trades/day  ${sum(t['pnl'] for t in trades)*10/days:+.2f}/day @10ct")
    from collections import defaultdict
    b = defaultdict(list)
    for t in trades:
        b[t["rem"]].append(t)
    for k in sorted(b):
        rs = b[k]
        print(f"  rem={k}: n={len(rs)} WR={sum(t['win'] for t in rs)/len(rs):.4f} "
              f"EV={sum(t['pnl'] for t in rs)/len(rs)*100:+.2f}c")
