"""Day-scoped satellite cells per PREREG_DAYSAT.md."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reentry_regime_2026_07"))
import engine_v as ev                                    # noqa: E402
rg = ev.rg
import strategy                                          # noqa: E402
from rotation import monday_of_week                      # noqa: E402

print("loading roll-aware bars...", flush=True)
bars, daily = {}, {}
for sym in ("NQ", "ES", "YM"):
    B = rg.load_60m(sym)
    bars[sym] = B
    daily[sym] = B["close"].resample("1D").last().dropna()
rg.START = pd.Timestamp("2011-01-01", tz="UTC")
W = rg.weekly_weights(daily, 35, "aggressive300",
                      min(b.index.min() for b in bars.values()),
                      max(b.index.max() for b in bars.values()))
WROWS = {m: W.loc[m] for m in W.index}


def benched_mask(sym, idx):
    out = np.zeros(len(idx), dtype=bool)
    mons = pd.Series([monday_of_week(t) for t in idx])
    for i, (t, mon) in enumerate(zip(idx, mons)):
        row = WROWS.get(mon)
        if row is None:
            continue
        if int(row[sym]) == 0 and sum(int(row[s]) == 0 for s in bars) < 3:
            out[i] = True
    return out


def day_sat(sym, anchor, exit_kind):
    """One-day trend satellite on benched weeks. Returns trades DataFrame."""
    B = bars[sym]
    sig = strategy.signal_frame(B, rg.SCFG)
    atr = sig["atr"].values
    vol_ok = sig["vol_ok"].values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour
    # session id: overnight anchor -> 18:00 starts next session
    if anchor == "on18":
        sess = (et + pd.Timedelta(hours=6)).date
    else:
        sess = et.date
    in_session = (hours >= 9) if anchor == "day9" else np.ones(len(B), bool)
    end_bar = hours == 16
    benched = benched_mask(sym, B.index)
    opens, highs, lows, closes = (
        B[c].values for c in ("open", "high", "low", "close"))
    rolls = B["roll"].values
    ts = B.index

    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    cur_sess = None
    sess_high = -np.inf
    sess_bars = 0
    traded_this_sess = False
    k = {"trail1": 1.0, "trail2": 2.0, "close": None}[exit_kind]
    for i in range(len(B)):
        if sess[i] != cur_sess:
            cur_sess = sess[i]
            sess_high, sess_bars = -np.inf, 0
            traded_this_sess = False
            pend = 0                      # never carry a pending across days
        if pend:
            pos, pend = 1, 0
            e = hw = opens[i]
            st = (e - k * atr[i - 1]) if k else -np.inf
            e_ts = ts[i]
            if k and np.isnan(st):
                pos = 0
        if pos and rolls[i] == 1:
            x = closes[i - 1] if i > 0 else opens[i]
            trades.append((e_ts, ts[i], sym, e, x, x - e, "roll", 1))
            pos = 0
        if pos:
            if k and lows[i] <= st:
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, x - e, "stop", 1))
                pos = 0
            elif end_bar[i]:
                trades.append((e_ts, ts[i], sym, e, closes[i],
                               closes[i] - e, "eod", 1))
                pos = 0
            elif k:
                hw = max(hw, highs[i])
                st = max(st, hw - k * atr[i])
        if in_session[i]:
            if (pos == 0 and pend == 0 and not traded_this_sess
                    and benched[i] and sess_bars >= 2
                    and closes[i] > sess_high and vol_ok[i]
                    and not end_bar[i] and not np.isnan(atr[i])
                    and i + 1 < len(B) and sess[i + 1] == cur_sess):
                pend = 1
                traded_this_sess = True
            sess_high = max(sess_high, highs[i])
            sess_bars += 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "points",
                                      "reason", "qty"])
    return T[T["entry_time"] >= rg.START]


def overnight_hold(sym):
    B = bars[sym]
    et = B.index.tz_convert("America/New_York")
    benched = benched_mask(sym, B.index)
    trades = []
    opens, closes = B["open"].values, B["close"].values
    rolls = B["roll"].values
    ts = B.index
    i18 = np.where((et.hour == 18) & benched)[0]
    hours = et.hour
    for i in i18:
        j = i + 1
        while j < len(B) and hours[j] != 9:
            if rolls[j]:
                break
            j += 1
        if j >= len(B) or hours[j] != 9:
            continue
        trades.append((ts[i], ts[j], sym, opens[i], opens[j],
                       opens[j] - opens[i], "on", 1))
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "points",
                                      "reason", "qty"])
    return T[T["entry_time"] >= rg.START]


BASE = dict(total=54371, ratio=8.05, worst=-1728)
core_pnl = None      # satellite is additive; report satellite standalone +
                     # combined gates vs verified base numbers

rows = []
cells = [(f"{a}_{x}",) + (a, x) for a in ("day9", "on18")
         for x in ("trail1", "trail2", "close")]
for name, anchor, exit_kind in cells:
    allT = pd.concat([day_sat(s, anchor, exit_kind) for s in bars])
    allT["pnl"] = (allT["points"] * allT["sym"].map(rg.MULT) - rg.FR)
    P = allT[allT["exit_time"] < "2026-01-01"]
    pnl = pd.Series(P["pnl"].values, index=pd.DatetimeIndex(P["exit_time"]))
    yearly = pnl.groupby(pnl.index.year).sum()
    tot = float(pnl.sum())
    print(f"{name:12} sat-only total ${tot:>9,.0f}  trades {len(P):>5}  "
          f"mean ${tot / max(len(P), 1):>6.2f}  "
          f"worst-yr ${float(yearly.min()):>7,.0f}  "
          f"combined ${BASE['total'] + tot:>9,.0f} "
          f"({'+' if tot > 0 else ''}{tot / BASE['total'] * 100:.1f}%)",
          flush=True)
    rows.append(dict(cell=name, sat_total=round(tot), trades=len(P),
                     worst_yr=round(float(yearly.min()))))

allT = pd.concat([overnight_hold(s) for s in bars])
allT["pnl"] = (allT["points"] * allT["sym"].map(rg.MULT) - rg.FR)
P = allT[allT["exit_time"] < "2026-01-01"]
pnl = pd.Series(P["pnl"].values, index=pd.DatetimeIndex(P["exit_time"]))
tot = float(pnl.sum())
yearly = pnl.groupby(pnl.index.year).sum()
print(f"{'C-ON hold':12} sat-only total ${tot:>9,.0f}  trades {len(P):>5}  "
      f"mean ${tot / max(len(P), 1):>6.2f}  "
      f"worst-yr ${float(yearly.min()):>7,.0f}", flush=True)
rows.append(dict(cell="C_ON_hold", sat_total=round(tot), trades=len(P),
                 worst_yr=round(float(yearly.min()))))
pd.DataFrame(rows).to_csv(HERE / "daysat_cells.csv", index=False)
print("DONE")
