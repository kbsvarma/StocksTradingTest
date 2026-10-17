#!/usr/bin/env python3
"""Analysis A: full calibration surface for the 15M family.

For every (minutes-remaining, favorite-mid price bin): realized favorite win
frequency vs implied (mid), plus tradable net EV (worse-fill taker at ask,
exact fee). BH-FDR across cells. Pooled + per-asset, IS/OOS split.
IS = close_ts < 2026-07-07 00:00 UTC, OOS = after.
"""
import json, os, sys, math, calendar, time
from collections import defaultdict
import backtest as bt

OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def bin_of(p):
    # favorite mid in [0.5,1]: 2.5c bins to 0.90, 1c bins to 1.00
    if p < 0.90:
        lo = 0.50 + 0.025 * int((p - 0.50) / 0.025)
        return f"{lo:.3f}-{lo+0.025:.3f}"
    lo = 0.90 + 0.01 * int((p - 0.90) / 0.01)
    return f"{lo:.2f}-{lo+0.01:.2f}"

def cells_from(markets, series_filter=None):
    cells = defaultdict(lambda: {"n":0,"w":0,"sum_mid":0.0,"var":0.0,
                                 "ev_sum":0.0,"is_n":0,"is_ev":0.0,"oos_n":0,"oos_ev":0.0})
    for m in markets:
        if series_filter and m["series"] != series_filter:
            continue
        br = m["by_rem"]
        for rem in range(13, 0, -1):
            c = br.get(rem)
            if not c:
                continue
            q = bt.fav_quotes(c)
            if not q:
                continue
            side, fbid, fask, fmid = q
            if fask - fbid > 0.02 or fask > 0.995:  # sanity: exclude junk quotes
                continue
            # tradable entry: worse of close ask and next-minute open ask
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
            key = (rem, bin_of(fmid))
            cell = cells[key]
            cell["n"] += 1
            cell["w"] += win
            cell["sum_mid"] += fmid
            cell["var"] += fmid * (1 - fmid)
            cell["ev_sum"] += ev
            if m["close_ts"] < OOS_TS:
                cell["is_n"] += 1; cell["is_ev"] += ev
            else:
                cell["oos_n"] += 1; cell["oos_ev"] += ev
    return cells

def bh_fdr(pvals, q=0.10):
    idx = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m = len(pvals)
    passed = set()
    kmax = -1
    for rank, i in enumerate(idx, 1):
        if pvals[i] <= q * rank / m:
            kmax = rank
    for rank, i in enumerate(idx, 1):
        if rank <= kmax:
            passed.add(i)
    return passed

def analyze(cells, label, min_n=150):
    rows = []
    for (rem, b), c in cells.items():
        if c["n"] < min_n:
            continue
        wr = c["w"] / c["n"]
        mid = c["sum_mid"] / c["n"]
        z = (c["w"] - c["sum_mid"]) / math.sqrt(c["var"]) if c["var"] > 0 else 0
        mean_ev = c["ev_sum"] / c["n"] * 100
        p = 0.5 * math.erfc(z / math.sqrt(2))  # one-sided: WR > implied
        rows.append({"rem": rem, "bin": b, "n": c["n"], "wr": wr, "mid": mid,
                     "excess": (wr - mid) * 100, "z": z, "p": p, "ev_c": mean_ev,
                     "is_ev": c["is_ev"] / c["is_n"] * 100 if c["is_n"] else None,
                     "oos_ev": c["oos_ev"] / c["oos_n"] * 100 if c["oos_n"] else None,
                     "is_n": c["is_n"], "oos_n": c["oos_n"]})
    # FDR on cells with positive tradable EV
    pos = [r for r in rows if r["ev_c"] > 0]
    passed = bh_fdr([r["p"] for r in pos]) if pos else set()
    print(f"\n=== {label}: {len(rows)} cells (n>={min_n}), {len(pos)} with EV>0, "
          f"{len(passed)} pass BH-FDR10 ===")
    print(f"{'rem':>3} {'bin':>12} {'n':>6} {'mid':>6} {'WR':>6} {'exc':>6} {'z':>6} "
          f"{'EV¢':>6} {'IS¢':>6} {'OOS¢':>6} {'oosN':>5} FDR")
    show = sorted(rows, key=lambda r: -r["z"])
    for r in show[:40]:
        i = pos.index(r) if r in pos else None
        fdr = "PASS" if (i is not None and i in passed) else ""
        print(f"{r['rem']:>3} {r['bin']:>12} {r['n']:>6} {r['mid']:.3f} {r['wr']:.3f} "
              f"{r['excess']:+.2f} {r['z']:+.2f} {r['ev_c']:+.2f} "
              f"{(r['is_ev'] if r['is_ev'] is not None else float('nan')):+.2f} "
              f"{(r['oos_ev'] if r['oos_ev'] is not None else float('nan')):+.2f} "
              f"{r['oos_n']:>5} {fdr}")
    return rows, passed, pos

if __name__ == "__main__":
    markets = []
    for s in bt.SERIES:
        markets += bt.load(s)
    print(f"loaded {len(markets)} markets")
    cells = cells_from(markets)
    rows, passed, pos = analyze(cells, "POOLED 4-asset surface")
    # dump full csv
    with open("data/calib_surface_pooled.csv", "w") as f:
        f.write("rem,bin,n,mid,wr,excess_pts,z,p,ev_cents,is_ev,oos_ev,is_n,oos_n\n")
        for r in sorted(rows, key=lambda r: (r["rem"], r["bin"])):
            f.write(f"{r['rem']},{r['bin']},{r['n']},{r['mid']:.4f},{r['wr']:.4f},"
                    f"{r['excess']:.3f},{r['z']:.3f},{r['p']:.2e},{r['ev_c']:.3f},"
                    f"{r['is_ev']},{r['oos_ev']},{r['is_n']},{r['oos_n']}\n")
    print("\nwrote data/calib_surface_pooled.csv")
