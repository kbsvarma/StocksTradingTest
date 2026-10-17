"""Independent reimplementation of codex's selected breadth cell:
s9_x16_k2_p2_b0.25_eb14, core_active_or_entering_same_bar, benched-only."""
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

core = []
for sym in bars:
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    T = rg.simulate_weighted(bars[sym], sym, wq)
    core.append(T[T["qty"] > 0])
CORE = pd.concat(core)
ev_series = pd.concat([
    pd.Series(CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["entry_time"])),
    pd.Series(-CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["exit_time"]))])
EXPO = ev_series.groupby(level=0).sum().sort_index().cumsum()


def benched(sym, ts):
    row = WROWS.get(monday_of_week(ts))
    if row is None:
        return False
    return int(row[sym]) == 0 and sum(int(row[s]) == 0 for s in bars) < 3


def day_sat(sym, close_hour=16, k=2.0, buffer_atr=0.25, eb=14):
    B = bars[sym]
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
    # roll-flag sessions skipped
    roll_dates = set(dates[rolls == 1])
    # last eligible bar per date (discovered)
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
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
            pend = 0
            if pos:            # never carries; safety
                pos = 0
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
            # core active or entering at the satellite's entry bar
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


S = pd.concat([day_sat(s) for s in bars])
S["pnl"] = S["points"] * S["sym"].map(rg.MULT) - rg.FR
P = S[S["exit_time"] < "2026-01-01"]
pnl = pd.Series(P["pnl"].values,
                index=pd.DatetimeIndex(P["exit_time"])).sort_index()
yr = pnl.groupby(pnl.index.year).sum()
print(f"trades {len(P)} (codex 474)  total ${pnl.sum():,.0f} (codex $5,660)")
print(f"halves {pnl[:'2017'].sum():,.0f}/{pnl['2018':].sum():,.0f} "
      f"(codex 803/4,857)")
print(f"2026 avail: ${S[S['exit_time'] >= '2026-01-01']['pnl'].sum():,.0f} "
      f"(codex +282)")
p7 = (P["pnl"] - 3.5).sum()
print(f"at $7 RT: ${p7:,.0f} (codex 4,001)")
top = yr.loc[[y for y in (2022, 2023, 2024) if y in yr.index]].sum()
print(f"2022-24 share: {top/pnl.sum()*100:.0f}% (codex ~86%)")
print(yr.round(0).to_string())
