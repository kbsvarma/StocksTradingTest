"""Two-sided Donchian per PREREG_TWO_SIDED.md — mechanical gates."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT / "trend_bot"))
sys.path.insert(0, str(ROOT / "trend_bot" / "research"))

import rotation_grid as rg                       # noqa: E402

rg.START = pd.Timestamp("2011-01-01", tz="UTC")
K = 2.0


def simulate_two(B, allow_short):
    """allow_short: bool array per bar (V1: all True; V2: 35d ret < 0)."""
    import strategy
    f = strategy.signal_frame(B, rg.SCFG)
    atr = f["atr"].values
    long_sig = f["signal"].values
    dlow = B["close"].rolling(rg.SCFG["donchian_n"]).min().shift(1)
    short_raw = (B["close"] < dlow) & f["vol_ok"] & f["session_ok"] \
        & f["atr"].notna()
    short_sig = (short_raw.values & allow_short)
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R, ts = B["roll"].values, B.index
    trades = []
    pos = pend = 0            # pos/pend in {+1, 0, -1}
    e = st = hw = lw = np.nan
    e_ts = None
    for i in range(len(B)):
        if pend and R[i]:
            pend = 0
        if pend:
            d, pend = pend, 0
            pos, e, e_ts = d, O[i], ts[i]
            if d > 0:
                hw, st = e, e - K * atr[i - 1]
            else:
                lw, st = e, e + K * atr[i - 1]
            if np.isnan(st):
                pos = 0
        if pos and R[i]:
            x = C[i - 1] if i else O[i]
            trades.append((e_ts, ts[i], e, x, (x - e) * pos, "roll", pos))
            pos = 0
        if pos > 0:
            if L[i] <= st:
                x = min(st, O[i])
                trades.append((e_ts, ts[i], e, x, x - e, "stop", 1))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - K * atr[i])
        elif pos < 0:
            if H[i] >= st:
                x = max(st, O[i])
                trades.append((e_ts, ts[i], e, x, e - x, "stop", -1))
                pos = 0
            else:
                lw = min(lw, L[i])
                st = min(st, lw + K * atr[i])
        if pos == 0 and pend == 0 and i > rg.SCFG["donchian_n"]:
            if long_sig[i]:
                pend = 1
            elif short_sig[i]:
                pend = -1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "entry_px",
                                      "exit_px", "points", "reason", "dir"])
    return T[T["entry_time"] >= rg.START].reset_index(drop=True)


def table(T, sym):
    pnl = pd.Series(T["points"].values * rg.MULT[sym] - rg.FR,
                    index=pd.DatetimeIndex(T["exit_time"]))
    y = pnl.groupby(pnl.index.year).sum()
    return dict(total=float(pnl.sum()), worst=float(y.min()),
                wy=int(y.idxmin()), y2022=float(y.get(2022, 0)),
                chop=float(pnl[pnl.index.year <= 2015].sum()),
                n=len(T),
                n_short=int((T["dir"] < 0).sum()),
                short_pnl=float(pnl[(T["dir"] < 0).values].sum()),
                short_win=(float((T[T["dir"] < 0]["points"] > 0).mean())
                           * 100 if (T["dir"] < 0).any() else np.nan))


def main():
    print(f"{'variant':<16}{'sym':<4}{'total':>9}{'worst-yr':>10}{'(yr)':>6}"
          f"{'2022':>8}{'2011-15':>9}{'n':>5}{'nS':>5}{'S-pnl':>8}"
          f"{'S-win%':>7}")
    results = {}
    for sym in rg.MULT:
        B = rg.load_60m(sym)
        ret35 = B["close"].resample("1D").last().dropna().pct_change(35)
        r35 = ret35.reindex(B.index.normalize()).values
        for name, allow in (("V0 long-only", np.zeros(len(B), bool)),
                            ("V1 pure 2-side", np.ones(len(B), bool)),
                            ("V2 regime-gate", r35 < 0)):
            T = simulate_two(B, allow)
            r = table(T, sym)
            results[(name, sym)] = r
            print(f"{name:<16}{sym:<4}{r['total']:>9,.0f}{r['worst']:>10,.0f}"
                  f"{r['wy']:>6}{r['y2022']:>8,.0f}{r['chop']:>9,.0f}"
                  f"{r['n']:>5}{r['n_short']:>5}{r['short_pnl']:>8,.0f}"
                  f"{r['short_win']:>7.1f}")
    print("\nMECHANICAL GATES (Pareto vs V0, >=2/3 instruments):")
    for name in ("V1 pure 2-side", "V2 regime-gate"):
        passes = 0
        for sym in rg.MULT:
            v, b = results[(name, sym)], results[("V0 long-only", sym)]
            ok = ((v["worst"] >= b["worst"] - 100 and v["total"] >= b["total"])
                  or (v["worst"] > b["worst"]
                      and v["total"] >= b["total"] - 500))
            passes += ok
        print(f"  {name}: {passes}/3 instruments -> "
              f"{'IMPROVEMENT' if passes >= 2 else 'FAIL'}")


if __name__ == "__main__":
    main()
