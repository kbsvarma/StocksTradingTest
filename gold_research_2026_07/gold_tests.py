"""Gold hypothesis tests G1-G4 per PREREGISTRATION_GOLD.md.
IS results printed for everything; OOS printed ONLY when this script is run with
--oos after survivors are locked (single shot).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "event_edge_research"))
from stats_tools import nw_tstat, ann_sharpe  # my validated helpers

ET = "America/New_York"
TICK = 0.10          # $/oz; $1 per tick per MGC (10oz)
MULT = 10.0          # MGC $ per 1.0 price point
FRICTION_RT = 3.00   # $1 comm RT + 1 tick/side slip (locked)

IS_END = pd.Timestamp("2022-12-31", tz=ET)
OOS_START = pd.Timestamp("2023-01-01", tz=ET)

def load_1m():
    a = pd.read_csv(Path("../spx_spread_bot/backtests/gold_london/data/mgc_1m.csv"), index_col=0)
    a.index = pd.to_datetime(a.index, utc=True).tz_convert(ET)
    b = pd.read_parquet("data/mgc_1m_topup_2026.parquet")
    b = b.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    b.index = b.index.tz_convert(ET)
    df = pd.concat([a[["open", "high", "low", "close", "volume"]], b]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df

def split(s):
    return s[s.index <= IS_END], s[s.index >= OOS_START]

def rep(label, s, tpy):
    s = s.dropna()
    if len(s) < 10:
        print(f"  {label:<34} n={len(s)} (insufficient)")
        return
    t, n = nw_tstat(s.values)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    sh = ann_sharpe(s.values, periods_per_year=tpy)
    print(f"  {label:<34} n={n:<5} total=${s.sum():+9.0f}  mean=${s.mean():+7.2f}  "
          f"t={t:+5.2f}  Sh={sh:+5.2f}  %pos={(s > 0).mean() * 100:3.0f}  worst=${s.min():+7.0f}")

# ---------- G1: London-close fade ----------
def g1_trades(df, target_t=20, stop_t=10, min_range_t=20):
    rows = {}
    for day, sess in df.groupby(df.index.date):
        morning = sess.between_time("08:00", "11:50")
        noon = sess.between_time("12:00", "12:00")
        post = sess.between_time("12:01", "13:30")
        if morning.empty or noon.empty or post.empty:
            continue
        m_open = float(morning.iloc[0]["open"]); m_close = float(morning.iloc[-1]["close"])
        rng_t = abs(round((m_close - m_open) / TICK))
        if rng_t < min_range_t:
            continue
        trend = 1 if m_close > m_open else -1
        d = -trend
        entry = float(noon.iloc[-1]["close"])
        tgt = entry + d * target_t * TICK
        stp = entry - d * stop_t * TICK
        exit_p, reason = float(post.iloc[-1]["close"]), "TIME"
        for _, bar in post.iterrows():
            lo, hi = float(bar["low"]), float(bar["high"])
            if d == 1:
                if lo <= stp: exit_p, reason = stp, "STOP"; break
                if hi >= tgt: exit_p, reason = tgt, "TARGET"; break
            else:
                if hi >= stp: exit_p, reason = stp, "STOP"; break
                if lo <= tgt: exit_p, reason = tgt, "TARGET"; break
        pnl = d * (exit_p - entry) * MULT - FRICTION_RT
        rows[pd.Timestamp(day, tz=ET)] = pnl
    return pd.Series(rows).sort_index()

def g1(df, oos=False):
    print("\n=== G1 London-close fade (locked 20/10/20) — CONTAMINATION DISCLOSED ===")
    tr = g1_trades(df)
    is_p, oos_p = split(tr)
    rep("IS 2019-2022", is_p, tpy=90)
    print("  IS neighborhood smoothness (IS-only diagnostic):")
    for tgt in (15, 20, 25):
        line = []
        for stp in (5, 10, 15):
            for mr in (15, 20, 25):
                if (tgt, stp, mr) == (20, 10, 20):
                    continue
        pass
    grid = {}
    for tgt in (15, 20, 25):
        for stp in (5, 10, 15):
            for mr in (15, 20, 25):
                g_is, _ = split(g1_trades(df, tgt, stp, mr))
                grid[(tgt, stp, mr)] = g_is.sum()
    pos = sum(1 for v in grid.values() if v > 0)
    print(f"    neighborhood cells IS-positive: {pos}/{len(grid)}")
    worst3 = sorted(grid.items(), key=lambda kv: kv[1])[:3]
    best3 = sorted(grid.items(), key=lambda kv: kv[1], reverse=True)[:3]
    print("    best:", [(k, round(v)) for k, v in best3])
    print("    worst:", [(k, round(v)) for k, v in worst3])
    if oos:
        rep("OOS 2023-2026 *single shot*", oos_p, tpy=110)
        unseen = tr[tr.index > pd.Timestamp("2026-05-05", tz=ET)]
        rep("truly-unseen (post 2026-05-05)", unseen, tpy=110)

# ---------- G2: session blocks ----------
def block_returns(df):
    """Per CME day: close-to-close $ move of each block on 1 MGC, net of friction."""
    c = df["close"]
    # anchor times; use asof lookups on the minute index
    out = {"asia": {}, "london": {}, "ny": {}, "late": {}}
    days = pd.DatetimeIndex(sorted(set(df.index.normalize())))
    ix = df.index
    def px_at(ts):
        i = ix.searchsorted(ts, side="right") - 1
        if i < 0: return np.nan
        if (ts - ix[i]).total_seconds() > 20 * 60: return np.nan  # >20min stale = no quote
        return float(c.iloc[i])
    for d in days:
        a0 = px_at(d - pd.Timedelta(hours=6))          # 18:00 prev day
        a1 = px_at(d + pd.Timedelta(hours=3))          # 03:00
        l1 = px_at(d + pd.Timedelta(hours=8, minutes=20))
        n1 = px_at(d + pd.Timedelta(hours=13, minutes=30))
        x1 = px_at(d + pd.Timedelta(hours=17))
        if not any(np.isnan(v) for v in (a0, a1)):
            out["asia"][d] = (a1 - a0) * MULT - FRICTION_RT
        if not any(np.isnan(v) for v in (a1, l1)):
            out["london"][d] = (l1 - a1) * MULT - FRICTION_RT
        if not any(np.isnan(v) for v in (l1, n1)):
            out["ny"][d] = (n1 - l1) * MULT - FRICTION_RT
        if not any(np.isnan(v) for v in (n1, x1)):
            out["late"][d] = (x1 - n1) * MULT - FRICTION_RT
    return {k: pd.Series(v).sort_index() for k, v in out.items()}

def g2(df, oos=False):
    print("\n=== G2 session blocks (predictions: asia LONG +, london SHORT +) ===")
    blocks = block_returns(df)
    for name, pred in (("asia", +1), ("london", -1), ("ny", 0), ("late", 0)):
        s = blocks[name] * (pred if pred != 0 else 1)
        is_p, oos_p = split(s)
        tag = {1: "LONG", -1: "SHORT(pnl)", 0: "measured-long"}[pred]
        rep(f"IS {name} [{tag}]", is_p, tpy=250)
        if oos and pred != 0:
            rep(f"OOS {name} [{tag}] *single shot*", oos_p, tpy=250)

# ---------- G3: fix windows ----------
def g3(df, oos=False):
    print("\n=== G3 PM-fix windows (pred: 09:30->10:00 neg; 10:00->10:30 nonneg) ===")
    c = df["close"]; ix = df.index
    def px_at(ts):
        i = ix.searchsorted(ts, side="right") - 1
        if i < 0: return np.nan
        if (ts - ix[i]).total_seconds() > 10 * 60: return np.nan
        return float(c.iloc[i])
    days = pd.DatetimeIndex(sorted(set(df.index.normalize())))
    w1, w2 = {}, {}
    for d in days:
        p0 = px_at(d + pd.Timedelta(hours=9, minutes=30))
        p1 = px_at(d + pd.Timedelta(hours=10))
        p2 = px_at(d + pd.Timedelta(hours=10, minutes=30))
        if not (np.isnan(p0) or np.isnan(p1)):
            w1[d] = -(p1 - p0) * MULT - FRICTION_RT   # SHORT into fix
        if not (np.isnan(p1) or np.isnan(p2)):
            w2[d] = (p2 - p1) * MULT - FRICTION_RT    # LONG after fix
    for label, s in (("into-fix SHORT", pd.Series(w1)), ("post-fix LONG", pd.Series(w2))):
        is_p, oos_p = split(s.sort_index())
        rep(f"IS {label}", is_p, tpy=250)
        if oos:
            rep(f"OOS {label} *single shot*", oos_p, tpy=250)

# ---------- G4: 12-1 momentum on GC daily ----------
def g4(oos=False):
    print("\n=== G4 gold 12-1 momentum (GC daily 2010-2026, long/flat monthly) ===")
    gc = pd.read_parquet("data/gc_1d_2010_2026.parquet")
    gc = gc.rename(columns=str.lower)
    px = gc["close"]
    iid = gc["instrument_id"]
    ret = px.pct_change()
    ret[iid != iid.shift()] = np.nan  # exclude roll transitions
    m_ret = (1 + ret.fillna(0)).groupby(px.index.to_period("M")).prod() - 1
    mom = (1 + m_ret).rolling(12).apply(np.prod, raw=True) / (1 + m_ret) - 1  # 12m excl last
    sig = (mom.shift(1) > 0).astype(float)  # decided at month start
    strat = sig * m_ret
    # $ on 1 MGC: monthly ret × avg notional that month
    notional = px.groupby(px.index.to_period("M")).mean() * MULT
    pnl = strat * notional - (sig.diff().abs().fillna(0) * FRICTION_RT / 2)
    pnl.index = pnl.index.to_timestamp().tz_localize(ET)
    is_p = pnl[pnl.index <= pd.Timestamp("2020-12-31", tz=ET)].dropna()
    oos_p = pnl[pnl.index >= pd.Timestamp("2021-01-01", tz=ET)].dropna()
    rep("IS 2010-2020 (monthly $)", is_p, tpy=12)
    if oos:
        rep("OOS 2021-2026 *single shot*", oos_p, tpy=12)

if __name__ == "__main__":
    oos = "--oos" in sys.argv
    df = load_1m()
    print(f"MGC 1-min merged: {len(df):,} rows {df.index[0]} -> {df.index[-1]}")
    g1(df, oos); g2(df, oos); g3(df, oos); g4(oos)
