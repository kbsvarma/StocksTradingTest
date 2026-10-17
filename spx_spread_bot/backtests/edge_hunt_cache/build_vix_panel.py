"""Build persistent VIX panel cache.
Uses existing ../narrow_width_databento_2026_05_21/spot_cache_GSPC.parquet for SPX.
Pulls VIX / VIX9D / VIX3M / VVIX daily OHLC from yfinance (one-time, ~30 sec).
Output: edge_hunt_cache/vix_panel.parquet
"""
from pathlib import Path
import pandas as pd, yfinance as yf

OUT = Path(__file__).parent / "vix_panel.parquet"
START = "2013-01-01"; END = "2026-06-01"

print("[vix_panel] pulling VIX panel from yfinance...")
panel = {}
for sym, key in [("^VIX","vix"), ("^VIX9D","vix9d"), ("^VIX3M","vix3m"), ("^VVIX","vvix")]:
    print(f"  {sym}...")
    df = yf.Ticker(sym).history(start=START, end=END)
    if df.empty:
        print(f"    NO DATA returned for {sym}")
        continue
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[["Open","High","Low","Close"]].rename(columns={
        "Open": f"{key}_open", "High": f"{key}_high", "Low": f"{key}_low", "Close": f"{key}_close"})
    panel[key] = df
    print(f"    {len(df)} rows, {df.index.min().date()} → {df.index.max().date()}")

# Combine
combined = None
for k, df in panel.items():
    combined = df if combined is None else combined.join(df, how="outer")

print(f"\n[vix_panel] combined shape: {combined.shape}")
print(combined.tail(5).round(2).to_string())
combined.to_parquet(OUT)
print(f"\n[vix_panel] saved → {OUT}")
print(f"           columns: {list(combined.columns)}")
