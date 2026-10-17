"""COT positioning conditioner per PREREG_COT.md — two registered tests."""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT / "trend_bot"))
sys.path.insert(0, str(ROOT / "trend_bot" / "research"))

MARKETS = {   # mapping recorded per prereg BEFORE tests; CFTC renamed
    # these markets over the years — the full alias list is the mapping
    "ES": ["E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
           "E-MINI S&P 500 STOCK INDEX - CHICAGO MERCANTILE EXCHANGE"],
    "NQ": ["NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE",
           "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE"],
    "YM": ["DJIA x $5 - CHICAGO BOARD OF TRADE",
           "DOW JONES INDUSTRIAL AVG- x $5 - CHICAGO BOARD OF TRADE"],
}


def load_cot():
    frames = []
    for f in sorted(glob.glob(str(BASE / "data/y*/FinFutYY.txt"))):
        df = pd.read_csv(f, low_memory=False)
        frames.append(df)
    cot = pd.concat(frames)
    cot["date"] = pd.to_datetime(cot["Report_Date_as_YYYY-MM-DD"], utc=True)
    out = {}
    for sym, names in MARKETS.items():
        clean = cot["Market_and_Exchange_Names"].str.strip()
        g = cot[clean.isin(names)].sort_values("date")
        g = g.drop_duplicates("date")
        s = pd.DataFrame({
            "oi": g["Open_Interest_All"].values,
            "lev_net": (g["Lev_Money_Positions_Long_All"]
                        - g["Lev_Money_Positions_Short_All"]).values,
        }, index=g["date"])
        s["oi_falling"] = s["oi"].diff() < 0
        s["lev_pct"] = s["lev_net"].rolling(156, min_periods=52).rank(pct=True)
        s["crowded"] = s["lev_pct"] > 0.80
        # LAG HONESTY: Tuesday obs -> Friday release -> conditions the
        # FOLLOWING Mon-Sun week
        s["applies_from"] = s.index + pd.Timedelta(days=6)   # next Monday
        out[sym] = s
        print(f"{sym}: {len(s)} weekly reports "
              f"{s.index.min().date()} -> {s.index.max().date()}")
    return out


def reference_trades():
    import rotation_grid as rg
    from rotation import monday_of_week
    rg.START = pd.Timestamp("2011-01-01", tz="UTC")
    bars, daily = {}, {}
    for sym in rg.MULT:
        B = rg.load_60m(sym)
        bars[sym] = B
        daily[sym] = B["close"].resample("1D").last().dropna()
    t0 = min(b.index.min() for b in bars.values())
    t1 = max(b.index.max() for b in bars.values())
    W = rg.weekly_weights(daily, 35, "aggressive300", t0, t1)
    rows = []
    for sym in rg.MULT:
        wq = lambda ts, s=sym: (int(W.loc[monday_of_week(ts), s])
                                if monday_of_week(ts) in W.index else 1)
        T = rg.simulate_weighted(bars[sym], sym, wq)
        T["pnl"] = (T["points"] * rg.MULT[sym] - rg.FR) * T["qty"]
        rows.append(T)
    return pd.concat(rows).sort_values("exit_time").reset_index(drop=True)


def join_feature(T, cot, col):
    """For each trade, the latest report APPLICABLE at entry (lag-honest)."""
    vals = []
    for r in T.itertuples():
        s = cot[r.sym]
        appl = s[s["applies_from"] <= r.entry_time]
        vals.append(appl[col].iloc[-1] if len(appl) else np.nan)
    return pd.Series(vals, index=T.index)


def nw_test(T, flag, label, direction):
    from trial_stats import newey_west_indicator_t
    ok_dir, halves_ok = [], []
    for sym in ("NQ", "ES", "YM"):
        g = T[T["sym"] == sym]
        a, b = g[g[flag] == True]["pnl"], g[g[flag] == False]["pnl"]
        d = a.mean() - b.mean()
        ok_dir.append(np.sign(d) == direction)
    Tt = T.dropna(subset=[flag]).sort_values("exit_time")
    t = newey_west_indicator_t(Tt["pnl"].values,
                               Tt[flag].astype(float).values, lags=3)
    mid = pd.Timestamp("2019-01-01", tz="UTC")
    hs = []
    for lo, hi in ((None, mid), (mid, None)):
        g = Tt
        if lo is not None:
            g = g[g["exit_time"] >= lo]
        if hi is not None:
            g = g[g["exit_time"] < hi]
        d = (g[g[flag] == True]["pnl"].mean()
             - g[g[flag] == False]["pnl"].mean())
        hs.append(np.sign(d) == direction)
    a = Tt[Tt[flag] == True]["pnl"]
    b = Tt[Tt[flag] == False]["pnl"]
    print(f"\n{label}:")
    print(f"  flagged {len(a)} trades ${a.mean():.2f}/trade  vs "
          f"other {len(b)} ${b.mean():.2f}/trade")
    print(f"  NW t = {t:.2f} (gate >=2, registered direction "
          f"{'+' if direction > 0 else '-'})")
    print(f"  per-instrument direction OK: {sum(ok_dir)}/3  "
          f"halves consistent: {sum(hs)}/2")
    passed = (abs(t) >= 2 and np.sign(t) == direction
              and sum(ok_dir) == 3 and sum(hs) == 2)
    print(f"  VERDICT: {'PASS' if passed else 'FAIL'}")
    return passed


def main():
    cot = load_cot()
    T = reference_trades()
    T["cot_oi_falling"] = join_feature(T, cot, "oi_falling")
    T["cot_crowded"] = join_feature(T, cot, "crowded")
    nw_test(T, "cot_oi_falling",
            "T1 COT-OI mirror (falling weekly OI => better entries)", +1)
    nw_test(T, "cot_crowded",
            "T2 crowding (lev-fund >80th pct => worse entries)", -1)


if __name__ == "__main__":
    main()
