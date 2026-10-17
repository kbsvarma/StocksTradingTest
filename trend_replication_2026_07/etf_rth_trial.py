"""RTH-only ETF variant per PREREG_SILVER_ETF.md.

Engine adapted: RTH session only (09:30-16:00 ET), 60m bars (7/day, last one
30min), NO session gate (all RTH), no rolls. Gap-through-stop at the open is
the measured risk: stop hit on bar low -> fill at min(stop, open).

Sizing: fixed $60,000 notional per entry (shares = 60000/entry price,
fractional allowed for comparability), friction per RT = shares * (2*$0.005
commission + $0.01 half-spread each side ~ $0.02 total spread cost).
Benchmark: MNQ futures trades over the identical window from the research
engine (same underlying for QQQ = clean A/B).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
NOTIONAL = 60_000.0
END22 = pd.Timestamp("2022-12-31", tz="UTC")


def load_etf_60m(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(BASE / f"data/etf/{sym}_1m.parquet")
    df = df[~df.index.duplicated(keep="last")]
    et = df.index.tz_convert("America/New_York")
    rth = (((et.hour == 9) & (et.minute >= 30)) | (et.hour.isin(range(10, 16))))
    df = df[rth]
    g = df.resample("60min", label="left", origin="start_day", offset="30min")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last()}
                     ).dropna(subset=["close"])
    return B


def run_etf(B: pd.DataFrame) -> pd.DataFrame:
    pc = B["close"].shift(1)
    tr_ = pd.concat([B["high"] - B["low"], (B["high"] - pc).abs(),
                     (B["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr_.rolling(20).mean()
    donch = B["close"].rolling(20).max().shift(1)
    ap = atr / B["close"]
    # ~7 bars/day: same *bar-count* windows as the futures engine
    th = ap.rolling(24 * 365, min_periods=24 * 60).quantile(0.95)
    sig = ((B["close"] > donch) & ~(ap > th) & atr.notna()).values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    A, ts = atr.values, B.index
    trades = []
    pos = pend = 0
    e = hw = st = sh = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend:
            pos, pend = 1, 0
            e, hw = O[i], O[i]
            sh = NOTIONAL / e
            st = e - 2.0 * A[i - 1]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos:
            if L[i] <= st:
                x = min(st, O[i])          # gap-through-stop fills at the open
                fr = sh * (2 * 0.005 + 0.02)
                trades.append((e_ts, ts[i], (x - e) * sh - fr,
                               "gap" if O[i] < st else "stop"))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - 2.0 * A[i])
        if pos == 0 and pend == 0 and sig[i] and i > 20:
            pend = 1
    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "pnl", "reason"])


def mnq_benchmark():
    """MNQ futures trades 2021+ from the research cache, scaled to $60k
    notional at entry (MNQ notional ~ $2*NQ ~ $60k -> ~1 contract)."""
    sys.path.insert(0, str(BASE.parent / "trend_bot"))
    sys.path.insert(0, str(BASE.parent / "trend_bot/research"))
    import rotation_grid as rg
    B = rg.load_60m("NQ")
    T = rg.simulate(B, "NQ")
    T = T[T["entry_time"] >= pd.Timestamp("2021-01-01", tz="UTC")]
    contracts = NOTIONAL / (T["entry_px"] * 2.0)
    pnl = (T["points"] * 2.0 - 3.50 / contracts) * contracts  # friction per ct
    return pd.Series(pnl.values, index=pd.DatetimeIndex(T["exit_time"]))


def report(label, p):
    yearly = p.groupby(p.index.year).sum()
    gaps = ""
    print(f"{label:<22} n={len(p):>4}  total ${p.sum():>9,.0f}  "
          f"worst-yr ${yearly.min():>8,.0f} ({yearly.idxmin()})  "
          f"yearly {dict(yearly.round(0).astype(int))}")


def main():
    for sym in ("QQQ", "SPY", "IWM", "IJH"):
        B = load_etf_60m(sym)
        T = run_etf(B)
        p = pd.Series(T["pnl"].values, index=pd.DatetimeIndex(T["exit_time"]))
        n_gap = (T["reason"] == "gap").sum()
        report(f"{sym} (RTH ETF)", p)
        print(f"{'':<22} gap-through-stop exits: {n_gap}/{len(T)} "
              f"({n_gap/max(len(T),1)*100:.0f}%)")
    print()
    report("MNQ futures (bench)", mnq_benchmark())


if __name__ == "__main__":
    main()
