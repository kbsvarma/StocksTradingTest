"""The single declared OOS look for filtered d0_L/d1_L + 2x-spread gate."""
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parent
D = pd.read_parquet(BASE / "data" / "panel_daily.parquet").sort_values("day")
D["prev_close"] = D.groupby("raw_symbol")["close"].shift(1)
D = D[(D["close"] >= 5) & D["prev_close"].notna()]
D["gap"] = D["open"] / D["prev_close"] - 1
D = D[(D["day"] >= "2018-06-01") & (D["gap"].abs() <= .25) & (D["open"] >= 10)]
D["dec"] = (D.groupby("day")["gap"]
            .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                         labels=False)
                       if len(s) >= 50 else pd.Series(np.nan, s.index)))


def run(dec, hs, lo, hi):
    blk = D[(D["dec"] == dec) & (D["day"] >= lo) & (D["day"] < hi)].copy()
    per = blk.groupby("day").size().rename("k")
    b = blk.merge(per, on="day")
    buy = b["open"] * (1 + hs)
    sell = b["close"] * (1 - hs)
    sh = (10000.0 / b["k"]) / buy
    pnl = (sh * (sell - buy) - 2 * sh * 0.005).groupby(b["day"].values).sum()
    m, s = pnl.mean(), pnl.std(ddof=1)
    t = m / (s / np.sqrt(len(pnl)))
    yrs = pnl.groupby(pd.Series(pnl.index).str[:4].values).sum()
    return (f"n={len(pnl)} mean=${m:>7.2f} t={t:>6.2f} WR={(pnl>0).mean():.1%} "
            f"years {{{', '.join(f'{y}:{v:+,.0f}' for y, v in yrs.items())}}}")


for dec in (0, 1):
    print(f"--- d{dec}_L OOS 2024-01..2026-07 ---")
    for hs in (0.0005, 0.0010):
        print(f"  hs={hs*1e4:.0f}bps: {run(dec, hs, '2024-01-01', '9999')}")
