"""TSMOM tests per PREREGISTRATION_TSMOM.md.
Paper portfolio (vol-weighted, monthly) + integer-micro implementability layer.
IS printed always; OOS only with --oos (single shot after survivor lock).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "event_edge_research"))
from stats_tools import nw_tstat

ET = "America/New_York"
IS_END = pd.Period("2020-12", freq="M")
OOS_START = pd.Period("2021-01", freq="M")

# contract specs: full-size multiplier ($ per 1.0 price move), tick size, tick $,
# per-side commission, micro (name, ratio to full) or None
SPECS = {
    "ES":  dict(mult=50, tick=0.25, comm=1.50, micro=("MES", 10)),
    "NQ":  dict(mult=20, tick=0.25, comm=1.50, micro=("MNQ", 10)),
    "RTY": dict(mult=50, tick=0.10, comm=1.50, micro=("M2K", 10)),
    "ZN":  dict(mult=1000, tick=1/64, comm=1.50, micro=None),
    "ZF":  dict(mult=1000, tick=1/128, comm=1.50, micro=None),
    "GC":  dict(mult=100, tick=0.10, comm=1.50, micro=("MGC", 10)),
    "SI":  dict(mult=5000, tick=0.005, comm=1.50, micro=("SIL", 5)),
    "HG":  dict(mult=25000, tick=0.0005, comm=1.50, micro=None),
    "CL":  dict(mult=1000, tick=0.01, comm=1.50, micro=("MCL", 10)),
    "NG":  dict(mult=10000, tick=0.001, comm=1.50, micro=("QG", 4)),
    "6E":  dict(mult=125000, tick=0.00005, comm=1.50, micro=("M6E", 10)),
    "6J":  dict(mult=12500000, tick=0.0000005, comm=1.50, micro=None),
    "6B":  dict(mult=62500, tick=0.0001, comm=1.50, micro=None),
    "6A":  dict(mult=100000, tick=0.0001, comm=1.50, micro=None),
}

def load():
    df = pd.read_parquet("data/basket_1d_2010_2026.parquet")
    df = df.rename(columns=str.lower)
    df["sym"] = df["symbol"].str.replace(r"\.v\.0", "", regex=True)
    out = {}
    excl = {}
    for sym, g in df.groupby("sym"):
        g = g.sort_index()
        px = g["close"]
        ret = px.pct_change()
        roll = g["instrument_id"] != g["instrument_id"].shift()
        excl[sym] = int((roll & ret.notna()).sum())
        ret[roll] = np.nan
        out[sym] = pd.DataFrame({"px": px, "ret": ret})
    print("roll-day returns excluded per asset:", excl)
    return out

def monthly_panel(data):
    rets, vols, pxs = {}, {}, {}
    for sym, d in data.items():
        r = d["ret"]
        m = (1 + r.fillna(0)).groupby(r.index.to_period("M")).prod() - 1
        rets[sym] = m
        dv = r.rolling(60, min_periods=40).std() * np.sqrt(252)
        vols[sym] = dv.groupby(dv.index.to_period("M")).last()
        pxs[sym] = d["px"].groupby(d["px"].index.to_period("M")).last()
    return pd.DataFrame(rets), pd.DataFrame(vols), pd.DataFrame(pxs)

def signal(mrets, lookback, skip_last):
    if skip_last:
        cum = (1 + mrets).rolling(lookback).apply(np.prod, raw=True) / (1 + mrets) - 1
    else:
        cum = (1 + mrets).rolling(lookback).apply(np.prod, raw=True) - 1
    return np.sign(cum).shift(1)  # decided at prior month-end

def pct_cost(sym, px):
    s = SPECS[sym]
    notional = px * s["mult"]
    per_side = s["comm"] + 0.5 * s["tick"] * s["mult"]
    return per_side / notional  # fractional cost per side

def paper_portfolio(mrets, vols, pxs, sig, long_flat=False):
    """Equal-risk paper portfolio: asset leg = sig * (0.10 / vol) * mret, avg across
    assets with valid sig+vol. Net of % friction on position changes."""
    legs, costs = {}, {}
    for sym in mrets.columns:
        s = sig[sym]
        if long_flat:
            s = s.clip(lower=0)
        lev = (0.10 / vols[sym]).clip(upper=4.0)
        pos = s * lev
        gross = pos * mrets[sym]
        turn = pos.diff().abs()
        c = turn * pct_cost(sym, pxs[sym]) * 2  # RT on changed exposure
        legs[sym] = gross - c
        costs[sym] = c
    L = pd.DataFrame(legs)
    port = L.mean(axis=1)
    return port, L

def rep(label, s, tpy=12):
    s = s.dropna()
    if len(s) < 12:
        print(f"  {label:<40} n={len(s)} insufficient"); return None
    t, n = nw_tstat(s.values, lags=3)
    sh = s.mean() / s.std() * np.sqrt(tpy) if s.std() > 0 else np.nan
    ann = s.mean() * tpy
    print(f"  {label:<40} n={n:<4} ann={ann*100:+6.2f}%  t={t:+5.2f}  Sh={sh:+5.2f}  "
          f"%pos={(s>0).mean()*100:3.0f}  worst={s.min()*100:+.1f}%")
    return dict(t=t, sh=sh, ann=ann)

def split_m(s):
    return s[s.index <= IS_END], s[s.index >= OOS_START]

def main(oos=False):
    data = load()
    mrets, vols, pxs = monthly_panel(data)
    print(f"panel: {mrets.shape[0]} months x {mrets.shape[1]} assets")

    print("\n=== PRIMARY: TSMOM 12-1 long/short vol-weighted (NET) ===")
    sig = signal(mrets, 12, skip_last=True)
    port, legs = paper_portfolio(mrets, vols, pxs, sig)
    is_p, oos_p = split_m(port)
    rep("IS 2010-2020", is_p)
    # drop-worst-year
    yr = is_p.groupby(is_p.index.year).mean()
    worst = yr.idxmin()
    rep(f"IS drop-worst-year ({worst})", is_p[is_p.index.year != worst])
    # breadth: per-asset IS sign
    pos_assets = sum(1 for sym in legs.columns
                     if legs[sym][legs[sym].index <= IS_END].mean() > 0)
    print(f"  IS breadth: {pos_assets}/{len(legs.columns)} assets positive (gate >=8)")
    if oos:
        rep("OOS 2021-2026 *single shot*", oos_p)

    print("\n=== SECONDARY GRID (IS-only) ===")
    for lb, skip in ((3, False), (6, False), (12, True)):
        for lf in (False, True):
            sg = signal(mrets, lb, skip)
            p, _ = paper_portfolio(mrets, vols, pxs, sg, long_flat=lf)
            is_g, _ = split_m(p)
            rep(f"IS lb={lb}{'-1' if skip else ''} {'long/flat' if lf else 'long/short'}", is_g)

if __name__ == "__main__":
    main("--oos" in sys.argv)
