"""Year-by-year breakdown of the best 2-7 DTE intraday configs to check stability."""
import sys
sys.path.insert(0, str(__file__.rsplit("/",1)[0]))
import pandas as pd
from backtest_short_dte_intraday import run

print("="*110)
print("YEAR-BY-YEAR for best intraday put-spread configs (real fills, ask/bid)")
print("="*110)
print()

configs = [
    (2, 0.10, 200),
    (2, 0.20, 150),
    (3, 0.10, 200),
    (2, 0.20, 100),
    (2, 0.10, 100),
    (3, 0.10, 100),
]

for dte, cred, w in configs:
    df = run(target_dte=dte, target_cred_pct=cred, width=w)
    if df.empty: continue
    df["entry"] = pd.to_datetime(df["entry"])
    df["year"] = df["entry"].dt.year
    print(f"\n### DTE={dte} cred={int(cred*100)}% w={w} ###")
    by = df.groupby("year")["pnl_real"].agg(["count","sum","mean","min"])
    by["winpct"] = df.groupby("year").apply(lambda g: (g["pnl_real"]>0).mean()*100)
    for y, row in by.iterrows():
        print(f"  {y}: n={int(row['count']):3d} total=${row['sum']:+,.0f} avg=${row['mean']:+.0f} "
              f"win={row['winpct']:.0f}% worst=${row['min']:+,.0f}")
    total = df["pnl_real"].sum()
    yrs = df["year"].nunique()
    print(f"  ALL: n={len(df)} total=${total:+,.0f} avg/yr=${total/yrs:+,.0f}/yr")
