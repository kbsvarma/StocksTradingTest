"""Time-series momentum (trend-following) on a metals+energy futures basket.

HONEST construction:
  - Roll gaps removed: daily return set to 0 on days where instrument_id changes
    (the front-month rolled to a new contract; the price-level jump is NOT a real P&L).
  - Gap-free synthetic price index built from clean returns -> used for BOTH the
    trend signal and P&L, so accumulated roll gaps can't distort the 12-month lookback.
  - Vol-scaled: each market targeted to 10% annualised vol via trailing-60d realised
    vol, so natgas/crude don't swamp gold. Equal risk, not equal notional.
  - Signal lagged 1 day (position.shift(1)) -> no look-ahead.

Strategies compared on the SAME data:
  A. TSMOM-252 : long if 12-month synth return > 0, short if < 0
  B. MA-200    : long if price > 200d MA, short if below
  C. Long-only : always +1 (buy & hold the vol-scaled basket) -- the benchmark
"""
import numpy as np, pandas as pd
from pathlib import Path

DF = pd.read_parquet(Path(__file__).parent / "metals_energy_1d.parquet")
NAMES = {"GC.c.0":"Gold","SI.c.0":"Silver","HG.c.0":"Copper","CL.c.0":"Crude","NG.c.0":"NatGas"}
TARGET_VOL = 0.10            # 10% annual per market
VOL_WIN = 60                 # trailing realised-vol window (days)
ANN = 252

def clean_market(g):
    g = g.sort_index()
    ret = g["close"].pct_change()
    roll = g["instrument_id"].diff().fillna(0) != 0   # contract changed -> roll day
    ret = ret.where(~roll, 0.0)                       # kill the roll-gap pseudo-return
    ret = ret.clip(-0.5, 0.5)                          # guard any residual bad print
    synth = (1 + ret).cumprod()                        # gap-free price index
    return pd.DataFrame({"ret": ret, "synth": synth})

def vol_scale(ret):
    rv = ret.rolling(VOL_WIN).std() * np.sqrt(ANN)
    w = (TARGET_VOL / rv).clip(upper=5.0)              # cap leverage at 5x
    return w.shift(1)                                  # size set yesterday, applied today

def signal(m, kind):
    s = m["synth"]
    if kind == "tsmom":
        sig = np.sign(s / s.shift(ANN) - 1.0)          # 12-month momentum sign
    elif kind == "ma":
        sig = np.where(s > s.rolling(200).mean(), 1.0, -1.0)
        sig = pd.Series(sig, index=s.index)
    elif kind == "long":
        sig = pd.Series(1.0, index=s.index)
    return sig.shift(1)                                # lag -> no look-ahead

def stats(r, label):
    r = r.dropna()
    if len(r) < 50: print(f"  {label}: too few obs"); return
    ann_ret = r.mean() * ANN
    ann_vol = r.std() * np.sqrt(ANN)
    sharpe = ann_ret / ann_vol if ann_vol else 0
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sk = r.skew()
    print(f"  {label:<10} ret {ann_ret*100:+5.1f}%  vol {ann_vol*100:4.1f}%  "
          f"Sharpe {sharpe:+.2f}  skew {sk:+.2f}  maxDD {dd*100:5.1f}%")
    return r

def basket(kind):
    cols = {}
    for sym, g in DF.groupby("symbol"):
        m = clean_market(g)
        sig = signal(m, kind)
        w = vol_scale(m["ret"])
        cols[sym] = (sig * w * m["ret"])               # vol-scaled signed daily P&L
    strat = pd.DataFrame(cols)
    return strat.mean(axis=1)                           # equal-weight the 5 markets

if __name__ == "__main__":
    print("="*78)
    print("FUTURES TREND-FOLLOWING · metals+energy · 2010-2026 · 10% vol-target/market")
    print("Roll gaps removed via instrument_id; signal lagged 1d (no look-ahead)")
    print("="*78)

    print("\nPER-MARKET (TSMOM-252, vol-scaled):")
    for sym, g in DF.groupby("symbol"):
        m = clean_market(g)
        r = signal(m,"tsmom") * vol_scale(m["ret"]) * m["ret"]
        stats(r, NAMES[sym])

    print("\nBASKET (equal-weight 5 markets):")
    rs = {}
    for kind,lbl in [("tsmom","TSMOM-252"),("ma","MA-200"),("long","Long-only")]:
        rs[lbl] = stats(basket(kind), lbl)

    print("\nTSMOM-252 basket — year by year:")
    r = rs["TSMOM-252"]
    for y,gy in r.groupby(r.index.year):
        eq=(1+gy).cumprod(); dd=(eq/eq.cummax()-1).min()
        print(f"    {y}: ret {gy.sum()*100:+6.1f}%  maxDD {dd*100:5.1f}%")
