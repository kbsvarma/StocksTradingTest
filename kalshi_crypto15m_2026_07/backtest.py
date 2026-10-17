#!/usr/bin/env python3
"""Independent replication of the '15-min crypto continuation' claim.

Core lane:      at 7 min remaining, favorite ask in [0.95, 0.98], favorite for
                >=6 consecutive minute-closes, tight spread -> buy at next
                minute's executable ask.
Aggressive lane: at 6 min remaining, favorite ask in [0.90, 0.95).

Fees: taker fee = 0.07 * P * (1-P) per contract (verified vs real account fill,
      no cent rounding). Maker fee 0. Settlement fee 0.
"""
import json, os, sys, calendar, time, math
from collections import defaultdict

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M"]

def to_ts(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))

def f(x):
    return None if x is None else float(x)

def fee(p):
    return 0.07 * p * (1 - p)

def wilson(w, n, z=1.96):
    if n == 0:
        return (0, 0)
    ph = w / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    e = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (c - e, c + e)

def load(series):
    mkts = {}
    with open(os.path.join(DATA, f"markets_{series}.jsonl")) as fh:
        for line in fh:
            m = json.loads(line)
            if m.get("result") in ("yes", "no"):
                mkts[m["ticker"]] = m
    out = []
    with open(os.path.join(DATA, f"candles_{series}.jsonl")) as fh:
        for line in fh:
            r = json.loads(line)
            m = mkts.get(r["ticker"])
            if not m or not r["candles"]:
                continue
            close_ts = to_ts(m["close_time"])
            by_rem = {}
            for c in r["candles"]:
                rem = (close_ts - c["t"]) // 60
                by_rem[rem] = c
            out.append({"ticker": r["ticker"], "series": series,
                        "close_ts": close_ts, "result": m["result"],
                        "by_rem": by_rem})
    return out

def fav_quotes(c):
    """Return (side, fav_bid, fav_ask, mid) from a candle's closes."""
    bc, ac = f(c.get("bc")), f(c.get("ac"))
    if bc is None or ac is None:
        return None
    mid = (bc + ac) / 2
    if mid > 0.5:
        return ("yes", bc, ac, mid)
    return ("no", 1 - ac, 1 - bc, 1 - mid)

def fav_ask_open(c, side):
    bo, ao = f(c.get("bo")), f(c.get("ao"))
    if side == "yes":
        return ao
    return None if bo is None else 1 - bo

def signal(mkt, dec_rem, band_lo, band_hi, persist, max_spread, entry_mode):
    """Evaluate lane at decision candle rem=dec_rem. Returns trade dict or None."""
    br = mkt["by_rem"]
    dec = br.get(dec_rem)
    if not dec:
        return None
    q = fav_quotes(dec)
    if not q:
        return None
    side, fbid, fask, _ = q
    if not (band_lo <= fask <= band_hi):
        return None
    if fask - fbid > max_spread + 1e-9:
        return None
    # persistence: previous `persist` minute-closes (incl. decision minute) same favorite
    for k in range(persist):
        c = br.get(dec_rem + k)
        if not c:
            return None
        qq = fav_quotes(c)
        if not qq or qq[0] != side:
            return None
    # executable entry: ask at open of the NEXT minute (after signal seen)
    nxt = br.get(dec_rem - 1)
    if not nxt:
        return None
    nxt_ask = fav_ask_open(nxt, side)
    if nxt_ask is None:
        return None
    if entry_mode == "next_open":
        entry = nxt_ask
    else:  # conservative: worse of signal-close ask and next-minute-open ask
        entry = max(fask, nxt_ask)
    if entry > 0.99:
        return None
    win = (mkt["result"] == side)
    pnl = (1 - entry) - fee(entry) if win else -entry - fee(entry)
    return {"ticker": mkt["ticker"], "series": mkt["series"], "ts": mkt["close_ts"],
            "side": side, "entry": entry, "win": win, "pnl": pnl,
            "sig_ask": fask, "spread": fask - fbid}

def run(markets, lanes, max_spread=0.01, entry_mode="worse", persist=6,
        one_per_interval=False):
    trades = []
    for m in markets:
        t = None
        if "core" in lanes:
            t = signal(m, 7, 0.95, 0.98, persist, max_spread, entry_mode)
        if t is None and "aggr" in lanes:
            t = signal(m, 6, 0.90, 0.9499, persist, max_spread, entry_mode)
        if t:
            trades.append(t)
    trades.sort(key=lambda x: x["ts"])
    if one_per_interval:
        # max one trade per 15-min close timestamp, fixed priority BTC>ETH>SOL>XRP
        prio = {s: i for i, s in enumerate(SERIES)}
        by_iv = {}
        for t in trades:
            k = t["ts"]
            if k not in by_iv or prio[t["series"]] < prio[by_iv[k]["series"]]:
                by_iv[k] = t
        trades = sorted(by_iv.values(), key=lambda x: x["ts"])
    return trades

