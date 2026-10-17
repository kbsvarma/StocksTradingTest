"""Bear satellite with profit-target exits per PREREG_BEARPT.md.

Engine inherited from run_bearsat.py (itself a strict mirror of the
deployed v2 geometry); ONLY the exit management changes per cell:
cover-limit target at entry - k*ATR60 (frozen at entry), stop either the
exact BEARSAT trail or a fixed entry+2*ATR. Stop-first within a bar.
"""
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
    j = STOP_EXITS.searchsorted(t)
    return j > 0 and (t - STOP_EXITS[j - 1]) <= pd.Timedelta(hours=24)


PREP = {}
for _sym in M1:
    B = native(_sym, "15min")
    hourly = HFRAME[_sym]
    et = B.index.tz_convert("America/New_York")
    PREP[_sym] = dict(
        B=B,
        vol_ok=hourly["vol_ok"].reindex(B.index, method="ffill"
                                        ).fillna(False).values,
        atr=hourly["atr"].reindex(B.index, method="ffill").values,
        hours=et.hour.values, dates=et.date,
        opens=B["open"].values, highs=B["high"].values,
        lows=B["low"].values, closes=B["close"].values,
        rolls=B["roll"].values, ts=B.index)


def bear_pt(sym, kind, tgt_k, style):
    p = PREP[sym]
    atr, hours, dates = p["atr"], p["hours"], p["dates"]
    opens, highs, lows, closes = (p["opens"], p["highs"], p["lows"],
                                  p["closes"])
    ts = p["ts"]
    eligible = (hours >= 9) & (hours < 16)
    roll_dates = set(dates[p["rolls"] == 1])
    last_elig = {}
    for i in range(len(ts)):
        if eligible[i]:
            last_elig[dates[i]] = i
    vol_ok = p["vol_ok"]
    trades = []
    pos = pend = 0
    e = lw = st = tgt = np.nan
    e_ts = None
    cur = None
    slow, nbars, done = np.inf, 0, False
    for i in range(len(ts)):
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
            tgt = e - tgt_k * atr[i - 1]
            e_ts = ts[i]
            if np.isnan(st) or np.isnan(tgt):
                pos = 0
        if pos:
            if highs[i] >= st:                      # stop-first, gap-honest
                x = max(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "stop"))
                pos = 0
            elif lows[i] <= tgt:                    # limit cover
                x = min(tgt, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "target"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i], "eod"))
                pos = 0
            elif style == "TRAIL":
                lw = min(lw, lows[i])
                st = min(st, lw + 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] < slow - 0.25 * atr[i]
                and i + 1 < len(ts) and eligible[i + 1]
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


rows = []
for kind in ("C1", "C3", "C4"):
    for style in ("TRAIL", "FIXED"):
        for tgt_k in (0.5, 0.75, 1.0, 1.5):
            S = pd.concat([bear_pt(s, kind, tgt_k, style) for s in M1])
            S25 = S[S["exit_time"] < "2026-01-01"]
            sp = pd.Series(S25["pnl"].values,
                           index=pd.DatetimeIndex(S25["exit_time"])
                           ).sort_index()
            cdd = daily_mtm_dd(pd.concat([base_t, S25.assign(qty=1)]))
            cp = pd.concat([bp, sp]).sort_index()
            cyr = cp.groupby(cp.index.year).sum()
            sat = float(sp.sum())
            ratio = float(cp.sum()) / abs(cdd)
            worst = float(cyr.min())
            h1 = float(sp[:"2017"].sum())
            h2 = float(sp["2018":].sum())
            gate = sat > 0 and ratio >= 8.05 and worst >= -2228
            n = len(S25)
            s7 = sat - 3.5 * n
            y22 = float(sp[sp.index.year == 2022].sum()) if len(sp) else 0
            y26 = float(S[S["exit_time"] >= "2026-01-01"]["pnl"].sum())
            hit = float((S25["reason"] == "target").mean()) if n else 0
            rows.append(dict(kind=kind, style=style, tgt=tgt_k, sat=round(sat),
                             n=n, ratio=round(ratio, 2), worst=round(worst),
                             h1=round(h1), h2=round(h2), rt7=round(s7),
                             y2022=round(y22), y2026=round(y26),
                             tgt_hit=round(hit, 2),
                             gate="PASS" if gate else ""))
            r = rows[-1]
            print(f"{kind} {style:5} tgt{tgt_k:<4} sat {r['sat']:>7,} "
                  f"n{n:>5} ratio {r['ratio']:>5.2f} worst {r['worst']:>7,} "
                  f"halves {r['h1']:>6,}/{r['h2']:>6,} $7RT {r['rt7']:>7,} "
                  f"2022 {r['y2022']:>6,} hit {r['tgt_hit']:.2f} {r['gate']}",
                  flush=True)
pd.DataFrame(rows).to_csv(Path(__file__).parent / "bearpt_cells.csv",
                          index=False)
print("DONE")
