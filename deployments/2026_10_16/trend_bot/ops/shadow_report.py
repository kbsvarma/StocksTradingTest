#!/usr/bin/env python3
"""Offline shadow/benchmark report — READ-ONLY, never touches trading.

Computes, over the paper window from the bot's own stored bars:
  - fixed 1-1-1 benchmark (the only constitution-floor-passing config)
  - shadow variants: trail-3, vol-gate q=.975, NQ-floor 2+1
  - the live config replayed by the corrected research FSM (context line)
Writes state/shadow_report.txt. Run ad-hoc or nightly; zero live-path code.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "research"))

import rotation_grid as rg                              # noqa: E402
import strategy                                         # noqa: E402
from data import BarStore                               # noqa: E402
from rotation import monday_of_week, trailing_returns   # noqa: E402


LAST_TRADES = None      # set by every *_pnl: main() reads it for the
                        # since-freeze forward bracket (codex 08-03 audit)


def variant_pnl(bars, daily, mode="aggressive300", k_trail=2.0, q=0.95,
                weights_override=None, paper_start=None):
    rg.SCFG = dict(rg.SCFG, k_trail=k_trail, vol_gate_q=q)
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, mode, t0, t1)
    if weights_override is not None:
        W = W.apply(weights_override, axis=1, result_type="expand")
    rows = []
    for sym in rg.MULT:
        wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                                if monday_of_week(ts) in W.index else 1)
        T = rg.simulate_weighted(bars[sym], sym, wq)
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        rows.append(T)
    allT = pd.concat(rows)
    global LAST_TRADES
    LAST_TRADES = allT
    if paper_start is not None:
        P = allT[allT["entry_time"] >= paper_start]
        return (float(allT["pnl"].sum()), len(allT),
                float(P["pnl"].sum()), len(P))
    return float(allT["pnl"].sum()), len(allT)


def graduated_qty(score: pd.DataFrame) -> pd.DataFrame:
    """SHADOW-ONLY: map a cross-sectional score to graduated entry weights.

    Gap of leader over runner-up: < 0.35 -> 1-1-1; 0.35-0.90 -> 2-1-0;
    >= 0.90 -> 3-0-0. Pure function; tested in tests/test_shadow_adaptive.py.
    """
    v = score.to_numpy(dtype=float)
    order = np.argsort(-v, axis=1)
    n = np.arange(len(v))
    gap = v[n, order[:, 0]] - v[n, order[:, 1]]
    q = np.ones_like(v, dtype=int)
    mid = np.isfinite(gap) & (gap >= 0.35) & (gap < 0.90)
    hard = np.isfinite(gap) & (gap >= 0.90)
    for i in np.flatnonzero(mid):
        q[i] = 0
        q[i, order[i, 0]], q[i, order[i, 1]] = 2, 1
    for i in np.flatnonzero(hard):
        q[i] = 0
        q[i, order[i, 0]] = 3
    return pd.DataFrame(q, index=score.index, columns=score.columns)


def adaptive_policy(bars, daily):
    """SHADOW-ONLY adaptive-rotation policy (codex 2026-07-21, independently
    reproduced to the dollar 2026-07-22; protocol was POST-SELECTION over a
    261-cell screen — NOT preregistered, NOT a candidate; forward record
    decides). Score S = Z(Monday L35) + 2 * Z(live 5-session return), where
    the live return uses the current COMPLETED hourly close against the
    close of the 5th prior completed session (D-1 lag class honored).
    """
    syms = list(rg.MULT)
    idx = bars[syms[0]].index
    for s in syms[1:]:
        idx = idx.intersection(bars[s].index)
    mon_rows = {m: trailing_returns(daily, m, 35)
                for m in sorted(set(idx.map(monday_of_week)))}
    slow = (pd.DataFrame(mon_rows).T[syms]
            .reindex(idx.map(monday_of_week)).set_axis(idx))
    closes = pd.concat({s: bars[s]["close"].reindex(idx) for s in syms},
                       axis=1)
    fast = pd.DataFrame(index=idx, columns=syms, dtype=float)
    grouped = closes.groupby(closes.index.normalize()).groups
    for day, locs in grouped.items():
        for s in syms:
            prior = daily[s][daily[s].index < day]
            if len(prior) >= 5:
                fast.loc[locs, s] = closes.loc[locs, s] / prior.iloc[-5] - 1.0

    def zc(F):
        m = F.mean(axis=1)
        sd = F.std(axis=1, ddof=0).replace(0, np.nan)
        return F.sub(m, axis=0).div(sd, axis=0).fillna(0.0)

    return graduated_qty(zc(slow) + 2.0 * zc(fast))


def adaptive_variant_pnl(bars, daily, paper_start, scfg_over=None):
    """Replay a core config under the adaptive policy; totals + exposure."""
    if scfg_over:
        rg.SCFG = dict(rg.SCFG, **scfg_over)
    policy = adaptive_policy(bars, daily)

    def wq_for(sym):
        col = policy[sym]

        def f(ts):
            return int(col.asof(ts)) if ts >= col.index[0] else 1
        return f

    rows, events = [], []
    for sym in rg.MULT:
        T = rg.simulate_weighted(bars[sym], sym, wq_for(sym))
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        rows.append(T)
        for _, r in T.iterrows():
            events.append((r["entry_time"], int(r["qty"])))
            events.append((r["exit_time"], -int(r["qty"])))
    allT = pd.concat(rows)
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = mx = 0
    for _, q in events:
        cur += q
        mx = max(mx, cur)
    P = allT[allT["entry_time"] >= paper_start]
    global LAST_TRADES
    LAST_TRADES = allT
    return (float(allT["pnl"].sum()), len(allT),
            float(P["pnl"].sum()), len(P), mx)


# ---------- SHADOW-ONLY ridge entry filter --------------------------------
# Port of quant_replacement_2026_07 stage C (headline ridge10_mean cell),
# user-elected 2026-07-22. Monthly causally-trained ridge predicts each
# breakout's risk-unit expectancy from 11 completed-bar features; entries
# with non-positive predicted mean are SKIPPED (weights forced to 0).
# Verified upstream: +$12,908 at cap3 (82%% = avoided friction), Sharpe
# 0.81->1.25, 31-day embargo + corruption tests pass, BUT selection-
# adjusted bootstrap over 21 attempts FAILS -> NOT a candidate; the
# forward bracket decides. Clock features (hour sin/cos) are essential
# to the historical edge — known overfit-magnet caveat.

RIDGE_FEATURES = ("overshoot", "t24", "t120", "t480", "nema", "eff120",
                  "vol_ratio", "relative120", "breadth120",
                  "hour_sin", "hour_cos")
# R2 winner of ridge_plus_2026_07 (26-cell ledgered program, 2026-07-27):
# + relative volume at the signal bar. +$1,872/+2.9% vs R0 on a 6-neighbor
# plateau; shadow-grade, own line, forward bracket decides.
RIDGE_FEATURES_V2 = RIDGE_FEATURES + ("rel_vol",)


def _roll_clean_log_returns(B):
    r = np.log(B["close"]).diff()
    return r.mask(B["roll"].astype(bool), 0.0)


def _rolling_t(returns, h):
    m = returns.rolling(h, min_periods=h).mean()
    sd = returns.rolling(h, min_periods=h).std(ddof=1)
    return m.div(sd.replace(0.0, np.nan)) * np.sqrt(h)


def _eff(returns, h):
    disp = returns.rolling(h, min_periods=h).sum().abs()
    path = returns.abs().rolling(h, min_periods=h).sum()
    return disp.div(path.replace(0.0, np.nan)).clip(0.0, 1.0)


def _normalized_returns(B):
    r = _roll_clean_log_returns(B)
    vol = r.ewm(halflife=48, adjust=False, min_periods=240).std().shift(1)
    return r.div(vol.replace(0.0, np.nan))


def _nema(B):
    y = _normalized_returns(B)
    parts = []
    for hl in (24, 96, 384):
        a = 1.0 - np.exp(np.log(0.5) / hl)
        raw = y.ewm(alpha=a, adjust=False, min_periods=4 * hl).mean()
        parts.append(raw / np.sqrt(a / (2.0 - a)))
    return pd.concat(parts, axis=1).mean(axis=1, skipna=False)


def ridge_features(bars):
    frames, ret120 = {}, {}
    for sym in rg.MULT:
        B = bars[sym]
        f = strategy.signal_frame(B, rg.SCFG)
        r = _roll_clean_log_returns(B)
        ret120[sym] = r.rolling(120, min_periods=120).sum()
        hour = B.index.tz_convert("America/New_York").hour
        med = (B["volume"].rolling(480, min_periods=240).median().shift(1)
               if "volume" in B.columns else pd.Series(np.nan, index=B.index))
        frames[sym] = pd.DataFrame({
            "rel_vol": (np.log(B["volume"] / med.replace(0, np.nan))
                        .clip(-3, 3) if "volume" in B.columns
                        else pd.Series(np.nan, index=B.index)),
            "overshoot": (B["close"] - f["donchian"]) / f["atr"],
            "t24": _rolling_t(r, 24), "t120": _rolling_t(r, 120),
            "t480": _rolling_t(r, 480), "nema": _nema(B),
            "eff120": _eff(r, 120),
            "vol_ratio": f["atr_pct"] / f["vol_thresh"],
            "hour_sin": np.sin(2 * np.pi * hour / 24),
            "hour_cos": np.cos(2 * np.pi * hour / 24),
        }, index=B.index)
    common = ret120[list(rg.MULT)[0]].index
    for sym in list(rg.MULT)[1:]:
        common = common.intersection(ret120[sym].index)
    rel = pd.concat({s: ret120[s].reindex(common) for s in rg.MULT}, axis=1)
    m = rel.mean(axis=1)
    sd = rel.std(axis=1, ddof=0).replace(0, np.nan)
    rel_z = rel.sub(m, axis=0).div(sd, axis=0).fillna(0.0)
    breadth = rel.gt(0).sum(axis=1) / len(rg.MULT)
    for sym in rg.MULT:
        frames[sym]["relative120"] = rel_z[sym].reindex(frames[sym].index)
        frames[sym]["breadth120"] = breadth.reindex(frames[sym].index)
    return frames


def ridge_design(frame, means=None, scales=None, fnames=RIDGE_FEATURES):
    x = frame[list(fnames)].to_numpy(dtype=float)
    if means is None:
        means = np.nanmean(x, axis=0)
        scales = np.nanstd(x, axis=0, ddof=1)
        scales = np.where(np.isfinite(scales) & (scales > 0), scales, 1.0)
    z = (x - means) / scales
    s = frame["sym"].astype(str)
    mat = np.column_stack([np.ones(len(frame)), z,
                           s.eq("ES").to_numpy(dtype=float),
                           s.eq("YM").to_numpy(dtype=float)])
    return mat, means, scales


def ridge_fit(train, penalty=10.0, max_obs=5000, half_life=1000.0,
              fnames=RIDGE_FEATURES):
    train = train.dropna(subset=list(fnames)).tail(max_obs)
    x, means, scales = ridge_design(train, fnames=fnames)
    y = train["y"].to_numpy(dtype=float)
    age = np.arange(len(train) - 1, -1, -1)
    w = np.exp(np.log(0.5) * age / half_life)
    pen = np.eye(x.shape[1]) * penalty
    pen[0, 0] = 0.0
    pen[-2:, -2:] = 0.0
    beta = np.linalg.pinv(x.T @ (x * w[:, None]) + pen) @ (x.T @ (w * y))
    return beta, means, scales


def ridge_predict(model, frame, fnames=RIDGE_FEATURES):
    beta, means, scales = model
    x, _, _ = ridge_design(frame, means, scales, fnames=fnames)
    return x @ beta


def ridge_train_slice(teachers, month, embargo_days=0):
    """Trades usable for the model predicting `month`: EXITED strictly
    before the month starts (minus embargo) — the exit-leak guard."""
    cutoff = month - pd.Timedelta(days=embargo_days)
    return teachers[teachers["exit_time"] < cutoff]


def ridge_teachers(bars, features):
    rows = []
    for sym in rg.MULT:
        f = strategy.signal_frame(bars[sym], rg.SCFG)
        atr = f["atr"]
        idx = bars[sym].index
        T = rg.simulate_weighted(bars[sym], sym, lambda ts: 1)
        for tr in T.itertuples():
            si = idx.get_loc(tr.entry_time) - 1
            if si < 0:
                continue
            feat = features[sym].iloc[si]
            vals = feat[list(RIDGE_FEATURES)].to_numpy(dtype=float)
            if not np.isfinite(vals).all():
                continue
            risk = rg.SCFG["k_trail"] * float(atr.iloc[si]) * rg.MULT[sym]
            rows.append({"signal_time": idx[si], "exit_time": tr.exit_time,
                         "sym": sym,
                         "y": (tr.points * rg.MULT[sym] - rg.FR) / risk,
                         **feat[list(RIDGE_FEATURES_V2)].to_dict()})
    return pd.DataFrame(rows).sort_values("exit_time").reset_index(drop=True)


def ridge_acceptance(bars, features, teachers, penalty=10.0,
                     fnames=RIDGE_FEATURES):
    """Per-symbol 0/1 acceptance at every baseline signal bar."""
    base = {s: strategy.signal_frame(bars[s], rg.SCFG)["signal"]
            .astype(np.int8) for s in rg.MULT}
    out = {s: pd.Series(0, index=bars[s].index, dtype=np.int8)
           for s in rg.MULT}
    start = min(b.index.min() for b in bars.values()).normalize()
    end = max(b.index.max() for b in bars.values())
    for month in pd.date_range(start.replace(day=1), end, freq="MS", tz="UTC"):
        nxt = month + pd.offsets.MonthBegin(1)
        train = ridge_train_slice(teachers, month)
        blocks = []
        for s in rg.MULT:
            mask = ((bars[s].index >= month) & (bars[s].index < nxt)
                    & base[s].astype(bool))
            blk = features[s].loc[mask, list(fnames)].copy()
            blk["sym"] = s
            blk["stamp"] = blk.index
            blocks.append(blk)
        dec = pd.concat(blocks).dropna(subset=list(fnames))
        if dec.empty:
            continue
        if len(train) < 500:                    # startup: accept everything
            for row in dec.itertuples():
                out[row.sym].loc[row.stamp] = 1
            continue
        mean = ridge_predict(ridge_fit(train, penalty, fnames=fnames),
                             dec, fnames=fnames)
        for row, m in zip(dec.itertuples(), mean):
            if m > 0.0:
                out[row.sym].loc[row.stamp] = 1
    return out


def ridge_variant_pnl(bars, daily, paper_start, fnames=RIDGE_FEATURES,
                      shared=None):
    """Live-config weekly weights, entries gated by the ridge filter."""
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    if shared is None:
        features = ridge_features(bars)
        teachers = ridge_teachers(bars, features)
        shared = (features, teachers)
    features, teachers = shared
    accept = ridge_acceptance(bars, features, teachers, fnames=fnames)

    def wq_for(sym):
        acc = accept[sym]

        def f(ts):
            base = (int(W.loc[monday_of_week(ts), sym])
                    if monday_of_week(ts) in W.index else 1)
            return base if int(acc.at[ts]) else 0
        return f

    rows = []
    for sym in rg.MULT:
        T = rg.simulate_weighted(bars[sym], sym, wq_for(sym))
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        rows.append(T)
    allT = pd.concat(rows)
    global LAST_TRADES
    LAST_TRADES = allT
    P = allT[allT["entry_time"] >= paper_start]
    return (float(allT["pnl"].sum()), len(allT),
            float(P["pnl"].sum()), len(P), shared)




# ---------- SHADOW-ONLY regime-scaled trail (dynamic K20) ------------------
# Port of quant_scientific_phase2_2026_07 posterior_dynamic_k20_cap3,
# elected 2026-07-22 as the wide-trail family's board representative
# (replacing D15/K7 adaptive, archived in adaptive_rotation_2026_07/).
# Hypothesis under forward test: causal regime-scaled stop width improves
# profit per unit of risk (historical: P/DD 8.67 vs 7.04, worst-yr -$1,570,
# beats equal-DD-scaled production by +$17.6k) — BUT through-2025 adjusted
# p = 0.65 (2026-concentrated evidence) -> NOT a candidate; bracket decides.
# Mechanics: Wonham filter (FIXED constants: means ±0.10, q=1/240 — zero
# fitted parameters, forward-only recursion); at each signal bar
# strength = clip(2p-1, 0, 1), entry K = 2 + 18*strength, FROZEN for the
# trade; joint 3-contract cap, largest-remainder capacity allocation,
# stop-first, gap-honest, roll force-close.


def wonham_bull(B, mean=0.10, duration=240):
    obs = _normalized_returns(B).to_numpy(dtype=float)
    q = 1.0 / duration
    T = np.array([[1.0 - q, q], [q, 1.0 - q]])
    means = np.array([-mean, mean])
    post = np.array([0.5, 0.5])
    out = np.full(len(obs), np.nan)
    for i, o in enumerate(obs):
        prior = post @ T
        if np.isfinite(o):
            ll = -0.5 * (o - means) ** 2
            lik = np.exp(ll - ll.max())
            post = prior * lik
            post /= post.sum()
            out[i] = post[1]
        else:
            post = prior
    return pd.Series(out, index=B.index)


def regime_entry_k(p, k_max=20.0):
    """K2 unless bull-leaning; scales linearly to k_max at p=1. Frozen/trade."""
    if not np.isfinite(p):
        return None
    strength = float(np.clip(2.0 * p - 1.0, 0.0, 1.0))
    return 2.0 + (k_max - 2.0) * strength


def allocate_capacity(requests, available):
    requests = np.asarray(requests, dtype=float)
    if requests.sum() <= available:
        return requests.astype(int)
    raw = available * requests / requests.sum()
    base = np.floor(raw).astype(int)
    for j in np.argsort(-(raw - base))[:int(available - base.sum())]:
        base[j] += 1
    return base


def regime_trail_variant_pnl(bars, daily, paper_start, k_max=20.0, cap=3):
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    frames = {s: strategy.signal_frame(bars[s], rg.SCFG) for s in rg.MULT}
    posterior = {s: wonham_bull(bars[s]) for s in rg.MULT}
    signals = {s: frames[s]["signal"].astype(bool) for s in rg.MULT}
    times = bars[list(rg.MULT)[0]].index
    for s in list(rg.MULT)[1:]:
        times = times.union(bars[s].index)
    loc = {s: {ts: i for i, ts in enumerate(bars[s].index)} for s in rg.MULT}
    st8 = {s: dict(pos=0, pending=0, pend_k=2.0, k=2.0, e=np.nan,
                   hw=np.nan, stop=np.nan, ets=None) for s in rg.MULT}
    rows = []
    for stamp in times:
        for s in rg.MULT:
            if (st8[s]["pending"] and stamp in loc[s]
                    and int(bars[s]["roll"].iloc[loc[s][stamp]])):
                st8[s]["pending"] = 0
        pend = [(s, st8[s]["pending"]) for s in rg.MULT
                if st8[s]["pending"] and stamp in loc[s]]
        existing = sum(st8[s]["pos"] for s in rg.MULT)
        grants = (allocate_capacity([q for _, q in pend],
                                    max(cap - existing, 0))
                  if pend else [])
        fills = {s: int(g) for (s, _), g in zip(pend, grants)}
        for s, _ in pend:
            state = st8[s]
            i = loc[s][stamp]
            state["pending"] = 0
            qty = fills.get(s, 0)
            if qty:
                state["pos"] = qty
                state["k"] = state["pend_k"]
                state["e"] = float(bars[s]["open"].iloc[i])
                state["hw"] = state["e"]
                state["stop"] = (state["e"] - state["k"]
                                 * float(frames[s]["atr"].iloc[i - 1]))
                state["ets"] = stamp
                if not np.isfinite(state["stop"]):
                    state["pos"] = 0
        for s in rg.MULT:
            if stamp not in loc[s]:
                continue
            i = loc[s][stamp]
            bar = bars[s].iloc[i]
            state = st8[s]
            if state["pos"] and int(bar["roll"]):
                x = float(bars[s]["close"].iloc[i - 1]) if i else float(bar["open"])
                rows.append((state["ets"], stamp, s, x - state["e"],
                             state["pos"]))
                state["pos"] = 0
            if state["pos"]:
                if float(bar["low"]) <= state["stop"]:
                    x = min(state["stop"], float(bar["open"]))
                    rows.append((state["ets"], stamp, s, x - state["e"],
                                 state["pos"]))
                    state["pos"] = 0
                else:
                    state["hw"] = max(state["hw"], float(bar["high"]))
                    state["stop"] = max(
                        state["stop"], state["hw"] - state["k"]
                        * float(frames[s]["atr"].iloc[i]))
            if (not state["pos"] and not state["pending"]
                    and bool(signals[s].iloc[i])
                    and i > rg.SCFG["donchian_n"]):
                mon = monday_of_week(stamp)
                req = int(W.loc[mon, s]) if mon in W.index else 1
                if req <= 0:
                    continue
                k = regime_entry_k(float(posterior[s].iloc[i]), k_max)
                if k is None:
                    continue
                state["pending"] = req
                state["pend_k"] = k
    T = pd.DataFrame(rows, columns=["entry_time", "exit_time", "sym",
                                    "points", "qty"])
    T["pnl"] = (T["points"] * T["sym"].map(rg.MULT) - rg.FR) * T["qty"]
    global LAST_TRADES
    LAST_TRADES = T
    P = T[T["entry_time"] >= paper_start]
    return (float(T["pnl"].sum()), len(T),
            float(P["pnl"].sum()), len(P))



def override_entry_weights(week_row, fast, threshold=0.02):
    """SHADOW-ONLY decision helper for the slow+fast override variant.

    User-elected 2026-07-21 as a read-only scoreboard line ("its not that
    we will use it but it is definitely nice to have"): keep the weekly
    L35 leader as the structural 3-0-0 allocation, but if another index
    beats the slow leader by >= threshold (2pp) on a ~1-week fast score
    at an ENTRY bar, that entry allocates 1 to the slow leader and 2 to
    the fast leader. Open positions are never touched (weights bind at
    entry by engine construction); unsure weeks keep their 1-1-1.
    Provenance: codex proposal 2026-07-21, reproduced independently
    (+$6.3-6.9k/16yr, ~3-6%% of hours active, bootstrap lower bound only
    +$178 — NOT an adoption candidate; forward record decides).

    week_row: the W-MON weights row (Series). fast: {sym: fast RS}.
    Returns the weights dict to apply to THIS entry.
    """
    if int(week_row.max()) != 3:               # unsure week -> keep 1-1-1
        return {s: int(week_row[s]) for s in week_row.index}
    slow = week_row.idxmax()
    others = {s: fast[s] - fast[slow] for s in week_row.index if s != slow}
    fast_leader, edge = max(others.items(), key=lambda kv: kv[1])
    if edge >= threshold:
        return {s: {slow: 1, fast_leader: 2}.get(s, 0)
                for s in week_row.index}
    return {s: int(week_row[s]) for s in week_row.index}


def override_variant_pnl(bars, daily, paper_start):
    """Replay the slow+fast override design; returns totals + max exposure."""
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    closes = pd.DataFrame({s: bars[s]["close"] for s in rg.MULT}).ffill()

    def fast_scores(ts):
        out = {}
        for s in rg.MULT:
            d1 = daily[s][daily[s].index < ts.normalize()]
            if len(d1) < 6:
                return None
            out[s] = float(closes[s].asof(ts) / d1.iloc[-5] - 1.0)
        return out

    def wq_for(sym):
        def f(ts):
            mon = monday_of_week(ts)
            if mon not in W.index:
                return 1
            fs = fast_scores(ts)
            if fs is None:
                return int(W.loc[mon, sym])
            return override_entry_weights(W.loc[mon], fs)[sym]
        return f

    rows, events = [], []
    for sym in rg.MULT:
        T = rg.simulate_weighted(bars[sym], sym, wq_for(sym))
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        rows.append(T)
        for _, r in T.iterrows():
            events.append((r["entry_time"], int(r["qty"])))
            events.append((r["exit_time"], -int(r["qty"])))
    allT = pd.concat(rows)
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = mx = 0
    for _, q in events:
        cur += q
        mx = max(mx, cur)
    P = allT[allT["entry_time"] >= paper_start]
    global LAST_TRADES
    LAST_TRADES = allT
    return (float(allT["pnl"].sum()), len(allT),
            float(P["pnl"].sum()), len(P), mx)


def bear3_short_trades(B, daily_nq, core_long_nq):
    """SHADOW-ONLY mined-cell overlay — forward scoreboard, NOT a candidate.

    "prod+bear3": causal NQ shorts at 3 contracts under strict core
    priority, added 2026-07-19 by user election as a read-only ledger
    line. Provenance: this is the argmax cell of the CLOSED bear-side
    sweep (bear_momentum_2026_07 — first half 2011-18 ~$0 standalone,
    ~85% of historical profit from the 2022+2025 episodes). Its forward
    record accrues here; nothing in the trading path reads it.

    Mechanics (mirrors the causality-tested research overlay):
      gate   D-1-lagged 5-day NQ return < 0 (daily closes strictly
             before the bar's date — the C1 leak class regression-tested
             in tests/test_shadow_bear3.py)
      entry  close below the prior 20-bar close-low + vol/session gates,
             filled next bar open, only while the replayed core NQ leg
             is flat
      exit   2.0*ATR chandelier from the low watermark, stop-first,
             gap-honest (max(stop, open)); roll bars force-close and
             cancel pendings; a core NQ entry closes the short at that
             bar's open — the long path is never altered.
    """
    f = strategy.signal_frame(B, rg.SCFG)
    gate = ((daily_nq.pct_change(5) < 0).astype(float).shift(1)
            .reindex(B.index.normalize()).fillna(0.0).values > 0)
    dlow = B["close"].rolling(20).min().shift(1)
    sig = ((B["close"] < dlow) & f["vol_ok"] & f["session_ok"]
           & f["atr"].notna()).values & gate
    atr = f["atr"].values
    opens, highs, lows, closes = (
        B[c].values for c in ("open", "high", "low", "close"))
    rolls, ts = B["roll"].values, B.index
    active = np.zeros(len(B), dtype=bool)
    starts = np.zeros(len(B), dtype=bool)
    core = core_long_nq[["entry_time", "exit_time"]].drop_duplicates()
    for tr in core.itertuples(index=False):
        j = ts.searchsorted(tr.entry_time)
        z = ts.searchsorted(tr.exit_time)
        active[j:z] = True
        if j < len(starts):
            starts[j] = True
    rows = []
    pos = pend = 0
    e = st = lw = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend and (rolls[i] or active[i]):
            pend = 0
        if pend:
            pos, pend = 1, 0
            e = lw = opens[i]
            e_ts = ts[i]
            st = e + 2.0 * atr[i - 1]
            if np.isnan(st):
                pos = 0
        if pos and starts[i]:
            rows.append((e_ts, ts[i], e - opens[i], "core_long"))
            pos = 0
        if pos and rolls[i]:
            x = closes[i - 1] if i else opens[i]
            rows.append((e_ts, ts[i], e - x, "roll"))
            pos = 0
        if pos:
            if highs[i] >= st:
                x = max(st, opens[i])
                rows.append((e_ts, ts[i], e - x, "stop"))
                pos = 0
            else:
                lw = min(lw, lows[i])
                st = min(st, lw + 2.0 * atr[i])
        if pos == 0 and pend == 0 and not active[i] and sig[i] and i > 20:
            pend = 1
    return pd.DataFrame(rows, columns=["entry_time", "exit_time",
                                       "points", "reason"])



# ---------- SHADOW-ONLY breadth satellite (codex daysat validation, 08-03) --
# Frozen rule (satellite_lane_2026_08/codex_validation): benched instrument,
# session channel from 09:00 ET, >=2 prior bars, close > session high +
# 0.25*ATR, entry at the next bar open BEFORE 14:00 ET, core must hold (or
# enter at the same bar open), 2.0*ATR trail, flat at the last bar closing by
# 16:00 ET, 1 lot, production vol gate. Backtest of record: +$5,660 / 474
# trades 2011-25 on the ROLL-AWARE caches (pending_followup_summary.json).
FREEZE = {
    "live-config replay (3-0-0)": "2026-07-18",
    "benchmark fixed 1-1-1": "2026-07-18",
    "shadow trail-3": "2026-07-18",
    "shadow q=.975": "2026-07-18",
    "shadow NQ-floor 2+1": "2026-07-18",
    "shadow prod+bear3 (mined)": "2026-07-18",
    "  bear3 leg alone (3x NQ)": "2026-07-18",
    "shadow slow+fast override": "2026-07-21",
    "shadow adaptive-z (v1 core)": "2026-07-21",
    "shadow dynamicK20 regime (c3)": "2026-07-22",
    "shadow ridge filter (v1)": "2026-07-22",
    "shadow ridge+vol (R2)": "2026-07-27",
    "shadow live+satellite (1-lot)": "2026-08-03",
}


def fwd_bracket(label, trades):
    """Since-FREEZE forward record: the only segment of the bracket that is
    genuine frozen-design forward evidence (codex 08-03 audit — the paper
    column replays designs from 07-05 regardless of when each was frozen)."""
    f = pd.Timestamp(FREEZE[label], tz="UTC")
    F = trades[trades["entry_time"] >= f]
    return (f" [fwd {f:%m-%d}: ${float(F['pnl'].sum()):>8,.2f} / {len(F)}]")


def satellite_trades(bars, core_trades):
    """Day-scoped breadth satellite trades on the live bar store."""
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    daily = {s: bars[s]["close"].resample("1D").last().dropna()
             for s in bars}
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    expo = (pd.concat([
        pd.Series(core_trades["qty"].to_numpy(),
                  index=pd.DatetimeIndex(core_trades["entry_time"])),
        pd.Series(-core_trades["qty"].to_numpy(),
                  index=pd.DatetimeIndex(core_trades["exit_time"]))])
        .groupby(level=0).sum().sort_index().cumsum())

    def benched(sym, ts):
        mon = monday_of_week(ts)
        if mon not in W.index:
            return False
        row = W.loc[mon]
        return (int(row[sym]) == 0
                and sum(int(row[s]) == 0 for s in bars) < len(bars))

    out = []
    for sym in bars:
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
                    out.append((e_ts, ts[i], sym, e - x, "stop"))
                    pos = 0
                elif i == last_elig[cur]:
                    out.append((e_ts, ts[i], sym, e - closes[i], "eod"))
                    pos = 0
                else:
                    hw = max(hw, highs[i])
                    st = max(st, hw - 2.0 * atr[i])
            if (pos == 0 and pend == 0 and not done and nbars >= 2
                    and benched(sym, ts[i]) and np.isfinite(atr[i])
                    and vol_ok[i]
                    and closes[i] > shigh + 0.25 * atr[i]
                    and i + 1 < len(B) and eligible[i + 1]
                    and dates[i + 1] == cur and hours[i + 1] < 14):
                loc = expo.index.searchsorted(ts[i + 1], side="right") - 1
                if loc >= 0 and expo.iloc[loc] > 0:
                    pend = 1
                    done = True
            shigh = max(shigh, highs[i])
            nbars += 1
    T = pd.DataFrame(out, columns=["entry_time", "exit_time", "sym",
                                   "neg_points", "reason"])
    T["pnl"] = -T["neg_points"] * T["sym"].map(rg.MULT) - rg.FR
    return T


def nq_floor(row):
    if row["NQ"] >= 3:
        return row
    leader = row.idxmax()
    return pd.Series({s: (2 if s == leader else (1 if s == "NQ" else 0))
                      for s in row.index})


def main():
    store = BarStore(BASE / "state")
    bars, daily = {}, {}
    for sym in ("NQ", "ES", "YM"):
        cols = ["open", "high", "low", "close"] + (
            ["volume"] if "volume" in store.bars(sym).columns else [])
        B = store.bars(sym)[cols].copy()
        B["roll"] = 0          # paper window: no roll yet (first: Sep 18)
        bars[sym] = B
        daily[sym] = B["close"].resample("1D").last().dropna()
    rg.START = min(b.index.min() for b in bars.values())
    base_scfg = dict(rg.SCFG)
    lines = [f"SHADOW REPORT (paper window, corrected FSM) "
             f"{pd.Timestamp.utcnow():%Y-%m-%d %H:%M}Z",
             f"window: {rg.START:%Y-%m-%d} -> "
             f"{max(b.index.max() for b in bars.values()):%Y-%m-%d}"]
    import json
    paper_start = pd.Timestamp(json.load(
        open(BASE / "state" / "state.json")).get("paper_start")
        or "2026-07-05", tz="UTC")
    lines.append(f"paper window since {paper_start:%Y-%m-%d} shown in "
                 "[paper] brackets — RETROACTIVE for designs frozen later")
    lines.append("[fwd <date>] = since each design's freeze commit — the only "
                 "genuine forward evidence (codex 08-03 audit)")
    lines.append("totals span each instrument's stored history (NQ/ES 2010-, "
                 "YM 2016-) — not a standardized window")
    results = {}
    TRADES = {}
    for label, kw in (
            ("live-config replay (3-0-0)", {}),
            ("benchmark fixed 1-1-1", dict(mode="fixed111")),
            ("shadow trail-3", dict(k_trail=3.0)),
            ("shadow q=.975", dict(q=0.975)),
            ("shadow NQ-floor 2+1", dict(weights_override=nq_floor))):
        rg.SCFG = dict(base_scfg)
        total, n, ptotal, pn = variant_pnl(bars, daily,
                                           paper_start=paper_start, **kw)
        results[label] = (total, n, ptotal, pn)
        TRADES[label] = LAST_TRADES
        lines.append(f"{label:<28} total ${total:>9,.2f}  trades {n}  "
                     f"[paper: ${ptotal:>8,.2f} / {pn}]"
                     + fwd_bracket(label, LAST_TRADES))

    # SHADOW-ONLY mined overlay line (see bear3_short_trades provenance):
    # replay the live core's NQ leg, then the causal bear3 shorts under
    # core priority; report combined and leg-alone forward records.
    rg.SCFG = dict(base_scfg)
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    wq = lambda ts: (int(W.loc[monday_of_week(ts), "NQ"])       # noqa: E731
                     if monday_of_week(ts) in W.index else 1)
    core_nq = rg.simulate_weighted(bars["NQ"], "NQ", wq)
    S = bear3_short_trades(bars["NQ"], daily["NQ"], core_nq)
    S["pnl"] = (S["points"] * rg.MULT["NQ"] - rg.FR) * 3
    in_paper = S["entry_time"] >= paper_start
    bear_tot, bear_pt = float(S["pnl"].sum()), float(S.loc[in_paper,
                                                           "pnl"].sum())
    live_tot, live_n, live_pt, live_pn = results["live-config replay (3-0-0)"]
    comb = pd.concat([TRADES["live-config replay (3-0-0)"],
                      S.assign(qty=3)])
    lines.append(f"{'shadow prod+bear3 (mined)':<28} total "
                 f"${live_tot + bear_tot:>9,.2f}  trades {live_n + len(S)}  "
                 f"[paper: ${live_pt + bear_pt:>8,.2f} / "
                 f"{live_pn + int(in_paper.sum())}]"
                 + fwd_bracket("shadow prod+bear3 (mined)", comb))
    lines.append(f"{'  bear3 leg alone (3x NQ)':<28} total "
                 f"${bear_tot:>9,.2f}  trades {len(S)}  "
                 f"[paper: ${bear_pt:>8,.2f} / {int(in_paper.sum())}]"
                 + fwd_bracket("  bear3 leg alone (3x NQ)", S))

    # SHADOW-ONLY slow+fast override (see override_entry_weights provenance)
    rg.SCFG = dict(base_scfg)
    o_tot, o_n, o_pt, o_pn, o_mx = override_variant_pnl(bars, daily,
                                                        paper_start)
    lines.append(f"{'shadow slow+fast override':<28} total ${o_tot:>9,.2f}  "
                 f"trades {o_n}  [paper: ${o_pt:>8,.2f} / {o_pn}]"
                 + fwd_bracket("shadow slow+fast override", LAST_TRADES)
                 + f"  maxExposure {o_mx} contracts")

    # SHADOW-ONLY adaptive-rotation challengers (see adaptive_policy
    # provenance): line 1 isolates the allocation hypothesis on the v1
    # core; line 2 is the profit-first D15/K7 family at honest exposure.
    rg.SCFG = dict(base_scfg)
    a_tot, a_n, a_pt, a_pn, a_mx = adaptive_variant_pnl(bars, daily,
                                                        paper_start)
    lines.append(f"{'shadow adaptive-z (v1 core)':<28} total ${a_tot:>9,.2f}  "
                 f"trades {a_n}  [paper: ${a_pt:>8,.2f} / {a_pn}]"
                 + fwd_bracket("shadow adaptive-z (v1 core)", LAST_TRADES)
                 + f"  maxExposure {a_mx} contracts")
    # D15/K7 adaptive DEMOTED to archived reference 2026-07-22 (research
    # artifacts in adaptive_rotation_2026_07/); seat taken by dynamic K20.
    rg.SCFG = dict(base_scfg)
    g_tot, g_n, g_pt, g_pn = regime_trail_variant_pnl(bars, daily,
                                                      paper_start)
    lines.append(f"{'shadow dynamicK20 regime (c3)':<28} total ${g_tot:>9,.2f}  "
                 f"trades {g_n}  [paper: ${g_pt:>8,.2f} / {g_pn}]"
                 + fwd_bracket("shadow dynamicK20 regime (c3)", LAST_TRADES))

    # SHADOW-ONLY ridge entry filter (see port provenance above)
    rg.SCFG = dict(base_scfg)
    r_tot, r_n, r_pt, r_pn, _shared = ridge_variant_pnl(bars, daily,
                                                        paper_start)
    lines.append(f"{'shadow ridge filter (v1)':<28} total ${r_tot:>9,.2f}  "
                 f"trades {r_n}  [paper: ${r_pt:>8,.2f} / {r_pn}]"
                 + fwd_bracket("shadow ridge filter (v1)", LAST_TRADES))
    rg.SCFG = dict(base_scfg)
    r2_tot, r2_n, r2_pt, r2_pn, _ = ridge_variant_pnl(
        bars, daily, paper_start, fnames=RIDGE_FEATURES_V2, shared=_shared)
    lines.append(f"{'shadow ridge+vol (R2)':<28} total ${r2_tot:>9,.2f}  "
                 f"trades {r2_n}  [paper: ${r2_pt:>8,.2f} / {r2_pn}]"
                 + fwd_bracket("shadow ridge+vol (R2)", LAST_TRADES))

    # SHADOW-ONLY breadth satellite (see provenance above the engine).
    # [retro] = counterfactual replay of the pre-freeze paper window; the
    # design did not exist then. [fwd] starts at $0/0 on the 08-03 freeze.
    ST = satellite_trades(bars, TRADES["live-config replay (3-0-0)"])
    sp = ST[ST["entry_time"] >= paper_start]
    lines.append(f"{'shadow live+satellite (1-lot)':<28} backtest +$5,660 "
                 f"(roll-aware 2011-25)  "
                 f"[retro: ${float(sp['pnl'].sum()):>8,.2f} / {len(sp)}]"
                 + fwd_bracket("shadow live+satellite (1-lot)", ST))
    out = "\n".join(lines)
    (BASE / "state" / "shadow_report.txt").write_text(out)
    print(out)


if __name__ == "__main__":
    main()
