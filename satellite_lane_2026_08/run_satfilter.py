"""Learned/state filters on the deployed satellite stream (PREREG_SATFILTER)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

src = open(Path(__file__).parent / "run_subhourly_exhaust.py").read()
exec(src[:src.index('run("ANCHOR")')])          # M1, native, CORE, EXPO...

# native() without volume -> extend here
_VCACHE = {}


def native_v(sym):
    if sym not in _VCACHE:
        g = M1[sym].resample("15min", label="left")
        B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                          "low": g["low"].min(), "close": g["close"].last(),
                          "volume": g["volume"].sum(),
                          "roll": g["roll_flag"].max()}
                         ).dropna(subset=["close"])
        B["roll"] = B["roll"].fillna(0).astype(int)
        _VCACHE[sym] = B
    return _VCACHE[sym]


VIX = None
vf = Path(__file__).parents[1] / "bps_rederive_2026_07/data/vix_daily_cboe.parquet"
if vf.exists():
    V = pd.read_parquet(vf)["close"]
    V.index = pd.to_datetime(V.index)
    VIX = V.shift(1)

LEADER = {m: max(r.index, key=lambda s: r[s]) for m, r in WROWS.items()}


def gen_trades(sym):
    """Deployed v2 satellite with per-trade FEATURES at the signal bar."""
    B = native_v(sym)
    hourly = HFRAME[sym]
    vol_ok = hourly["vol_ok"].reindex(B.index, method="ffill"
                                      ).fillna(False).values
    atr = hourly["atr"].reindex(B.index, method="ffill").values
    atrp = hourly["atr_pct"].reindex(B.index, method="ffill").values
    et = B.index.tz_convert("America/New_York")
    hours = et.hour.values
    minutes = (et.hour * 60 + et.minute).values
    dates = et.date
    slot = et.strftime("%H:%M")
    eligible = (hours >= 9) & (hours < 16)
    opens, highs, lows, closes, vols = (B[c].values for c in
                                        ("open", "high", "low", "close",
                                         "volume"))
    rolls = B["roll"].values
    ts = B.index
    roll_dates = set(dates[rolls == 1])
    last_elig = {}
    for i in range(len(B)):
        if eligible[i]:
            last_elig[dates[i]] = i
    # 20-day same-slot median volume, strictly prior
    volmed = (pd.Series(vols, index=B.index)
              .groupby(slot).transform(
                  lambda s: s.rolling(20, min_periods=5).median().shift(1))
              .values)
    dprev = {s2: daily[s2].shift(1) for s2 in M1}
    d15 = {s2: native_v(s2) for s2 in M1}
    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    feat_pend = None
    cur = None
    shigh, slow2, nbars, done = -np.inf, np.inf, 0, False
    sess_closes = []
    sess_open = np.nan
    for i in range(len(B)):
        if dates[i] != cur:
            cur = dates[i]
            shigh, slow2, nbars, done = -np.inf, np.inf, 0, False
            sess_closes = []
            sess_open = np.nan
            pend = pos = 0
        if not eligible[i]:
            continue
        if np.isnan(sess_open):
            sess_open = opens[i]
        if pend:
            pos, pend = 1, 0
            e = hw = opens[i]
            st = e - 2.0 * atr[i - 1]
            e_ts = ts[i]
            efeat = feat_pend
            if np.isnan(st):
                pos = 0
        if pos:
            if lows[i] <= st:
                x = min(st, opens[i])
                trades.append((e_ts, ts[i], sym, e, x, "stop") + efeat)
                pos = 0
            elif i == last_elig[cur]:
                trades.append((e_ts, ts[i], sym, e, closes[i], "eod") + efeat)
                pos = 0
            else:
                hw = max(hw, highs[i])
                st = max(st, hw - 2.0 * atr[i])
        if (pos == 0 and pend == 0 and not done and nbars >= 2
                and cur not in roll_dates and benched(sym, ts[i])
                and np.isfinite(atr[i]) and vol_ok[i]
                and closes[i] > shigh + 0.25 * atr[i]
                and i + 1 < len(B) and eligible[i + 1]
                and dates[i + 1] == cur and hours[i + 1] < 15):
            loc = EXPO.index.searchsorted(ts[i + 1], side="right") - 1
            if loc >= 0 and EXPO.iloc[loc] > 0:
                day = str(cur)
                mon = monday_of_week(ts[i])
                lead = LEADER.get(mon)
                lr = np.nan
                if lead:
                    L = d15[lead]
                    Let = L.index.tz_convert("America/New_York")
                    lb = L[(Let.date == cur) & (Let.hour >= 9)
                           & (L.index <= ts[i])]
                    if len(lb):
                        lr = lb["close"].iloc[-1] / lb["open"].iloc[0] - 1
                brd = 0
                for s2 in M1:
                    pc = dprev[s2].asof(pd.Timestamp(day, tz="UTC"))
                    L = d15[s2]
                    lb = L[L.index <= ts[i]]
                    if len(lb) and np.isfinite(pc) and \
                            lb["close"].iloc[-1] > pc:
                        brd += 1
                pc_own = dprev[sym].asof(pd.Timestamp(day, tz="UTC"))
                gap = (sess_open / pc_own - 1) if np.isfinite(pc_own) else 0.0
                rng = ((shigh - slow2) / atr[i]
                       if np.isfinite(slow2) and atr[i] else 0.0)
                rv = (vols[i] / volmed[i]
                      if volmed[i] and np.isfinite(volmed[i]) else np.nan)
                vwapp = (closes[i] / np.mean(sess_closes) - 1
                         if sess_closes else 0.0)
                vx = (float(VIX.asof(pd.Timestamp(day)))
                      if VIX is not None else np.nan)
                feat_pend = (rng, float(minutes[i] - 540), gap,
                             float(atrp[i]), rv, lr, float(brd), vwapp,
                             vx, float(atr[i]))
                pend = 1
                done = True
        shigh = max(shigh, highs[i])
        slow2 = min(slow2, lows[i])
        sess_closes.append(closes[i])
        nbars += 1
    cols = ["entry_time", "exit_time", "sym", "entry_px", "exit_px",
            "reason", "f_rng", "f_min", "f_gap", "f_atrp", "f_rv", "f_lead",
            "f_brd", "f_vwap", "f_vix", "f_atr"]
    T = pd.DataFrame(trades, columns=cols)
    T["pnl"] = (T["exit_px"] - T["entry_px"]) * T["sym"].map(rg.MULT) - rg.FR
    T["pnl_ru"] = T["pnl"] / (2 * T["f_atr"] * T["sym"].map(rg.MULT))
    return T[T["entry_time"] >= rg.START]


print("generating base stream with features...", flush=True)
ALL = pd.concat([gen_trades(s) for s in M1]).sort_values("entry_time")
ALL = ALL[ALL["exit_time"] < "2026-01-01"].reset_index(drop=True)
print(f"base: n={len(ALL)} total ${ALL['pnl'].sum():,.0f}", flush=True)

FEATS8 = ["f_rng", "f_min", "f_gap", "f_atrp", "f_rv", "f_lead", "f_brd",
          "f_vwap"]


def model_mask(kind):
    take = np.ones(len(ALL), dtype=bool)
    months = pd.date_range("2011-01-01", "2026-01-01", freq="MS", tz="UTC")
    X = ALL[FEATS8].fillna(0.0).values
    y_ru = ALL["pnl_ru"].values
    y_win = (ALL["pnl"] > 0).astype(float).values
    ent = pd.DatetimeIndex(ALL["entry_time"])
    ext = pd.DatetimeIndex(ALL["exit_time"])
    for m0, m1_ in zip(months[:-1], months[1:]):
        sel = (ent >= m0) & (ent < m1_)
        if not sel.any():
            continue
        tr = ext < m0
        if tr.sum() < 150:
            continue
        Xt = X[tr]
        mu, sd = Xt.mean(0), Xt.std(0) + 1e-9
        Xs = (Xt - mu) / sd
        Xp = (X[sel] - mu) / sd
        if kind == "ridge":
            A = Xs.T @ Xs + 10.0 * np.eye(Xs.shape[1])
            w = np.linalg.solve(A, Xs.T @ y_ru[tr])
            b = y_ru[tr].mean()
            take[sel] = (Xp @ w + b) > 0
        else:
            from sklearn.linear_model import LogisticRegression
            lr = LogisticRegression(C=0.5, max_iter=500)
            lr.fit(Xs, y_win[tr])
            take[sel] = lr.predict_proba(Xp)[:, 1] >= 0.5
    return take


def report(name, mask):
    S = ALL[mask]
    sp = pd.Series(S["pnl"].values,
                   index=pd.DatetimeIndex(S["exit_time"])).sort_index()
    tot = float(sp.sum())
    h1 = float(sp[:"2017"].sum())
    h2 = float(sp["2018":].sum())
    p7 = float((S["pnl"] - 3.5).sum())
    win = (tot > 10397 and h1 >= 423 - 250 and h2 >= 9974 - 250
           and p7 > 6988)
    print(f"{name:12} sat {tot:>7,.0f} n{len(S):>4} "
          f"halves {h1:>6,.0f}/{h2:>7,.0f} $7RT {p7:>7,.0f} "
          f"{'WIN' if win else ''}", flush=True)


report("UNFILTERED", np.ones(len(ALL), dtype=bool))
report("F1_ridge", model_mask("ridge"))
report("F2_logit", model_mask("logit"))
prior_mean = ALL["pnl"].rolling(20).mean().shift(1)
report("F3_curve20", (prior_mean > 0).fillna(True).values)
sym_prior = ALL.groupby("sym")["pnl"].transform(
    lambda s: s.rolling(10).mean().shift(1))
report("F4_symcurve", (sym_prior > 0).fillna(True).values)
vix = ALL["f_vix"]
vq = vix.expanding(100).quantile(0.7).shift(1)
calm = (vix < vq) | vix.isna() | vq.isna()
report("F5a_vixcalm", calm.values)
report("F5b_vixstr", (~calm | vix.isna() | vq.isna()).values)
report("F6_tape", (ALL["f_lead"].fillna(0) > 0).values)
report("F7_relvol", (ALL["f_rv"].fillna(1.0) > 1.0).values)
ALL.to_csv(Path(__file__).parent / "satfilter_base.csv", index=False)
print("DONE")
