"""Single-stock 60m Donchian kill-test per PREREG_STOCK_TREND.md.

Engine copied from the ETF-variant study (gap-honest RTH). Data hygiene:
raw ITCH prices are split-UNADJUSTED — back-adjusted here from a fixed
public split table, verified against the observed overnight ratio.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
NOTIONAL = 30_000.0
END_IS = pd.Timestamp("2022-12-31", tz="UTC")
SYMS = ["NVDA", "TSLA", "AMD", "AAPL", "MSFT", "META", "AMZN", "GOOGL",
        "NFLX", "AVGO", "MU", "SMCI", "PLTR", "UBER"]
SPLITS = {   # date (effective open), ratio (old shares -> new)
    "AAPL": [("2020-08-31", 4)],
    "TSLA": [("2020-08-31", 5), ("2022-08-25", 3)],
    "NVDA": [("2021-07-20", 4), ("2024-06-10", 10)],
    "AMZN": [("2022-06-06", 20)],
    "GOOGL": [("2022-07-18", 20)],
    "SMCI": [("2024-10-01", 10)],
    "AVGO": [("2024-07-15", 10)],
}


def load_60m(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(BASE / f"data/{sym}_1m.parquet")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for dt, ratio in SPLITS.get(sym, []):
        cut = pd.Timestamp(dt, tz="UTC")
        pre = df.index < cut
        # verify the split is where we think (overnight ratio near `ratio`)
        before = df.loc[pre, "close"].iloc[-1] if pre.any() else None
        after = df.loc[~pre, "open"].iloc[0]
        if before is not None:
            obs = before / after
            assert 0.6 * ratio < obs < 1.6 * ratio, \
                f"{sym} split {dt}: observed ratio {obs:.1f} != {ratio}"
        df.loc[pre, ["open", "high", "low", "close"]] /= ratio
    et = df.index.tz_convert("America/New_York")
    rth = (((et.hour == 9) & (et.minute >= 30)) |
           (et.hour.isin(range(10, 16))))
    df = df[rth]
    g = df.resample("60min", label="left", origin="start_day",
                    offset="30min")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last()}
                     ).dropna(subset=["close"])
    return B


def run_name(B: pd.DataFrame) -> pd.DataFrame:
    pc = B["close"].shift(1)
    tr_ = pd.concat([B["high"] - B["low"], (B["high"] - pc).abs(),
                     (B["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr_.rolling(20).mean()
    donch = B["close"].rolling(20).max().shift(1)
    ap = atr / B["close"]
    th = ap.rolling(24 * 365, min_periods=24 * 60).quantile(0.95)
    sig = ((B["close"] > donch) & ~(ap > th) & atr.notna()).values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    A, ts = atr.values, B.index
    trades = []
    pos = pend = 0
    e_px = st = sh = 0.0
    e_ts = None
    for i in range(1, len(B)):
        if pend:
            e_px, e_ts, pos, pend = O[i], ts[i], 1, 0
            sh = NOTIONAL / e_px
            st = e_px - 2.0 * A[i - 1]
            hw = e_px
        if pos:
            if L[i] <= st:
                x = min(st, O[i])          # gap-through fills at the open
                fee = sh * 0.005 * 2 + (e_px + x) * sh * 2e-4
                trades.append((e_ts, ts[i], e_px, x,
                               (x - e_px) * sh - fee,
                               "gap" if O[i] < st else "stop"))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - 2.0 * A[i])
        if not pos and not pend and sig[i]:
            pend = 1
    return pd.DataFrame(trades, columns=["entry_time", "exit_time",
                                         "entry_px", "exit_px", "pnl",
                                         "reason"])


def yearly(T, col="pnl"):
    if not len(T):
        return pd.Series(dtype=float)
    return T.set_index("exit_time")[col].groupby(lambda t: t.year).sum()


def main():
    all_trades = {}
    print(f"{'sym':<6}{'trades':>7}{'total':>11}{'worst-yr':>11}"
          f"{'(yr)':>7}{'gap%':>6}  floor(-$1500)")
    fails_is = 0
    for sym in SYMS:
        B = load_60m(sym)
        T = run_name(B)
        all_trades[sym] = T
        Tis = T[T["exit_time"] <= END_IS]
        y = yearly(Tis)
        wy = y.min() if len(y) else 0.0
        wyl = y.idxmin() if len(y) else "-"
        gap = (T["reason"] == "gap").mean() * 100 if len(T) else 0
        fail = "FAIL" if wy < -1500 else "ok"
        if wy < -1500:
            fails_is += 1
        print(f"{sym:<6}{len(Tis):>7}{Tis['pnl'].sum():>11,.0f}"
              f"{wy:>11,.0f}{str(wyl):>7}{gap:>5.0f}%  {fail}")
    # portfolio C(ii): weekly top-3 by 35-trading-day return
    closes = {s: load_60m(s)["close"].resample("1D").last().dropna()
              for s in SYMS}
    allT = (pd.concat([t.assign(sym=s) for s, t in all_trades.items()])
            .sort_values("entry_time"))
    qty = []
    for r in allT.itertuples():
        mon = (r.entry_time - pd.Timedelta(days=r.entry_time.weekday())
               ).normalize()
        rets = {}
        for s, cl in closes.items():
            past = cl[cl.index < mon]
            if len(past) > 35:
                rets[s] = past.iloc[-1] / past.iloc[-36] - 1
        top3 = sorted(rets, key=rets.get, reverse=True)[:3]
        qty.append(1.0 if r.sym in top3 else 0.0)
    allT = allT.assign(qty=qty)
    P = allT[allT["qty"] > 0].copy()
    Pis = P[P["exit_time"] <= END_IS]
    yp = yearly(Pis)
    print(f"\nPORTFOLIO C(ii) top-3 rotation IS: {len(Pis)} trades, "
          f"total ${Pis['pnl'].sum():,.0f}, worst-yr ${yp.min():,.0f} "
          f"({yp.idxmin() if len(yp) else '-'})")
    print(f"per-name IS floor failures: {fails_is}/14 "
          f"(kill rule: >=8 fails OR portfolio IS <= 0)")
    killed = fails_is >= 8 or Pis["pnl"].sum() <= 0
    print("IS VERDICT:", "KILLED — no OOS spend" if killed
          else "IS survives — OOS single shot authorized")
    allT.to_parquet(BASE / "trades_all.parquet")
    if not killed:
        Poos = P[P["exit_time"] > END_IS]
        yo = yearly(Poos)
        print(f"\nOOS (single shot): {len(Poos)} trades, total "
              f"${Poos['pnl'].sum():,.0f}, worst-yr ${yo.min():,.0f} "
              f"({yo.idxmin() if len(yo) else '-'})")
        gap_oos = (Poos["reason"] == "gap").mean() * 100
        print(f"OOS gap-exit share: {gap_oos:.0f}% (gate <= 15%)")


if __name__ == "__main__":
    main()
