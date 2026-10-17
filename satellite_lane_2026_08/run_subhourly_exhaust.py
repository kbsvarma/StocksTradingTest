"""Sub-hourly satellite exhaustion per PREREG_SUBHOURLY_EXHAUST.md.
Efficient: 1-min loaded once per sym, resampled in memory per freq.
Incremental CSV so a crash loses nothing."""
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

print("loading 1-min...", flush=True)
M1 = {}
for sym in ("NQ", "ES", "YM"):
    files = sorted(glob.glob(str(rg.SRC[sym])))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["roll_flag"] = (df["instrument_id"]
                       != df["instrument_id"].shift()).astype(int)
    M1[sym] = df

_CACHE = {}


def native(sym, freq):
    key = (sym, freq)
    if key not in _CACHE:
        g = M1[sym].resample(freq, label="left")
        B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                          "low": g["low"].min(), "close": g["close"].last(),
                          "roll": g["roll_flag"].max()}
                         ).dropna(subset=["close"])
        B["roll"] = B["roll"].fillna(0).astype(int)
        _CACHE[key] = B
    return _CACHE[key]


daily = {s: native(s, "60min")["close"].resample("1D").last().dropna()
         for s in M1}
rg.START = pd.Timestamp("2011-01-01", tz="UTC")
W = rg.weekly_weights(daily, 35, "aggressive300",
                      min(native(s, "60min").index.min() for s in M1),
                      max(native(s, "60min").index.max() for s in M1))
WROWS = {m: W.loc[m] for m in W.index}

print("core lane...", flush=True)
core = []
for sym in M1:
    wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                            if monday_of_week(ts) in W.index else 1)
    T = rg.simulate_weighted(native(sym, "60min"), sym, wq)
    T = T[T["qty"] > 0].copy()
    T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
    core.append(T)
CORE = pd.concat(core)
assert abs(CORE["pnl"].sum() - 57138.47) < 1
EXPO = (pd.concat([
    pd.Series(CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["entry_time"])),
    pd.Series(-CORE["qty"].to_numpy(), index=pd.DatetimeIndex(CORE["exit_time"]))])
    .groupby(level=0).sum().sort_index().cumsum())
HFRAME = {s: strategy.signal_frame(native(s, "60min"), rg.SCFG) for s in M1}


def benched(sym, ts):
    row = WROWS.get(monday_of_week(ts))
    return (row is not None and int(row[sym]) == 0
            and sum(int(row[s]) == 0 for s in M1) < 3)


def day_sat(sym, freq="15min", k=2.0, buffer=0.25, cutoff=14, prior=2,
            flat=16, start=9, stop_basis="hequiv", reentry=False):
    B = native(sym, freq)
    hourly = HFRAME[sym]
    vol_ok = hourly["vol_ok"].reindex(B.index, method="ffill"
                                      ).fillna(False).values
    if stop_basis == "native":
        atr = strategy.atr(B, 20).values
    else:
        atr = hourly["atr"].reindex(B.index, method="ffill").values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    dates = et.date
    eligible = (hours >= start) & (hours < flat)
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
    e = st = np.nan
    hw = np.nan
    e_ts = None
    cur = None
    shigh, nbars, sigs = -np.inf, 0, 0
    max_sigs = 2 if reentry else 1
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
            st = e - k * atr[i - 1]
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
                st = max(st, hw - k * atr[i])
        if (pos == 0 and pend == 0 and sigs < max_sigs and nbars >= prior
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + buffer * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < cutoff):
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


def daily_mtm_dd(all_trades, lo="2011-01-01", hi="2025-12-31"):
    days = pd.date_range(lo, hi, freq="D", tz="UTC")
    eq = pd.Series(0.0, index=days)
    for sym in M1:
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
OUT = HERE / "subhourly_exhaust.csv"
rows = []


def run(name, **kw):
    S = pd.concat([day_sat(s, **kw) for s in M1])
    S25 = S[S["exit_time"] < "2026-01-01"]
    sp = pd.Series(S25["pnl"].values,
                   index=pd.DatetimeIndex(S25["exit_time"])).sort_index()
    cdd = daily_mtm_dd(pd.concat([base_t, S25.assign(qty=1)]))
    cp = pd.concat([bp, sp]).sort_index()
    cyr = cp.groupby(cp.index.year).sum()
    tot = float(cp.sum())
    gate = (float(sp.sum()) > 0 and tot / abs(cdd) >= 8.05
            and float(cyr.min()) >= -2228)
    r = dict(cell=name, sat=round(float(sp.sum())), n=len(S25),
             ratio=round(tot / abs(cdd), 2), worst=round(float(cyr.min())),
             h1=round(float(sp[:"2017"].sum())),
             h2=round(float(sp["2018":].sum())),
             rt7=round(float((S25["pnl"] - 3.5).sum())),
             y26=round(float(S[S["exit_time"] >= "2026-01-01"]["pnl"].sum())),
             gate="PASS" if gate else "")
    rows.append(r)
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f"{name:22} sat {r['sat']:>6} n{r['n']:>5} ratio {r['ratio']:>5} "
          f"worst {r['worst']:>6} halves {r['h1']:>5}/{r['h2']:>5} "
          f"$7 {r['rt7']:>6} 26 {r['y26']:>5} {r['gate']}", flush=True)
    return r


run("ANCHOR")
for f in ("5min", "10min", "20min", "30min"):
    run(f"bar_{f}", freq=f)
for k in (1.5, 2.5, 3.0):
    run(f"k_{k}", k=k)
for b in (0.0, 0.10, 0.50):
    run(f"buf_{b}", buffer=b)
for c in (12, 13, 15):
    run(f"cutoff_{c}", cutoff=c)
for p in (4, 8):
    run(f"prior_{p}", prior=p)
for fl in (15, 17):
    run(f"flat_{fl}", flat=fl)
for s0 in (8, 10):
    run(f"start_{s0}", start=s0)
for kn in (3.0, 4.0):
    run(f"native_k{kn}", stop_basis="native", k=kn)
run("reentry2", reentry=True)
print("PHASE A DONE — Phase B assembled from the ledger by prereg rule",
      flush=True)
