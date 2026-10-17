"""Silver fair trial per PREREG_SILVER_ETF.md — standalone cells + sleeve test.

SIL micro economics: $1,000/point (1,000 oz), friction $7.00 RT.
Engine: identical to the locked config (engine2_cleanroom semantics).
"""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent / "trend_bot"))
sys.path.insert(0, str(BASE.parent / "trend_bot/research"))

MULT = 1000.0
FR = 7.00
END22 = pd.Timestamp("2022-12-31", tz="UTC")


def load_si_60m():
    files = sorted(glob.glob(str(BASE / "data/SI_1m_*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["roll_flag"] = (df["instrument_id"] != df["instrument_id"].shift()).astype(int)
    g = df.resample("60min", label="left")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last(),
                      "roll": g["roll_flag"].max()}).dropna(subset=["close"])
    B["roll"] = B["roll"].fillna(0).astype(int)
    return B


def run_cell(B, N, K):
    pc = B["close"].shift(1)
    tr_ = pd.concat([B["high"] - B["low"], (B["high"] - pc).abs(),
                     (B["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr_.rolling(20).mean()
    donch = B["close"].rolling(N).max().shift(1)
    ap = atr / B["close"]
    th = ap.rolling(24 * 365, min_periods=24 * 60).quantile(0.95)
    hours = B.index.tz_convert("America/New_York").hour
    sig = ((B["close"] > donch) & ~(ap > th)
           & ~((hours >= 2) & (hours < 6)) & atr.notna()).values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R, A, ts = B["roll"].values, atr.values, B.index
    trades = []
    pos = pend = 0
    e = hw = st = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend:
            pos, pend = 1, 0
            e, hw = O[i], O[i]
            st = e - K * A[i - 1]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos and R[i] == 1:
            x = C[i - 1] if i > 0 else O[i]
            trades.append((e_ts, ts[i], (x - e) * MULT - FR, "roll"))
            pos = 0
        if pos:
            if L[i] <= st:
                x = min(st, O[i])
                trades.append((e_ts, ts[i], (x - e) * MULT - FR, "stop"))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - K * A[i])
        if pos == 0 and pend == 0 and sig[i] and i > N:
            pend = 1
    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "pnl", "reason"])


def main():
    B = load_si_60m()
    print(f"SI 60m bars: {len(B):,}  {B.index[0].date()} -> {B.index[-1].date()}  "
          f"rolls: {int(B['roll'].sum())}")
    print(f"\n{'cell':<10}{'n':>6}{'total':>12}{'train<=22':>12}{'test>22':>12}"
          f"{'worst-yr':>16}")
    results = {}
    for N in (20, 55):
        for K in (2.0, 3.0):
            T = run_cell(B, N, K)
            p = pd.Series(T["pnl"].values, index=pd.DatetimeIndex(T["exit_time"]))
            yearly = p.groupby(p.index.year).sum()
            tr, te = p[p.index <= END22].sum(), p[p.index > END22].sum()
            results[(N, K)] = dict(p=p, yearly=yearly)
            print(f"N{N} K{K:<5}{len(T):>6}{p.sum():>12,.0f}{tr:>12,.0f}"
                  f"{te:>12,.0f}{yearly.min():>12,.0f} ({yearly.idxmin()})")
    print("\nN20 K2 yearly (the deployed-config cell):")
    print(results[(20, 2.0)]["yearly"].round(0).astype(int).to_string())


if __name__ == "__main__":
    main()
