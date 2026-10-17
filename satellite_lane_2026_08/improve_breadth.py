"""One declared improvement attempt + window neighbors: codex breadth cell
+ Q2-style multi-day RS qualification (benched sym must have positive W-bar
return AND lead the other benched sym). Target: era balance, not more total."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

exec(open("verify_breadth.py").read().split("def day_sat")[0])

RETW = {}
for wname, wbars in (("1d", 24), ("3d", 69), ("5d", 115)):
    RETW[wname] = {}
    for sym in bars:
        idx = bars[sym].index
        cols = {}
        for other in bars:
            r = bars[other]["close"].pct_change(wbars, fill_method=None)
            cols[other] = r if other == sym else r.reindex(idx, method="ffill")
        RETW[wname][sym] = pd.DataFrame(cols, index=idx)


def day_sat_q(sym, wname, close_hour=16, k=2.0, buffer_atr=0.25, eb=14):
    B = bars[sym]
    import strategy
    sig = strategy.signal_frame(B, rg.SCFG)
    atr = sig["atr"].values
    vol_ok = sig["vol_ok"].values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= 9) & (hours < close_hour)
    opens, highs, lows, closes = (B[c].values for c in
                                  ("open", "high", "low", "close"))
    rolls = B["roll"].values
    ts = B.index
    roll_dates = set(dates[rolls == 1])
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
    R = RETW[wname][sym] if wname else None
    trades = []
    pos = pend = 0
    e = hw = st = sa = np.nan
    e_ts = None
    cur = None
    shigh = -np.inf
    nbars = 0
    done = False
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            shigh, nbars, done = -np.inf, 0, False
            pend = pos = 0
        if not eligible[i]:
            continue
        if pend:
            pos, pend = 1, 0
            e = hw = opens[i]
            sa = atr[i - 1]
            st = e - k * sa
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos:
            if lows[i] <= st:
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, x - e, "stop"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i],
                               closes[i] - e, "eod"))
                pos = 0
            else:
                hw = max(hw, highs[i])
                st = max(st, hw - k * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + buffer_atr * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < eb):
            ok = True
            if R is not None:
                row = WROWS.get(monday_of_week(ts[i]))
                others = [s for s in bars if s != sym and int(row[s]) == 0]
                rr = R.loc[:ts[i]].iloc[-1]
                mine = rr[sym]
                ok = (np.isfinite(mine) and mine > 0
                      and all(mine >= rr[o] for o in others
                              if np.isfinite(rr[o])))
            if ok:
                t_entry = ts[i + 1]
                loc = EXPO.index.searchsorted(t_entry, side="right") - 1
                if loc >= 0 and EXPO.iloc[loc] > 0:
                    pend = 1
                    done = True
        shigh = max(shigh, highs[i])
        nbars += 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "points",
                                      "reason"])
    return T[T["entry_time"] >= rg.START]


for wname in (None, "1d", "3d", "5d"):
    S = pd.concat([day_sat_q(s, wname) for s in bars])
    S["pnl"] = S["points"] * S["sym"].map(rg.MULT) - rg.FR
    P = S[S["exit_time"] < "2026-01-01"]
    pnl = pd.Series(P["pnl"].values,
                    index=pd.DatetimeIndex(P["exit_time"])).sort_index()
    yr = pnl.groupby(pnl.index.year).sum()
    t22 = yr.loc[[y for y in (2022, 2023, 2024) if y in yr.index]].sum()
    neg = int((yr < 0).sum())
    y26 = float(S[S["exit_time"] >= "2026-01-01"]["pnl"].sum())
    tag = wname or "codex(none)"
    print(f"{tag:11} n {len(P):>3}  total ${pnl.sum():>6,.0f}  "
          f"halves {pnl[:'2017'].sum():>5,.0f}/{pnl['2018':].sum():>6,.0f}  "
          f"$7RT ${(P['pnl'] - 3.5).sum():>6,.0f}  "
          f"2022-24 {t22/pnl.sum()*100 if pnl.sum() else 0:>3.0f}%  "
          f"negYrs {neg}/15  2026 ${y26:>5,.0f}", flush=True)
