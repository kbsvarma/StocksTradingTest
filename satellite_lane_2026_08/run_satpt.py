"""Profit-target + multi-trade satellite grid per PREREG_SATPT.md."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

src = open(Path(__file__).parent / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])


def pt_sat(sym, pt=None, max_sigs=1, mode="trail"):
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
    e = hw = st = tgt = np.nan
    e_ts = None
    cur = None
    shigh, nbars, sigs = -np.inf, 0, 0
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            shigh, nbars, sigs = -np.inf, 0, 0
            pend = pos = 0
        if not eligible[i]:
            continue
        if pend:
            pos, pend = 1, 0
            e = hw = opens[i]
            st = e - 2.0 * atr[i - 1]
            tgt = e + pt * atr[i - 1] if pt else np.inf
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos:
            if lows[i] <= st:                       # stop-first
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "stop"))
                pos = 0
            elif highs[i] >= tgt:
                x = max(tgt, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "pt"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i], "eod"))
                pos = 0
            elif mode == "trail":
                hw = max(hw, highs[i])
                st = max(st, hw - 2.0 * atr[i])
        if (pos == 0 and pend == 0 and sigs < max_sigs and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < 15):
            loc = EXPO.index.searchsorted(ts[i + 1], side="right") - 1
            if loc >= 0 and EXPO.iloc[loc] > 0:
                pend = 1
                sigs += 1
        shigh = max(shigh, highs[i])
        nbars += 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "reason"])
    T["pnl"] = (T["exit_px"] - T["entry_px"]) * T["sym"].map(rg.MULT) - rg.FR
    return T[T["entry_time"] >= rg.START]


def rep(name, **kw):
    S = pd.concat([pt_sat(s, **kw) for s in M1])
    S25 = S[S["exit_time"] < "2026-01-01"]
    sp = pd.Series(S25["pnl"].values,
                   index=pd.DatetimeIndex(S25["exit_time"])).sort_index()
    tot = float(sp.sum())
    h1 = float(sp[:"2017"].sum())
    h2 = float(sp["2018":].sum())
    p7 = float((S25["pnl"] - 3.5).sum())
    npt = int((S25["reason"] == "pt").sum())
    win = tot > 10397 and h1 >= 173 and h2 >= 9724 and p7 > 6988
    print(f"{name:18} sat {tot:>7,.0f} n{len(S25):>5} pt-exits {npt:>5} "
          f"halves {h1:>6,.0f}/{h2:>7,.0f} $7RT {p7:>7,.0f} "
          f"{'WIN' if win else ''}", flush=True)


rep("REF_deployed", pt=None, max_sigs=1)
for p in (0.75, 1.0, 1.5, 2.0):
    for ms in (2, 3, 99):
        rep(f"trail_pt{p}_m{ms}", pt=p, max_sigs=ms, mode="trail")
for p in (1.0, 2.0):
    rep(f"bracket_pt{p}_m99", pt=p, max_sigs=99, mode="fixed")
print("DONE")
