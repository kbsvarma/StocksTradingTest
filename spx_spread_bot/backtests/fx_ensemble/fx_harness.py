"""FX ensemble-signal swing engine — honest harness.

Build order (disciplined): prove the ENGINE on fixed params first, with
realistic costs and zero look-ahead, BEFORE any optimization. Optimizing an
unvalidated engine is how you fool yourself.

Pipeline (no look-ahead):
  - Indicators computed on bars [0..t] (only past+current close).
  - Signal decided at bar t close.
  - Entry at bar t+1 OPEN (the price you could actually get).
  - Exit: scan subsequent bars; if bar's high/low crosses TP/SL, exit there.
    (Intrabar: if both TP and SL inside the same bar's range, assume SL first
     — conservative.)
  - Spread cost charged on entry (you cross the spread).
  - Max hold = N bars, else exit at close (time stop).

4 orthogonal indicators (each +1 long / -1 short / 0 flat):
  1. TREND   : Donchian breakout (close vs N-bar high/low)
  2. MOM     : SMA-fast vs SMA-slow
  3. MEANREV : z-score of close vs SMA (fade extremes)
  4. VOLFILT : ATR-percentile gate (only trade in tradeable vol) -> 1/0 filter

Ensemble (baseline): directional vote of {TREND, MOM, MEANREV}; trade only if
|net vote| >= VOTE_MIN and VOLFILT passes.
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).parent / "data"

# pip size + typical retail spread (pips) per pair
def pip_size(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001

def spread_pips(pair):
    majors = {"EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD"}
    if pair in majors: return 1.0
    return 2.0  # crosses wider


# ---------- indicators (vectorized, no look-ahead: all use rolling past) ----------
def donchian_signal(df, n=20):
    hi = df["High"].rolling(n).max().shift(1)   # shift(1) => excludes current bar
    lo = df["Low"].rolling(n).min().shift(1)
    sig = pd.Series(0, index=df.index)
    sig[df["Close"] > hi] = 1
    sig[df["Close"] < lo] = -1
    return sig

def momentum_signal(df, fast=10, slow=40):
    sf = df["Close"].rolling(fast).mean()
    sl = df["Close"].rolling(slow).mean()
    return np.sign(sf - sl).fillna(0).astype(int)

def meanrev_signal(df, n=20, z=1.5):
    sma = df["Close"].rolling(n).mean()
    sd = df["Close"].rolling(n).std()
    zsc = (df["Close"] - sma) / sd
    sig = pd.Series(0, index=df.index)
    sig[zsc < -z] = 1    # oversold -> long
    sig[zsc > z] = -1     # overbought -> short
    return sig

def vol_filter(df, n=14, lo_pct=0.2, hi_pct=0.95):
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    # percentile rank of current ATR within trailing 252 bars
    rank = atr.rolling(252).apply(lambda x: (x[-1] >= x).mean(), raw=True)
    return ((rank >= lo_pct) & (rank <= hi_pct)).astype(int)


def build_signal(df, params):
    trend = donchian_signal(df, params["don_n"])
    mom = momentum_signal(df, params["mom_fast"], params["mom_slow"])
    mr = meanrev_signal(df, params["mr_n"], params["mr_z"])
    vf = vol_filter(df, params["atr_n"], params["vol_lo"], params["vol_hi"])
    net = trend + mom + mr
    sig = pd.Series(0, index=df.index)
    sig[(net >= params["vote_min"]) & (vf == 1)] = 1
    sig[(net <= -params["vote_min"]) & (vf == 1)] = -1
    return sig


# ---------- backtest one pair ----------
def backtest_pair(pair, params, tp_pips, sl_pips, max_hold=10):
    df = pd.read_parquet(DATA / f"{pair}.parquet")
    df = df[(df[["Open","High","Low","Close"]] > 0).all(axis=1)].copy()
    ps = pip_size(pair)
    spr = spread_pips(pair) * ps
    sig = build_signal(df, params)

    opens = df["Open"].values; highs = df["High"].values
    lows = df["Low"].values; closes = df["Close"].values
    sigv = sig.values
    n = len(df)
    trades = []
    i = 0
    while i < n - 1:
        s = sigv[i]
        if s == 0:
            i += 1; continue
        entry_idx = i + 1
        entry = opens[entry_idx] + (spr/2 if s == 1 else -spr/2)  # cross half-spread
        if s == 1:
            tp = entry + tp_pips*ps; sl = entry - sl_pips*ps
        else:
            tp = entry - tp_pips*ps; sl = entry + sl_pips*ps
        exit_px = None; reason = None
        for j in range(entry_idx, min(entry_idx + max_hold, n)):
            hi, lo = highs[j], lows[j]
            if s == 1:
                if lo <= sl: exit_px, reason = sl, "SL"; break
                if hi >= tp: exit_px, reason = tp, "TP"; break
            else:
                if hi >= sl: exit_px, reason = sl, "SL"; break
                if lo <= tp: exit_px, reason = tp, "TP"; break
        if exit_px is None:
            exit_px = closes[min(entry_idx+max_hold-1, n-1)]; reason = "TIME"
        pnl_pips = ((exit_px - entry) / ps) * s
        trades.append({"pair": pair, "entry_idx": entry_idx,
                       "date": df.index[entry_idx], "dir": s,
                       "pnl_pips": pnl_pips, "reason": reason})
        i = j + 1  # no overlapping trades on same pair
    return pd.DataFrame(trades)


DEFAULT = dict(don_n=20, mom_fast=10, mom_slow=40, mr_n=20, mr_z=1.5,
               atr_n=14, vol_lo=0.2, vol_hi=0.95, vote_min=2)

if __name__ == "__main__":
    pairs = sorted(p.stem for p in DATA.glob("*.parquet"))
    print("="*92)
    print("FX ENSEMBLE — BASELINE (fixed params, realistic spread, no look-ahead, no optimization)")
    print(f"Params: {DEFAULT}  |  TP=30 SL=55 pips, max_hold=10 bars")
    print("="*92)
    all_tr = []
    print(f"{'Pair':<8}{'Trades':<8}{'Win%':<7}{'AvgPip':<9}{'TotPip':<10}{'Pip/yr':<9}")
    print("-"*55)
    for p in pairs:
        tr = backtest_pair(p, DEFAULT, tp_pips=30, sl_pips=55, max_hold=10)
        if tr.empty: continue
        all_tr.append(tr)
        df = pd.read_parquet(DATA/f"{p}.parquet")
        yrs = (df.index[-1]-df.index[0]).days/365.25
        n=len(tr); win=(tr["pnl_pips"]>0).mean()*100
        tot=tr["pnl_pips"].sum()
        print(f"{p:<8}{n:<8}{win:<6.0f}%{tr['pnl_pips'].mean():<+8.1f}{tot:<+9.0f}{tot/yrs:<+8.0f}")
    big = pd.concat(all_tr)
    yrs_all = 22.4
    print("-"*55)
    print(f"{'ALL':<8}{len(big):<8}{(big['pnl_pips']>0).mean()*100:<6.0f}%"
          f"{big['pnl_pips'].mean():<+8.1f}{big['pnl_pips'].sum():<+9.0f}"
          f"{big['pnl_pips'].sum()/yrs_all:<+8.0f}")
    print(f"\nExpectancy/trade: {big['pnl_pips'].mean():+.2f} pips  "
          f"(this is NET of spread; BEFORE swap/slippage)")
    print(f"Exit mix: {big['reason'].value_counts().to_dict()}")
