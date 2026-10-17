#!/usr/bin/env python3
"""Early-exit study for R1_HIGH (pre-registered).

Baseline: hold to settlement (the live bot's behavior).
PROFIT-EXIT: after fill, at any minute-close with rem>=1, if favorite bid
  >= X, sell at that bid (taker, fee 0.07*p*(1-p)).
STOP-EXIT: if favorite bid <= Y, sell at that bid (taker fee).
Grid: X in {0.985, 0.99, 0.995}; Y in {0.90, 0.80, 0.50}; each alone + combos.
Exits evaluated from rem_entry-1 down to rem=1 (no final-minute exits).
IS = before 2026-07-07, OOS after. Verdict: variant must beat baseline EV
in BOTH halves to be considered.
"""
import math
import backtest as bt
import portfolio as pf

def build_trades():
    trades = []
    for s in bt.SERIES:
        for m in bt.load(s):
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
                # favorite-bid path at minute-closes after fill (rem-2 .. 1)
                path = []
                for r2 in range(rem - 2, 0, -1):
                    c2 = br.get(r2)
                    if not c2:
                        continue
                    q2 = bt.fav_quotes(c2)
                    if not q2:
                        continue
                    # bid for OUR side regardless of current favorite
                    bc, ac = float(c2["bc"]), float(c2["ac"])
                    our_bid = bc if side == "yes" else 1 - ac
                    path.append(our_bid)
                win = (m["result"] == side)
                trades.append({"ts": m["close_ts"], "entry": entry, "win": win,
                               "path": path, "oos": m["close_ts"] >= pf.OOS_TS})
                break
    trades.sort(key=lambda t: t["ts"])
    return trades

def pnl_hold(t):
    return (1 - t["entry"]) - bt.fee(t["entry"]) if t["win"] \
        else -t["entry"] - bt.fee(t["entry"])

def pnl_variant(t, X=None, Y=None):
    for b in t["path"]:
        if X is not None and b >= X:
            return (b - t["entry"]) - bt.fee(t["entry"]) - bt.fee(b), "profit_exit"
        if Y is not None and b <= Y:
            return (b - t["entry"]) - bt.fee(t["entry"]) - bt.fee(b), "stop_exit"
    return pnl_hold(t), "held"

def report(trades, X, Y, label):
    IS = [t for t in trades if not t["oos"]]; OOS = [t for t in trades if t["oos"]]
    out = []
    for part, name in ((IS, "IS"), (OOS, "OOS")):
        base = sum(pnl_hold(t) for t in part) / len(part)
        vs = [pnl_variant(t, X, Y) for t in part]
        ev = sum(v[0] for v in vs) / len(vs)
        exits = sum(1 for v in vs if v[1] != "held")
        out.append((name, base * 100, ev * 100, (ev - base) * 100,
                    exits / len(part)))
    both = all(o[3] > 0 for o in out)
    for name, b, e, d, x in out:
        print(f"  {label:>28} {name:>3}: hold={b:+.3f}c var={e:+.3f}c "
              f"delta={d:+.3f}c exit_rate={x:.1%}")
    print(f"  {'':>28} verdict: {'BEATS baseline both halves' if both else 'no'}")

if __name__ == "__main__":
    trades = build_trades()
    print(f"{len(trades)} R1 trades  "
          f"(baseline hold EV={sum(pnl_hold(t) for t in trades)/len(trades)*100:+.3f}c)\n")
    print("--- profit-exit only ---")
    for X in (0.985, 0.99, 0.995):
        report(trades, X, None, f"sell bid>={X}")
    print("\n--- stop-exit only (the 'close when losing' half) ---")
    for Y in (0.90, 0.80, 0.50):
        report(trades, None, Y, f"sell bid<={Y}")
    print("\n--- combos ---")
    for X in (0.99, 0.995):
        for Y in (0.80, 0.50):
            report(trades, X, Y, f"X={X} Y={Y}")
