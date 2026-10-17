"""Neighborhood robustness for filtered d0_L (declared: +/-1-step perturbations
must ALL stay net-positive; ablation = robustness, not mining)."""
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = pd.read_parquet(BASE / "data" / "panel_daily.parquet").sort_values("day")
RAW["prev_close"] = RAW.groupby("raw_symbol")["close"].shift(1)
RAW = RAW[RAW["prev_close"].notna()]
RAW["gap"] = RAW["open"] / RAW["prev_close"] - 1
RAW = RAW[RAW["day"] >= "2018-06-01"]
CHINA = {"BABA", "JD", "BIDU", "PDD", "NTES", "LI", "BILI", "IQ", "XPEV",
         "NIO", "TME", "BEKE", "DIDI", "EDU", "TAL", "VIPS", "WB", "BGNE"}


def cell(gap_cut=.25, pfloor=10, frac=0.10, hs=0.0005, split="2024-01-01",
         drop_china=False, dvq=None):
    D = RAW[(RAW["gap"].abs() <= gap_cut) & (RAW["open"] >= pfloor)
            & (RAW["close"] >= 5)].copy()
    if drop_china:
        D = D[~D["raw_symbol"].isin(CHINA)]
    if dvq is not None:                       # keep only top-dvq fraction by dv
        thr = D.groupby("day")["dv"].transform(lambda s: s.quantile(1 - dvq))
        D = D[D["dv"] >= thr]
    r = D.groupby("day")["gap"].rank(method="first")
    n = D.groupby("day")["gap"].transform("size")
    blk = D[r <= (n * frac).clip(lower=1)].copy()
    per = blk.groupby("day").size().rename("k")
    b = blk.merge(per, on="day")
    buy = b["open"] * (1 + hs)
    sell = b["close"] * (1 - hs)
    sh = (10000.0 / b["k"]) / buy
    pnl = (sh * (sell - buy) - 2 * sh * 0.005).groupby(b["day"].values).sum()
    out = {}
    for tag, lo, hi in (("IS", "", split), ("OOS", split, "9999")):
        x = pnl[(pnl.index >= lo) & (pnl.index < hi)]
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
        out[tag] = f"${x.mean():>6.2f} t={t:>5.2f}"
    return out


base = cell()
print(f"{'BASE (cut25,p10,10%,5bps)':38} IS {base['IS']}   OOS {base['OOS']}")
for name, kw in [
        ("gap_cut=20%", dict(gap_cut=.20)), ("gap_cut=30%", dict(gap_cut=.30)),
        ("price_floor=$5", dict(pfloor=5)), ("price_floor=$15", dict(pfloor=15)),
        ("band=top5%", dict(frac=.05)), ("band=top15%", dict(frac=.15)),
        ("hs=10bps", dict(hs=.0010)),
        ("split=2023-07", dict(split="2023-07-01")),
        ("drop China ADRs", dict(drop_china=True)),
        ("liquid half only (top50% dv)", dict(dvq=.50)),
        ("most liquid quartile", dict(dvq=.25))]:
    r = cell(**kw)
    print(f"{name:38} IS {r['IS']}   OOS {r['OOS']}")

# tail properties of BASE
D = RAW[(RAW["gap"].abs() <= .25) & (RAW["open"] >= 10) & (RAW["close"] >= 5)]
r = D.groupby("day")["gap"].rank(method="first")
n = D.groupby("day")["gap"].transform("size")
blk = D[r <= (n * .10).clip(lower=1)].copy()
per = blk.groupby("day").size().rename("k")
b = blk.merge(per, on="day")
sh = (10000.0 / b["k"]) / (b["open"] * 1.0005)
pnl = (sh * (b["close"] * .9995 - b["open"] * 1.0005) - 2 * sh * .005
       ).groupby(b["day"].values).sum()
eq = pnl.cumsum()
dd = (eq - eq.cummax()).min()
print(f"\nBASE full-period: total ${pnl.sum():,.0f}  worst day ${pnl.min():,.0f}  "
      f"best day ${pnl.max():,.0f}  maxDD ${dd:,.0f}  "
      f"ann.Sharpe {pnl.mean()/pnl.std(ddof=1)*np.sqrt(252):.2f}")
print(f"skew {pnl.skew():.2f}  worst5: {[round(v) for v in pnl.nsmallest(5)]}")
