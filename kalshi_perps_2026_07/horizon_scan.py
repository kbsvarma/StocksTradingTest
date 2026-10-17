#!/usr/bin/env python3
"""Horizon scan: which design classes clear Kalshi perp fee economics?

For horizons 1m..24h, from 42d of 1-min perp mids:
  - typical |move| (median, p75) in bps  -> raw material available
  - sign autocorrelation (momentum/reversal signal strength)
  - gross harvest of naive momentum AND reversal rules per round trip
  - verdict vs fee walls: maker RT 10bps, taker RT 160bps
This closes the 'scalping and friends' question with numbers.
"""
import json, os, sys, statistics

P = os.path.dirname(os.path.abspath(__file__))
HORIZONS = [1, 5, 15, 60, 240, 480, 1440]        # minutes
MAKER_RT, TAKER_RT = 10.0, 160.0                 # bps

def load_mids(tkr):
    mids = {}
    with open(os.path.join(P, "data", f"perp_candles_{tkr}.jsonl")) as f:
        for line in f:
            rec = json.loads(line)
            for c in rec["c"]:
                b, a = c.get("b"), c.get("a")
                if b and a:
                    mids[c["t"]] = (float(b) + float(a)) / 2
    return dict(sorted(mids.items()))

def scan(tkr):
    mids = load_mids(tkr)
    ts = sorted(mids)
    print(f"\n=== {tkr}: {len(ts)} minutes of data ===")
    print(f"{'horizon':>8} {'med|mv|':>8} {'p75|mv|':>8} {'signAC':>7} "
          f"{'mom_gross':>10} {'rev_gross':>10}  verdict")
    for h in HORIZONS:
        step = h * 60
        rets = []
        pairs = []          # (previous-h return, next-h return)
        for t in ts:
            if t + step in mids and t - step in mids:
                r_next = (mids[t + step] - mids[t]) / mids[t] * 1e4
                r_prev = (mids[t] - mids[t - step]) / mids[t - step] * 1e4
                rets.append(abs(r_next))
                pairs.append((r_prev, r_next))
        if len(pairs) < 100:
            print(f"{h:>7}m  insufficient data")
            continue
        med = statistics.median(rets)
        p75 = sorted(rets)[int(len(rets) * 0.75)]
        same = sum(1 for a, b in pairs if a * b > 0)
        nz = sum(1 for a, b in pairs if a * b != 0)
        ac = same / nz if nz else 0.5
        # naive rules evaluated on non-overlapping samples
        mom = statistics.mean(b if a > 0 else -b for a, b in pairs[::h] if a != 0)
        rev = -mom
        best = max(mom, rev)
        wall = "clears MAKER wall" if best > MAKER_RT else "below all walls"
        if best > TAKER_RT:
            wall = "clears TAKER wall(!)"
        print(f"{h:>7}m {med:>7.1f} {p75:>8.1f} {ac:>7.3f} "
              f"{mom:>+10.2f} {rev:>+10.2f}  {wall}")

if __name__ == "__main__":
    for tkr in (sys.argv[1:] or ["KXBTCPERP", "KXETHPERP", "KXSOLPERP", "KXXRPPERP"]):
        try:
            scan(tkr)
        except FileNotFoundError:
            print(f"{tkr}: no data yet")
