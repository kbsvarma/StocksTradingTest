"""Bearish satellite mirror per PREREG_BEARSAT.md."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

src = open(Path(__file__).parent / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])     # loads M1, CORE, EXPO, HFRAME...

STOP_EXITS = pd.DatetimeIndex(
    CORE[CORE["reason"] == "stop"]["exit_time"]).sort_values()


def confirmed(kind, t):
    if kind == "C4":
        return True
    loc = EXPO.index.searchsorted(t, side="right") - 1
    expo = EXPO.iloc[loc] if loc >= 0 else 0
    if kind == "C1":
        return expo > 0
    if kind == "C2":
        return expo == 0
    j = STOP_EXITS.searchsorted(t)
    return j > 0 and (t - STOP_EXITS[j - 1]) <= pd.Timedelta(hours=24)


def bear_sat(sym, kind):
    B = native(sym, "15min")
    hourly = HFRAME[sym]
    vol_ok = hourly["vol_ok"].reindex(B.index, method="ffill"
                                      ).fillna(False).values
    atr = hourly["atr"].reindex(B.index, method="ffill").values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= 9) & (hours < 16)
    opens, highs, lows, closes = (B[c].values for c in
                                  ("open", "high", "low", "close"))
    rolls = B["roll"].values
    ts = B.index
    roll_dates = set(dates[rolls == 1])
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
    trades = []
    pos = pend = 0
    e = lw = st = np.nan
    e_ts = None
    cur = None
    slow, nbars, done = np.inf, 0, False
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            slow, nbars, done = np.inf, 0, False
            pend = pos = 0
        if not eligible[i]:
            continue
        if pend:
            pos, pend = 1, 0
            e = lw = opens[i]
            st = e + 2.0 * atr[i - 1]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos:
            if highs[i] >= st:
                x = max(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "stop"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i], "eod"))
                pos = 0
            else:
                lw = min(lw, lows[i])
                st = min(st, lw + 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] < slow - 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < 15
                and confirmed(kind, ts[i + 1])):
            pend = 1
            done = True
        slow = min(slow, lows[i])
        nbars += 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "reason"])
    T["pnl"] = (T["entry_px"] - T["exit_px"]) * T["sym"].map(rg.MULT) - rg.FR
    return T[T["entry_time"] >= rg.START]


for kind in ("C1", "C2", "C3", "C4"):
    S = pd.concat([bear_sat(s, kind) for s in M1])
    S25 = S[S["exit_time"] < "2026-01-01"]
    sp = pd.Series(S25["pnl"].values,
                   index=pd.DatetimeIndex(S25["exit_time"])).sort_index()
    cdd = daily_mtm_dd(pd.concat([base_t, S25.assign(qty=1)]))
    cp = pd.concat([bp, sp]).sort_index()
    cyr = cp.groupby(cp.index.year).sum()
    tot = float(cp.sum())
    gate = (float(sp.sum()) > 0 and tot / abs(cdd) >= 8.05
            and float(cyr.min()) >= -2228)
    yr22 = float(sp[sp.index.year == 2022].sum()) if len(sp) else 0
    print(f"{kind} sat {float(sp.sum()):>7,.0f} n{len(S25):>5} "
          f"ratio {tot/abs(cdd):>5.2f} worst {float(cyr.min()):>7,.0f} "
          f"halves {float(sp[:'2017'].sum()):>6,.0f}/"
          f"{float(sp['2018':].sum()):>6,.0f} 2022 {yr22:>6,.0f} "
          f"{'PASS' if gate else ''}", flush=True)
print("DONE")
