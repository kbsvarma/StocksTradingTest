"""IC yearly split for 2-7 DTE intraday strategy (09:45 → 15:55)."""
import sys
sys.path.insert(0, str(__file__.rsplit("/",1)[0]))
import pandas as pd
from backtest_ic_if_intraday import run_ic

configs = [
    (2, 0.20, 100),
    (2, 0.10, 100),
    (2, 0.20, 50),
    (2, 0.20, 150),
    (3, 0.10, 100),
    (3, 0.20, 100),
]

print("="*110)
print("SPX 2-7 DTE INTRADAY IRON CONDOR — yearly stability (real fills)")
print("="*110)
for dte, cred, w in configs:
    df = run_ic(target_dte=dte, put_cred_pct=cred, call_cred_pct=cred, width=w)
    if df.empty: continue
    df["entry"] = pd.to_datetime(df["entry"])
    df["year"] = df["entry"].dt.year
    print(f"\n### DTE={dte} cred={int(cred*100)}%/{int(cred*100)}% w={w}pt ###")
    by = df.groupby("year")["pnl_real"].agg(["count","sum","mean","min"])
    by["winpct"] = df.groupby("year").apply(lambda g: (g["pnl_real"]>0).mean()*100, include_groups=False)
    for y, row in by.iterrows():
        print(f"  {y}: n={int(row['count']):3d} total=${row['sum']:+,.0f} avg=${row['mean']:+.0f} "
              f"win={row['winpct']:.0f}% worst=${row['min']:+,.0f}")
    yrs = df["year"].nunique()
    print(f"  ALL: n={len(df)} total=${df['pnl_real'].sum():+,.0f} avg/yr=${df['pnl_real'].sum()/yrs:+,.0f}")
