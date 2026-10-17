#!/usr/bin/env python3
"""Cross-product loss-filter study (pre-registered).

Question: does the perp mark (same CF index as event settlement) carry
information at 15M-entry time that predicts the late-reversal losses?

Features at the R1_HIGH decision minute (from perp 1-min mid closes):
  z      = signed margin of favorite vs strike, in units of 1-min perp vol
           scaled by sqrt(minutes remaining)  ("time-to-live z-score")
  drift3 = 3-min change in signed margin (favorite gaining or losing ground)

Pre-registered filter selection: choose z* on IS (first 28d of overlap) only,
from the grid z* in {1.5, 2, 3, 4}, maximizing IS EV subject to dropping
<=20% of signals. Report OOS (last 14d) with that single frozen z*.
"""
import json, os, sys, math, calendar, time, statistics

R = "/Users/varmakammili/Documents/GitHub/StocksTradingTest/kalshi_crypto15m_2026_07"
P = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, R)
os.chdir(R)
import backtest as bt
import portfolio as pf

PERP = {"KXBTC15M": "KXBTCPERP", "KXETH15M": "KXETHPERP",
        "KXSOL15M": "KXSOLPERP", "KXXRP15M": "KXXRPPERP"}

def load_perp(tkr):
    mids = {}
    with open(os.path.join(P, "data", f"perp_candles_{tkr}.jsonl")) as f:
        for line in f:
            rec = json.loads(line)
            for c in rec["c"]:
                b, a = c.get("b"), c.get("a")
                if b and a:
                    mids[c["t"]] = (float(b) + float(a)) / 2
    return mids

def perp_mid_at(mids, t, tol=180):
    for dt in range(0, tol + 60, 60):
        if t - dt in mids:
            return mids[t - dt]
    return None

def main():
    # strikes
    strikes = {}
    for s in PERP:
        for line in open(os.path.join(R, "data", f"markets_{s}.jsonl")):
            m = json.loads(line)
            if m.get("floor_strike"):
                strikes[m["ticker"]] = float(m["floor_strike"])

    rows = []
    for s, ptkr in PERP.items():
        mids = load_perp(ptkr)
        if not mids:
            print(f"{s}: no perp data yet"); continue
        tmin, tmax = min(mids), max(mids)
        markets = bt.load(s)
        # R1-only signals (deployed config)
        for m in markets:
            strike = strikes.get(m["ticker"])
            if not strike or not (tmin < m["close_ts"] - 900 and m["close_ts"] < tmax):
                continue
            br = m["by_rem"]; seq = {}
            for rem in range(14, 0, -1):
                c = br.get(rem)
                if c:
                    q = bt.fav_quotes(c)
                    if q:
                        seq[rem] = q
            for rem in range(8, 3, -1):
                if rem not in seq:
                    continue
                side, fbid, fask, fmid = seq[rem]
                if fask - fbid > 0.01 or not (0.93 <= fmid < 0.99) or fask > 0.99:
                    continue
                fl, obs = pf.observed_flips(seq, 14, rem)
                if fl > 0 or obs < 5:
                    continue
                nxt = br.get(rem - 1); entry = fask
                if nxt is not None:
                    na = bt.fav_ask_open(nxt, side)
                    if na is not None:
                        entry = max(entry, na)
                if entry > 0.99:
                    continue
                # perp features at decision minute close
                tdec = m["close_ts"] - rem * 60
                pm = perp_mid_at(mids, tdec)
                pm3 = perp_mid_at(mids, tdec - 180)
                if pm is None or pm3 is None:
                    break
                # scale: infer index units via strike/perp ratio (power of 10)
                k = 10 ** round(math.log10(strike / pm))
                idx, idx3 = pm * k, pm3 * k
                sgn = 1 if side == "yes" else -1
                margin = sgn * (idx - strike) / strike
                drift3 = sgn * (idx - idx3) / strike
                # 1-min vol over last 15 min of perp
                vals = [mids[t] for t in range(tdec - 900, tdec + 60, 60) if t in mids]
                rets = [abs(b - a) / a for a, b in zip(vals, vals[1:])]
                sig1 = statistics.median(rets) * 1.4826 if len(rets) >= 5 else None
                if not sig1 or sig1 <= 0:
                    break
                z = margin / (sig1 * math.sqrt(rem))
                win = (m["result"] == side)
                pnl = (1 - entry) - bt.fee(entry) if win else -entry - bt.fee(entry)
                rows.append({"ts": m["close_ts"], "series": s, "win": win,
                             "pnl": pnl, "z": z, "drift3": drift3 / sig1,
                             "entry": entry})
                break

    rows.sort(key=lambda r: r["ts"])
    n = len(rows)
    if n < 200:
        print(f"only {n} aligned signals — insufficient"); return
    losses = [r for r in rows if not r["win"]]
    print(f"aligned signals: {n}  losses: {len(losses)}  "
          f"span {time.strftime('%m-%d', time.gmtime(rows[0]['ts']))} → "
          f"{time.strftime('%m-%d', time.gmtime(rows[-1]['ts']))}")

    # diagnostic: z and drift distributions for winners vs losers
    for name, key in (("z", "z"), ("drift3/sig", "drift3")):
        wv = sorted(r[key] for r in rows if r["win"])
        lv = sorted(r[key] for r in rows if not r["win"])
        med = lambda v: v[len(v)//2] if v else float("nan")
        print(f"{name}: median win={med(wv):+.2f}  median LOSS={med(lv):+.2f}  "
              f"p25 loss={lv[len(lv)//4] if lv else float('nan'):+.2f}")

    # pre-registered filter: keep signal iff z >= z* (grid chosen on IS only)
    split = rows[0]["ts"] + 28 * 86400
    IS = [r for r in rows if r["ts"] < split]; OOS = [r for r in rows if r["ts"] >= split]
    best = None
    for zstar in (1.5, 2.0, 3.0, 4.0):
        keep = [r for r in IS if r["z"] >= zstar]
        if len(keep) < 0.8 * len(IS):
            continue
        ev = sum(r["pnl"] for r in keep) / len(keep)
        if best is None or ev > best[1]:
            best = (zstar, ev, len(keep))
    base_is = sum(r["pnl"] for r in IS) / len(IS)
    print(f"\nIS baseline EV={base_is*100:+.3f}c (n={len(IS)})")
    if not best:
        print("no z* kept >=80% of signals — filter fails pre-registration"); return
    zstar = best[0]
    print(f"chosen z*={zstar}: IS EV={best[1]*100:+.3f}c (n={best[2]}, "
          f"dropped {1-best[2]/len(IS):.1%})")
    keep_o = [r for r in OOS if r["z"] >= zstar]
    drop_o = [r for r in OOS if r["z"] < zstar]
    base_o = sum(r["pnl"] for r in OOS) / len(OOS)
    ev_o = sum(r["pnl"] for r in keep_o) / len(keep_o) if keep_o else float("nan")
    lc = sum(1 for r in drop_o if not r["win"])
    print(f"OOS: baseline EV={base_o*100:+.3f}c (n={len(OOS)}) → filtered "
          f"EV={ev_o*100:+.3f}c (n={len(keep_o)}, dropped {len(drop_o)}, "
          f"caught {lc}/{sum(1 for r in OOS if not r['win'])} losses)")

if __name__ == "__main__":
    main()
