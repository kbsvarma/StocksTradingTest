"""Tight stop x target bear satellite per PREREG_BEARTIGHT.md.

Entries: identical 15m FSM to run_bearpt.py (same signal, conditioning,
one/session). Exits: resolved on 1-MINUTE bars entry->16:00 ET so tight
levels are ordered honestly (same-minute double touch stays stop-first).
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


def entries_for(sym, kind):
    """Same entry FSM as run_bearpt.py; returns entry rows only."""
    B = native(sym, "15min")
    hourly = HFRAME[sym]
    vol_ok = hourly["vol_ok"].reindex(B.index, method="ffill"
                                      ).fillna(False).values
    atr = hourly["atr"].reindex(B.index, method="ffill").values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= 9) & (hours < 16)
    opens, lows, closes = B["open"].values, B["low"].values, B["close"].values
    rolls = B["roll"].values
    ts = B.index
    roll_dates = set(dates[rolls == 1])
    out = []
    pend = 0
    cur = None
    slow, nbars, done = np.inf, 0, False
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            slow, nbars, done = np.inf, 0, False
            pend = 0
        if not eligible[i]:
            continue
        if pend:
            pend = 0
            if np.isfinite(atr[i - 1]):
                out.append((ts[i], opens[i], atr[i - 1], cur))
        if (pend == 0 and not done and nbars >= 2
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
    return out


ET_TZ = "America/New_York"
MIN_ARR = {}
for _s in M1:
    df = M1[_s]
    MIN_ARR[_s] = dict(ts=df.index, o=df["open"].values, h=df["high"].values,
                       lo=df["low"].values, c=df["close"].values)

ENTRIES = {}
for kind in ("C1", "C3"):
    for s in M1:
        rows = []
        for e_ts, e_px, e_atr, d in entries_for(s, kind):
            m = MIN_ARR[s]
            i0 = m["ts"].searchsorted(e_ts)
            end_utc = (pd.Timestamp(d).tz_localize(ET_TZ)
                       + pd.Timedelta(hours=16)).tz_convert("UTC")
            i1 = m["ts"].searchsorted(end_utc)
            if i1 > i0:
                rows.append((e_ts, e_px, e_atr, i0, i1))
        ENTRIES[(kind, s)] = rows


def run_cell(kind, stop_k, tgt_k):
    recs = []
    for s in M1:
        m = MIN_ARR[s]
        mult = rg.MULT[s]
        for e_ts, e, a, i0, i1 in ENTRIES[(kind, s)]:
            st, tg = e + stop_k * a, e - tgt_k * a
            hs, ls, os_ = m["h"][i0:i1], m["lo"][i0:i1], m["o"][i0:i1]
            hit_s = hs >= st
            hit_t = ls <= tg
            js = int(np.argmax(hit_s)) if hit_s.any() else -1
            jt = int(np.argmax(hit_t)) if hit_t.any() else -1
            if js >= 0 and (jt < 0 or js <= jt):        # stop-first on tie
                x, rsn, j = max(st, os_[js]), "stop", js
            elif jt >= 0:
                x, rsn, j = min(tg, os_[jt]), "target", jt
            else:
                x, rsn, j = m["c"][i1 - 1], "eod", i1 - 1 - i0
            recs.append((e_ts, m["ts"][i0 + j], s, e, x, rsn,
                         (e - x) * mult - rg.FR))
    return pd.DataFrame(recs, columns=["entry_time", "exit_time", "sym",
                                       "entry_px", "exit_px", "reason",
                                       "pnl"])


rows = []
for kind in ("C1", "C3"):
    for stop_k in (0.25, 0.5, 0.75, 1.0):
        for tgt_k in (0.5, 0.75, 1.0, 1.5):
            S = run_cell(kind, stop_k, tgt_k)
            S = S[S["entry_time"] >= rg.START]
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
            n = len(S25)
            gate = sat > 0 and ratio >= 8.05 and worst >= -2228
            r = dict(kind=kind, stop=stop_k, tgt=tgt_k, sat=round(sat), n=n,
                     ratio=round(ratio, 2), worst=round(worst),
                     h1=round(float(sp[:"2017"].sum())),
                     h2=round(float(sp["2018":].sum())),
                     rt7=round(sat - 3.5 * n),
                     y2022=round(float(sp[sp.index.year == 2022].sum())
                                 if len(sp) else 0),
                     y2026=round(float(
                         S[S["exit_time"] >= "2026-01-01"]["pnl"].sum())),
                     hit=round(float((S25["reason"] == "target").mean())
                               if n else 0, 2),
                     gate="PASS" if gate else "")
            rows.append(r)
            print(f"{kind} stop{stop_k:<4} tgt{tgt_k:<4} sat {r['sat']:>7,} "
                  f"n{n:>5} ratio {r['ratio']:>5.2f} worst {r['worst']:>7,} "
                  f"halves {r['h1']:>6,}/{r['h2']:>6,} $7RT {r['rt7']:>7,} "
                  f"2022 {r['y2022']:>6,} hit {r['hit']:.2f} {r['gate']}",
                  flush=True)
pd.DataFrame(rows).to_csv(Path(__file__).parent / "beartight_cells.csv",
                          index=False)
print("DONE")
