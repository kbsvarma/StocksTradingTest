"""Integrity forensics on daily_gap_d0_L / d1_L BEFORE the OOS look."""
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path(__file__).resolve().parent
D = pd.read_parquet(BASE / "data" / "panel_daily.parquet").sort_values("day")
D["prev_close"] = D.groupby("raw_symbol")["close"].shift(1)
D = D[(D["close"] >= 5) & D["prev_close"].notna()]
D["gap"] = D["open"] / D["prev_close"] - 1
D["o2c"] = D["close"] / D["open"] - 1
D = D[D["day"] >= "2018-06-01"]
IS = D[D["day"] < "2024-01-01"].copy()

IS["dec"] = (IS.groupby("day")["gap"]
             .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                          labels=False)
                        if len(s) >= 50 else pd.Series(np.nan, s.index)))
d0 = IS[IS["dec"] == 0]
print("=== d0 composition (IS) ===")
print("names/day median:", d0.groupby("day").size().median())
print("gap quantiles:", d0["gap"].quantile([.01, .1, .5, .9, .99]).round(3).to_dict())
print("open price quantiles:", d0["open"].quantile([.1, .5, .9]).round(1).to_dict())
print("|gap|>25% rows (split/artifact suspects):", (d0["gap"].abs() > .25).sum(),
      "of", len(d0))
print("gap<-25% rows:", (d0["gap"] < -.25).sum())

print("\n=== same-day o2c by gap band inside d0 ===")
d0["band"] = pd.cut(d0["gap"], [-1, -.25, -.10, -.05, -.02, 0, 1])
print(d0.groupby("band", observed=True)["o2c"]
      .agg(["count", "mean", "median"]).round(4).to_string())

print("\n=== artifact-filtered rerun: drop |gap|>25%, price>=$10 ===")
F = IS[(IS["gap"].abs() <= .25) & (IS["open"] >= 10)].copy()
F["dec"] = (F.groupby("day")["gap"]
            .transform(lambda s: pd.qcut(s.rank(method="first"), 10,
                                         labels=False)
                       if len(s) >= 50 else pd.Series(np.nan, s.index)))
for dec in (0, 1):
    blk = F[F["dec"] == dec].copy()
    for hs in (0.0005, 0.0015, 0.0030, 0.0050, 0.0075):
        per = blk.groupby("day").size().rename("k")
        b = blk.merge(per, on="day")
        buy = b["open"] * (1 + hs)
        sell = b["close"] * (1 - hs)
        sh = (10000.0 / b["k"]) / buy
        pnl = (sh * (sell - buy) - 2 * sh * 0.005).groupby(b["day"].values).sum()
        m, s = pnl.mean(), pnl.std(ddof=1)
        t = m / (s / np.sqrt(len(pnl)))
        print(f"d{dec} hs={hs*1e4:>4.0f}bps: mean=${m:>7.2f} t={t:>6.2f} "
              f"WR={(pnl>0).mean():.1%}")

print("\n=== name concentration in filtered d0 ===")
blk = F[F["dec"] == 0]
top = blk.groupby("raw_symbol")["o2c"].agg(["count", "mean"])
print("distinct names:", len(top), "| days:", blk["day"].nunique())
print(top.nlargest(10, "count").round(4).to_string())
print("\nmedian dv20-proxy (close*volume) of d0 names: "
      f"${blk['dv'].median()/1e6:.1f}M vs universe ${F['dv'].median()/1e6:.1f}M")
