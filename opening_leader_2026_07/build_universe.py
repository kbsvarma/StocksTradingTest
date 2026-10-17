"""Stage A3 (local, $0): symbol-level daily panel + monthly PIT universe.

Universe per PREREG: U_m = top 150 raw symbols by MEDIAN daily dollar volume
over calendar month m-1, price >= $5 at m-1 end, present >= 60% of m-1's
trading days. Common stock/ADR only where the definition data lets us tell.
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"

daily = pd.concat(
    [pd.read_parquet(f)[["instrument_id", "open", "high", "low",
                         "close", "volume"]]
     .assign(day=lambda d: d.index.date.astype(str))
     for f in sorted(OUT.glob("daily_*.parquet"))], ignore_index=True)
idmap = pd.concat([pd.read_parquet(f) for f in
                   sorted(OUT.glob("idmap_*.parquet"))], ignore_index=True)
print(f"daily rows {len(daily):,} | mapped id-days {len(idmap):,} | "
      f"security_type values: {idmap.get('security_type', pd.Series()).value_counts().head(8).to_dict()}")

panel = daily.merge(idmap, on=["day", "instrument_id"], how="inner")
# ITCH issue-classification codes (verified empirically 2026-07-31):
# C=common stock, A=ADR, O=foreign ordinary -> KEEP.
# Q=ETF/ETP (SPY,QQQ,GLD,XLE...), L/S/U/W=notes/units/warrants -> EXCLUDE.
if "security_type" in panel.columns:
    bad = ~panel["security_type"].isin(("C", "A", "O"))
    print(f"excluded by security_type: {bad.sum():,} rows "
          f"({panel.loc[bad, 'raw_symbol'].nunique()} symbols, e.g. "
          f"{panel.loc[bad, 'raw_symbol'].value_counts().head(5).index.tolist()})")
    panel = panel[~bad]
panel["dv"] = panel["close"] * panel["volume"]
panel["month"] = panel["day"].str[:7]
panel = panel.sort_values("day")
panel.to_parquet(OUT / "panel_daily.parquet")

months = sorted(panel["month"].unique())
tdays = panel.groupby("month")["day"].nunique()
rows = []
for prev, cur in zip(months[:-1], months[1:]):
    M = panel[panel["month"] == prev]
    g = M.groupby("raw_symbol").agg(
        med_dv=("dv", "median"), n_days=("day", "nunique"),
        last_close=("close", "last"))
    ok = g[(g["last_close"] >= 5.0) &
           (g["n_days"] >= 0.6 * tdays[prev])]
    top = ok.nlargest(150, "med_dv")
    mega = set(top.nlargest(30, "med_dv").index)
    for sym in top.index:
        rows.append({"month": cur, "symbol": sym,
                     "mega": sym in mega,
                     "med_dv": float(top.loc[sym, "med_dv"])})
U = pd.DataFrame(rows)
U.to_parquet(OUT / "universe.parquet")
print(f"universe months: {U['month'].nunique()} "
      f"({U['month'].min()} .. {U['month'].max()})")
print(f"distinct symbols ever in universe: {U['symbol'].nunique()}")
print("sample 2019-06 top-10:",
      U[U["month"] == "2019-06"].nlargest(10, "med_dv")["symbol"].tolist())
print("sample 2025-06 top-10:",
      U[U["month"] == "2025-06"].nlargest(10, "med_dv")["symbol"].tolist())