def report(name, trades, contracts=10):
    n = len(trades)
    if n == 0:
        print(f"{name}: 0 trades")
        return
    w = sum(t["win"] for t in trades)
    avg_entry = sum(t["entry"] for t in trades) / n
    pnl_c = [t["pnl"] * 100 for t in trades]           # cents per contract
    mean_pnl = sum(pnl_c) / n
    lo, hi = wilson(w, n)
    # equity curve at `contracts` contracts, max drawdown
    eq = peak = mdd = 0.0
    for t in trades:
        eq += t["pnl"] * contracts
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    days = (trades[-1]["ts"] - trades[0]["ts"]) / 86400
    total = sum(t["pnl"] for t in trades) * contracts
    # calibration: expected wins if market price were true prob
    exp_w = sum(t["entry"] for t in trades)
    print(f"{name}:")
    print(f"  n={n}  wins={w}  WR={w/n:.4f}  (95% CI {lo:.4f}-{hi:.4f})  avg_entry={avg_entry:.4f}")
    var = sum(t["entry"] * (1 - t["entry"]) for t in trades)
    z = (w - exp_w) / math.sqrt(var) if var > 0 else 0.0
    pval = 0.5 * math.erfc(z / math.sqrt(2))
    print(f"  edge_vs_price={(w/n - avg_entry)*100:+.2f} prob-pts  "
          f"expected_wins_if_calibrated={exp_w:.1f}  z={z:+.2f}  p(one-sided)={pval:.2e}")
    print(f"  mean_pnl={mean_pnl:+.3f} c/contract   total@{contracts}={total:+.2f} $  over {days:.1f} days"
          f"  (${total/max(days,1e-9):+.2f}/day)")
    print(f"  maxDD@{contracts}=${mdd:.2f}   trades/day={n/max(days,1e-9):.1f}")
    # chronological halves
    half = n // 2
    for tag, part in (("H1", trades[:half]), ("H2", trades[half:])):
        if part:
            pw = sum(t["win"] for t in part)
            pp = sum(t["pnl"] for t in part) / len(part) * 100
            print(f"  {tag}: n={len(part)} WR={pw/len(part):.4f} mean={pp:+.3f}c")
    # per series
    by_s = defaultdict(list)
    for t in trades:
        by_s[t["series"]].append(t)
    for s, part in sorted(by_s.items()):
        pw = sum(t["win"] for t in part)
        pp = sum(t["pnl"] for t in part) / len(part) * 100
        print(f"  {s}: n={len(part)} WR={pw/len(part):.4f} mean={pp:+.3f}c")
    # daily P&L distribution at `contracts`
    by_day = defaultdict(float)
    for t in trades:
        by_day[time.strftime("%Y-%m-%d", time.gmtime(t["ts"]))] += t["pnl"] * contracts
    dvals = sorted(by_day.values())
    ndays = len(dvals)
    npos = sum(1 for v in dvals if v > 0)
    print(f"  active days={ndays}  positive={npos} ({npos/ndays:.0%})  "
          f"worst_day=${dvals[0]:+.2f}  best_day=${dvals[-1]:+.2f}  "
          f"median_day=${dvals[ndays//2]:+.2f}")
    # weekly buckets
    by_wk = defaultdict(lambda: [0, 0, 0.0])
    for t in trades:
        wk = time.strftime("%G-W%V", time.gmtime(t["ts"]))
        by_wk[wk][0] += 1
        by_wk[wk][1] += t["win"]
        by_wk[wk][2] += t["pnl"] * contracts
    wk_str = "  weeks: " + "  ".join(
        f"{wk}[n={v[0]} WR={v[1]/v[0]:.3f} ${v[2]:+.1f}]" for wk, v in sorted(by_wk.items()))
    print(wk_str)
    losses = [t for t in trades if not t["win"]]
    if losses:
        print(f"  losses ({len(losses)}):")
        for t in losses[:40]:
            print(f"    {t['ticker']} side={t['side']} entry={t['entry']:.3f} "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(t['ts']))}Z")

if __name__ == "__main__":
    markets = []
    for s in SERIES:
        ms = load(s)
        print(f"loaded {s}: {len(ms)} markets with candles")
        markets += ms
    print()
    report("CORE (95-98 @ 7min, persist6, spread<=1c, worse-fill)",
           run(markets, ["core"]))
    print()
    report("AGGRESSIVE (90-95 @ 6min, persist6, spread<=1c, worse-fill)",
           run(markets, ["aggr"]))
    print()
    report("COMBINED all signals (core preferred per market)",
           run(markets, ["core", "aggr"]))
    print()
    report("COMBINED one-per-interval (BTC>ETH>SOL>XRP priority)",
           run(markets, ["core", "aggr"], one_per_interval=True))
    print()
    print("--- sensitivity ---")
    report("CORE next-open fill", run(markets, ["core"], entry_mode="next_open"))
    print()
    report("CORE spread<=0.5c", run(markets, ["core"], max_spread=0.005))
    print()
    report("CORE spread<=2c", run(markets, ["core"], max_spread=0.02))
    print()
    report("CORE no-persistence (any 95-98 fav @7min, spread<=1c)",
           run(markets, ["core"], persist=1))
