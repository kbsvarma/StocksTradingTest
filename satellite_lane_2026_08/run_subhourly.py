"""Sub-hourly satellite grid per PREREG_SUBHOURLY.md."""
import glob
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


def load_native(sym, freq):
    files = sorted(glob.glob(str(rg.SRC[sym])))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["roll_flag"] = (df["instrument_id"]
                       != df["instrument_id"].shift()).astype(int)
    g = df.resample(freq, label="left")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last(),
                      "roll": g["roll_flag"].max()}).dropna(subset=["close"])
    B["roll"] = B["roll"].fillna(0).astype(int)
    return B


print("loading bars (60m + 30m + 15m)...", flush=True)
BARS = {}
for sym in ("NQ", "ES", "YM"):
    raw = {}
    for freq in ("60min", "30min", "15min"):
        raw[freq] = load_native(sym, freq)
    BARS[sym] = raw
daily = {s: BARS[s]["60min"]["close"].resample("1D").last().dropna()
         for s in BARS}
rg.START = pd.Timestamp("2011-01-01", tz="UTC")
W = rg.weekly_weights(daily, 35, "aggressive300",
                      min(b["60min"].index.min() for b in BARS.values()),
                      max(b["60min"].index.max() for b in BARS.values()))
WROWS = {m: W.loc[m] for m in W.index}

print("core lane + exposure...", flush=True)
core = []
for sym in BARS:
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    T = rg.simulate_weighted(BARS[sym]["60min"], sym, wq)
    T = T[T["qty"] > 0].copy()
    T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
    core.append(T)
CORE = pd.concat(core)
assert abs(CORE["pnl"].sum() - 57138.47) < 1, "core gate-zero failed"
EXPO = (pd.concat([
    pd.Series(CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["entry_time"])),
    pd.Series(-CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["exit_time"]))])
    .groupby(level=0).sum().sort_index().cumsum())

HFRAME = {s: strategy.signal_frame(BARS[s]["60min"], rg.SCFG) for s in BARS}


def benched(sym, ts):
    row = WROWS.get(monday_of_week(ts))
    return (row is not None and int(row[sym]) == 0
            and sum(int(row[s]) == 0 for s in BARS) < 3)


def day_sat(sym, freq, window, stop_basis):
    B = BARS[sym][freq]
    hourly = HFRAME[sym]
    vol_ok_h = hourly["vol_ok"].reindex(B.index, method="ffill")
    if stop_basis == "native":
        atr_s = strategy.atr(B, 20)
    else:
        atr_s = hourly["atr"].reindex(B.index, method="ffill")
    atr = atr_s.values
    vol_ok = vol_ok_h.fillna(False).values
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
                trades.append((e_ts, ts[i], sym, e, x, "stop"))
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i], "eod"))
                pos = 0
            else:
                hw = max(hw, highs[i])
                st = max(st, hw - 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur):
            h_entry = hours[i + 1]
            ok = (h_entry < 14 if window == "early"
                  else 12 <= h_entry < 14)
            if ok:
                loc = EXPO.index.searchsorted(ts[i + 1], side="right") - 1
                if loc >= 0 and EXPO.iloc[loc] > 0:
                    pend = 1
                    done = True
        shigh = max(shigh, highs[i])
        nbars += 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "sym",
                                      "entry_px", "exit_px", "reason"])
    T["pnl"] = (T["exit_px"] - T["entry_px"]) * T["sym"].map(rg.MULT) - rg.FR
    return T[T["entry_time"] >= rg.START]


def daily_mtm_dd(all_trades, lo="2011-01-01", hi="2025-12-31"):
    days = pd.date_range(lo, hi, freq="D", tz="UTC")
    eq = pd.Series(0.0, index=days)
    for sym in BARS:
        T = all_trades[all_trades["sym"] == sym]
        dc = daily[sym].reindex(days, method="ffill")
        for tr in T.itertuples():
            mult = rg.MULT[sym]
            q = getattr(tr, "qty", 1)
            live = ((days >= tr.entry_time.normalize())
                    & (days < tr.exit_time.normalize()))
            eq[live] += (dc[live] - tr.entry_px) * mult * q
            eq[days >= tr.exit_time.normalize()] += (
                (tr.exit_px - tr.entry_px) * mult * q - rg.FR * q)
    return float((eq - eq.cummax()).min())


base_t = CORE[(CORE["exit_time"] >= "2011-01-01")
              & (CORE["exit_time"] < "2026-01-01")]
bp = pd.Series(base_t["pnl"].values,
               index=pd.DatetimeIndex(base_t["exit_time"])).sort_index()
BASE_TOTAL, BASE_RATIO, BASE_WORST = 54371.0, 8.05, -1728.0

rows = []
cells = [("60m_ref", "60min", "noon", "hequiv")]
for freq, ftag in (("30min", "30m"), ("15min", "15m")):
    for window in ("early", "noon"):
        for stop in ("native", "hequiv"):
            cells.append((f"{ftag}_{window}_{stop}", freq, window, stop))
for name, freq, window, stop in cells:
    S = pd.concat([day_sat(s, freq, window, stop) for s in BARS])
    S25 = S[S["exit_time"] < "2026-01-01"]
    sp = pd.Series(S25["pnl"].values,
                   index=pd.DatetimeIndex(S25["exit_time"])).sort_index()
    comb_trades = pd.concat([base_t, S25.assign(qty=1)])
    cdd = daily_mtm_dd(comb_trades)
    cp = pd.concat([bp, sp]).sort_index()
    cyr = cp.groupby(cp.index.year).sum()
    tot = float(cp.sum())
    gate = (float(sp.sum()) > 0 and tot / abs(cdd) >= BASE_RATIO
            and float(cyr.min()) >= BASE_WORST - 500)
    y26 = float(S[S["exit_time"] >= "2026-01-01"]["pnl"].sum())
    p7 = float((S25["pnl"] - 3.5).sum())
    rows.append(dict(cell=name, sat=round(float(sp.sum())), n=len(S25),
                     comb=round(tot), ratio=round(tot / abs(cdd), 2),
                     worst=round(float(cyr.min())),
                     h1=round(float(sp[:"2017"].sum())),
                     h2=round(float(sp["2018":].sum())),
                     rt7=round(p7), y26=round(y26),
                     gate="PASS" if gate else ""))
    r = rows[-1]
    print(f"{name:18} sat {r['sat']:>6} n{r['n']:>4}  comb {r['comb']:>6} "
          f"ratio {r['ratio']:>5} worst {r['worst']:>6} "
          f"halves {r['h1']:>5}/{r['h2']:>5}  $7RT {r['rt7']:>6} "
          f"2026 {r['y26']:>5} {r['gate']}", flush=True)
pd.DataFrame(rows).to_csv(HERE / "subhourly_cells.csv", index=False)
print("DONE")
