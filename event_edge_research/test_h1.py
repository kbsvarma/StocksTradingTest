"""H1 — Pre-FOMC announcement drift (Lucca & Moench 2015).
Trade proxy: long 1 micro from the prior session's anchor time to just before the
decision-day announcement. Pre-registered exit 13:55 ET; robustness exit 12:25 ET
(avoids 2011-2012 press-conference 12:30 ET releases). Net of micro friction.
"""
import numpy as np, pandas as pd
from sessions import get_minute, build_sessions
from fomc_dates import decision_dates
from stats_tools import (split_is_oos, nw_tstat, worst_year_drop_tstat,
                         summarize_dollar, fmt, MES_MULT, MES_FRICTION_RT,
                         MNQ_MULT, MNQ_FRICTION_RT, ann_sharpe)

ET = "America/New_York"

def price_at(daymap, etdate, hh, mm):
    """Close of last 1-min bar at/before hh:mm ET on etdate. Returns (price, iid) or None."""
    day = daymap.get(pd.Timestamp(etdate).normalize())
    if day is None or len(day) == 0:
        return None
    tmin = day.index.hour * 60 + day.index.minute
    sel = day[tmin <= hh * 60 + mm]
    if len(sel) == 0:
        return None
    row = sel.iloc[-1]
    return float(row["close"]), int(row["instrument_id"])

def build_daymap(m):
    m = m.copy()
    key = m.index.normalize()
    return {d: g for d, g in m.groupby(key)}

def run_h1(tag, mult, friction, exit_hh, exit_mm, anchor_hh=14, anchor_mm=0):
    m = get_minute(tag)
    daymap = build_daymap(m)
    sess = build_sessions(tag)
    sdates = list(sess.index.normalize())
    sset = pd.DatetimeIndex(sdates)

    rows = []
    for D in decision_dates():
        Dt = pd.Timestamp(D).tz_localize(ET).normalize()
        # prior trading session = last session strictly before D
        prior = sset[sset < Dt]
        if len(prior) == 0:
            continue
        P = prior[-1]
        entry = price_at(daymap, P, anchor_hh, anchor_mm)
        exit_ = price_at(daymap, Dt, exit_hh, exit_mm)
        if entry is None or exit_ is None:
            continue
        ep, eiid = entry
        xp, xiid = exit_
        roll = eiid != xiid  # contract changed between prior day and decision day
        ret = xp / ep - 1.0
        pnl = ret * ep * mult - friction
        rows.append({"date": Dt, "ret": ret, "pnl": pnl, "roll": roll,
                     "entry": ep, "exit": xp})
    df = pd.DataFrame(rows).set_index("date").sort_index()
    df = df[~df["roll"]]  # exclude roll-spanning windows
    return df

def report(tag, df, label):
    ser = df["pnl"]
    is_p, oos_p = split_is_oos(ser)
    print(f"\n=== {tag}  {label}  (n={len(ser)}, excl rolls) ===")
    print("  ALL " + fmt(summarize_dollar(ser, "2010-2026")))
    print("  IS  " + fmt(summarize_dollar(is_p, "2010-2020")))
    print("  OOS " + fmt(summarize_dollar(oos_p, "2021-2026")))
    if len(is_p.dropna()) > 12:
        tw, wy, wm = worst_year_drop_tstat(is_p.dropna(), lags=3)
        print(f"  IS drop-worst-year ({wy}, mean=${wm:+.2f}): t={tw:+.2f}")
    # benchmark: unconditional same-clock 24h window return, all sessions
    return ser

def benchmark_unconditional(tag, mult, friction, exit_hh, exit_mm, anchor_hh=14, anchor_mm=0):
    """Mean net P&L of the SAME clock window (prior-day 14:00 -> next-day exit) on
    ALL trading days, as the unconditional baseline the FOMC window must beat."""
    m = get_minute(tag)
    daymap = build_daymap(m)
    sess = build_sessions(tag)
    sset = pd.DatetimeIndex(list(sess.index.normalize()))
    pnls = []
    for i in range(1, len(sset)):
        P, Dt = sset[i-1], sset[i]
        entry = price_at(daymap, P, anchor_hh, anchor_mm)
        exit_ = price_at(daymap, Dt, exit_hh, exit_mm)
        if entry is None or exit_ is None:
            continue
        ep, eiid = entry; xp, xiid = exit_
        if eiid != xiid:
            continue
        pnls.append(ret := xp/ep - 1.0)
    arr = np.array(pnls)
    return arr.mean(), arr.std(), len(arr)

if __name__ == "__main__":
    for tag, mult, fr in [("ES", MES_MULT, MES_FRICTION_RT), ("NQ", MNQ_MULT, MNQ_FRICTION_RT)]:
        print("\n" + "#"*72 + f"\n# H1 PRE-FOMC DRIFT — {tag}\n" + "#"*72)
        # Pre-registered: exit 13:55 ET
        df = run_h1(tag, mult, fr, 13, 55)
        report(tag, df, "exit 13:55 ET (pre-registered)")
        # Robustness: exit 12:25 ET (no press-conf look-ahead)
        df2 = run_h1(tag, mult, fr, 12, 25)
        report(tag, df2, "exit 12:25 ET (look-ahead-safe)")
        # unconditional benchmark (same clock, all days), gross bps
        mu, sd, n = benchmark_unconditional(tag, mult, fr, 13, 55)
        print(f"  [benchmark] unconditional same-window mean = {mu*1e4:+.2f} bps over {n} days")
