"""Verify Databento statistics schema returns open interest for SPX & AVGO options.
Single-day dry-cost + small actual pull to confirm OI is present.
"""
import os, sys
from pathlib import Path
import databento as db
import pandas as pd

key = os.environ["DATABENTO_API_KEY"]
client = db.Historical(key)

def sym_spx(expiry_yymmdd, strike, side):
    return f"SPXW  {expiry_yymmdd}{side}{int(strike*1000):08d}"

def sym_avgo(expiry_yymmdd, strike, side):
    return f"AVGO  {expiry_yymmdd}{side}{int(strike*1000):08d}"

# SPX sample: Thursday 2024-11-14 → expiry Friday 2024-11-15
# SPX close ~5950, build strikes 5800-6100 step 5
spx_syms = []
for k in range(5800, 6105, 5):
    spx_syms.append(sym_spx("241115", k, "P"))
    spx_syms.append(sym_spx("241115", k, "C"))

# AVGO sample: Tue 2026-03-17 → expiry Wed 2026-03-18 (M/W/F)
# AVGO ~$310 Mar 2026, step $2.5, range 290-330
avgo_syms = []
k = 290.0
while k <= 330.0:
    avgo_syms.append(sym_avgo("260318", k, "P"))
    avgo_syms.append(sym_avgo("260318", k, "C"))
    k += 2.5

for label, syms, query_date in [
    ("SPX 2024-11-14", spx_syms, "2024-11-14"),
    ("AVGO 2026-03-17", avgo_syms, "2026-03-17"),
]:
    print(f"\n=== {label} — {len(syms)} symbols ===")
    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="statistics", symbols=syms,
            stype_in="raw_symbol",
            start=f"{query_date}T00:00:00", end=f"{query_date}T23:59:59",
        )
        print(f"  cost: ${cost:.6f}")
    except Exception as e:
        print(f"  cost-err: {e}")
        continue
    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="statistics", symbols=syms,
            stype_in="raw_symbol",
            start=f"{query_date}T00:00:00", end=f"{query_date}T23:59:59",
        )
        df = data.to_df()
        print(f"  rows={len(df)}  cols={list(df.columns)[:8]}...")
        if not df.empty:
            # Filter for OI-related stat_type — varies by dataset
            print(f"  stat_types present:")
            if "stat_type" in df.columns:
                print(f"    {df['stat_type'].value_counts().head(10).to_dict()}")
            # Find open interest rows
            oi_df = df[df["stat_type"]==9] if "stat_type" in df.columns else df
            if not oi_df.empty:
                print(f"  OI rows: {len(oi_df)}, sample:")
                print(oi_df[["symbol","ts_recv","stat_type","quantity"]].head(5).to_string() if "quantity" in df.columns else oi_df.head(5).to_string())
    except Exception as e:
        print(f"  pull-err: {e}")
