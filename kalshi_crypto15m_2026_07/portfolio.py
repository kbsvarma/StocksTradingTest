#!/usr/bin/env python3
"""Portfolio design: the 'unbroken favorite' book, pre-specified from
calib_surface + drift results.

Regions (first qualifying rem wins; max one trade per market; all require
ZERO favorite flips over observed closes since rem 14, >=5 observed closes,
spread <= 1c, tradable ask <= 0.99):
  R2 MID :  rem 12..8, fav_mid in [0.70, 0.90)
  R1 HIGH:  rem  8..4, fav_mid in [0.93, 0.99)
  R3 LATE:  rem  3..1, fav_mid >= 0.98, ask <= 0.995  (no next-candle needed at rem1)

Taker fills: worse(signal close ask, next-minute open ask).
Maker variant (same signals, R1/R2 only): join best bid at signal close;
filled iff any later candle's traded-price low <= bid; fee 0; unfilled = skip.
"""
import math, calendar, time
from collections import defaultdict
import backtest as bt

OOS_TS = calendar.timegm(time.strptime("2026-07-07T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))

def f(x):
    return None if x is None else float(x)

def observed_flips(seq, from_rem, to_rem):
    """count favorite flips among observed closes rem from_rem..to_rem (desc)."""
    flips, obs, prev = 0, 0, None
    for r in range(from_rem, to_rem - 1, -1):
        if r in seq:
            obs += 1
            if prev is not None and seq[r][0] != prev:
                flips += 1
            prev = seq[r][0]
    return flips, obs

def signals(markets):
    out = []
    for m in markets:
        br = m["by_rem"]
        seq = {}
        for rem in range(14, 0, -1):
            c = br.get(rem)
            if c:
                q = bt.fav_quotes(c)
                if q:
                    seq[rem] = q
        trade = None
        for rem in range(12, 0, -1):
            if rem not in seq:
                continue
            side, fbid, fask, fmid = seq[rem]
            if fask - fbid > 0.01:
                continue
            region = None
            if 8 <= rem <= 12 and 0.70 <= fmid < 0.90:
                region = "R2_MID"
            elif 4 <= rem <= 8 and 0.93 <= fmid < 0.99:
                region = "R1_HIGH"
            elif 1 <= rem <= 3 and fmid >= 0.98:
                region = "R3_LATE"
            if not region:
                continue
            flips, obs = observed_flips(seq, 14, rem)
            if flips > 0 or obs < 5:
                continue
            # taker entry
            nxt = br.get(rem - 1)
            entry = fask
            if nxt is not None:
                na = bt.fav_ask_open(nxt, side)
                if na is not None:
                    entry = max(entry, na)
            cap = 0.995 if region == "R3_LATE" else 0.99
            if entry > cap:
                continue
            win = (m["result"] == side)
            pnl = (1 - entry) - bt.fee(entry) if win else -entry - bt.fee(entry)
            # maker variant: join bid; filled iff any later candle traded <= bid
            mk = None
            if region in ("R1_HIGH", "R2_MID"):
                bid = fbid
                filled = False
                for r2 in range(rem - 1, -1, -1):
                    c2 = br.get(r2)
                    if not c2:
                        continue
                    # traded price low from favorite perspective
                    pl = None
                    if side == "yes":
                        pl = f(c2.get("pc"))  # only close available in compact; use bid low as proxy
                        bl = f(c2.get("bl"))
                        pl = bl if bl is not None else pl
                    else:
                        ah = f(c2.get("ah"))
                        pl = (1 - ah) if ah is not None else None
                    if pl is not None and pl <= bid:
                        filled = True
                        break
                if filled:
                    mk = (1 - bid) if win else -bid
            trade = {"ticker": m["ticker"], "series": m["series"], "ts": m["close_ts"],
                     "region": region, "rem": rem, "side": side, "mid": fmid,
                     "entry": entry, "win": win, "pnl": pnl, "maker_pnl": mk,
                     "oos": m["close_ts"] >= OOS_TS}
            break
        if trade:
            out.append(trade)
    out.sort(key=lambda t: t["ts"])
    return out

def stats(trades, contracts=10, label=""):
    n = len(trades)
    if not n:
        print(f"{label}: 0 trades")
        return
    w = sum(t["win"] for t in trades)
    days = (trades[-1]["ts"] - trades[0]["ts"]) / 86400 or 1
    ev = sum(t["pnl"] for t in trades) / n * 100
    tot = sum(t["pnl"] for t in trades) * contracts
    eq = pk = dd = 0.0
    for t in trades:
        eq += t["pnl"] * contracts
        pk = max(pk, eq)
        dd = min(dd, eq - pk)
    mid = sum(t["mid"] for t in trades) / n
    var = sum(t["mid"] * (1 - t["mid"]) for t in trades)
    z = (w - sum(t["mid"] for t in trades)) / math.sqrt(var) if var else 0
    is_t = [t for t in trades if not t["oos"]]
    oos_t = [t for t in trades if t["oos"]]
    is_ev = sum(t["pnl"] for t in is_t) / len(is_t) * 100 if is_t else float("nan")
    oos_ev = sum(t["pnl"] for t in oos_t) / len(oos_t) * 100 if oos_t else float("nan")
    by_day = defaultdict(float)
    for t in trades:
        by_day[int(t["ts"] // 86400)] += t["pnl"] * contracts
    dv = sorted(by_day.values())
    print(f"{label}: n={n} ({n/days:.1f}/day) WR={w/n:.4f} mid={mid:.3f} z={z:+.1f} "
          f"EV={ev:+.2f}c IS={is_ev:+.2f} OOS={oos_ev:+.2f} | ${tot:+.0f} "
          f"(${tot/days:+.2f}/d) maxDD=${dd:.0f} worstDay=${dv[0]:+.0f} "
          f"posDays={sum(1 for v in dv if v>0)}/{len(dv)}")

if __name__ == "__main__":
    markets = []
    for s in bt.SERIES:
        markets += bt.load(s)
    tr = signals(markets)
    print(f"total signals: {len(tr)}\n")
    print("== per region ==")
    for reg in ("R2_MID", "R1_HIGH", "R3_LATE"):
        stats([t for t in tr if t["region"] == reg], label=reg)
    print("\n== per asset (all regions) ==")
    for s in bt.SERIES:
        stats([t for t in tr if t["series"] == s], label=s)
    print("\n== COMBINED BOOK (all regions, all assets, 10 contracts) ==")
    stats(tr, label="ALL")
    print("\n== maker variant (R1+R2 signals that filled at bid, fee=0) ==")
    mk = [t for t in tr if t["maker_pnl"] is not None]
    if mk:
        n = len(mk)
        base = sum(1 for t in tr if t["region"] in ("R1_HIGH", "R2_MID"))
        w = sum(t["win"] for t in mk)
        ev = sum(t["maker_pnl"] for t in mk) / n * 100
        print(f"filled {n}/{base} ({n/base:.0%})  WR(filled)={w/n:.4f}  maker EV={ev:+.2f}c "
              f"vs taker EV on same filled set="
              f"{sum(t['pnl'] for t in mk)/n*100:+.2f}c")
    print("\n== R3_LATE at 100 contracts (scale test of the micro-edge) ==")
    stats([t for t in tr if t["region"] == "R3_LATE"], contracts=100, label="R3@100")
