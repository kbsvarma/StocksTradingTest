"""Breadth satellite's would-have-been record over the LIVE paper window
(2026-07-05 -> now), on the live BarStore bars (no rolls in-window)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reentry_regime_2026_07"))
import engine_v as ev
rg = ev.rg
import strategy
from rotation import monday_of_week
from data import BarStore

store = BarStore(HERE.parent / "trend_bot" / "state")
bars, daily = {}, {}
for sym in ("NQ", "ES", "YM"):
    B = store.bars(sym)[["open", "high", "low", "close"]].copy()
    B["roll"] = 0
    bars[sym] = B
    daily[sym] = B["close"].resample("1D").last().dropna()
rg.START = min(b.index.min() for b in bars.values())
W = rg.weekly_weights(daily, 35, "aggressive300", rg.START,
                      max(b.index.max() for b in bars.values()))
WROWS = {m: W.loc[m] for m in W.index}

core = []
for sym in bars:
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    T = rg.simulate_weighted(bars[sym], sym, wq)
    core.append(T[T["qty"] > 0])
CORE = pd.concat(core)
evs = pd.concat([
    pd.Series(CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["entry_time"])),
    pd.Series(-CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["exit_time"]))])
EXPO = evs.groupby(level=0).sum().sort_index().cumsum()


def benched(sym, ts):
    row = WROWS.get(monday_of_week(ts))
    return (row is not None and int(row[sym]) == 0
            and sum(int(row[s]) == 0 for s in bars) < 3)


def day_sat(sym):
    B = bars[sym]
    sig = strategy.signal_frame(B, rg.SCFG)
    atr = sig["atr"].values
    vol_ok = sig["vol_ok"].values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= 9) & (hours < 16)
    opens, highs, lows, closes = (B[c].values for c in
                                  ("open", "high", "low", "close"))
    ts = B.index
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    cur = None
    shigh, nbars, done = -np.inf, 0, False
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
            st = e - 2.0 * atr[i - 1]
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
                st = max(st, hw - 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and benched(sym, ts[i]) and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < 14):
            loc = EXPO.index.searchsorted(ts[i + 1], side="right") - 1
            if loc >= 0 and EXPO.iloc[loc] > 0:
                pend = 1
                done = True
        shigh = max(shigh, highs[i])
        nbars += 1
    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                         "entry_px", "exit_px", "points",
                                         "reason"])


S = pd.concat([day_sat(s) for s in bars])
S["pnl"] = S["points"] * S["sym"].map(rg.MULT) - rg.FR
P = S[S["entry_time"] >= "2026-07-05"]
print(f"SATELLITE paper window (2026-07-05 -> now): "
      f"${P['pnl'].sum():,.2f} / {len(P)} trades")
for r in P.itertuples():
    print(f"  {r.sym} in {r.entry_time:%m-%d %H:%M} -> "
          f"out {r.exit_time:%m-%d %H:%M}  ${r.pnl:>8,.2f}  {r.reason}")
