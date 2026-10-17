"""Pull SI.v.0 1-min (2016+) year-chunked + ETF 1-min (2021+). Resumable."""
import os
import sys
from pathlib import Path

import databento as db
import pandas as pd

KEY = os.environ["DATABENTO_API_KEY"]
c = db.Historical(KEY)
DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

# --- SI year chunks -------------------------------------------------------
for yr in range(2016, 2027):
    out = DATA / f"SI_1m_{yr}.parquet"
    if out.exists():
        print(f"SI {yr}: cached"); continue
    start = f"{yr}-01-01"
    end = f"{yr+1}-01-01" if yr < 2026 else "2026-07-05"
    try:
        d = c.timeseries.get_range(
            dataset="GLBX.MDP3", symbols=["SI.v.0"], stype_in="continuous",
            schema="ohlcv-1m", start=start, end=end)
        df = d.to_df()
        df.to_parquet(out)
        print(f"SI {yr}: {len(df):,} rows -> {out.name}")
    except Exception as e:
        print(f"SI {yr}: FAILED {str(e)[:150]}")
        sys.exit(1)

# --- ETFs -----------------------------------------------------------------
ETF = DATA / "etf"
ETF.mkdir(exist_ok=True)
for dset, syms in (("XNAS.ITCH", ["QQQ"]), ("ARCX.PILLAR", ["SPY", "IWM", "IJH"])):
    for sym in syms:
        out = ETF / f"{sym}_1m.parquet"
        if out.exists():
            print(f"{sym}: cached"); continue
        try:
            d = c.timeseries.get_range(
                dataset=dset, symbols=[sym], schema="ohlcv-1m",
                start="2021-01-01", end="2026-07-03")
            df = d.to_df()
            df.to_parquet(out)
            print(f"{sym}: {len(df):,} rows -> {out.name}")
        except Exception as e:
            print(f"{sym}: FAILED {str(e)[:150]}")
            sys.exit(1)
print("ALL PULLS COMPLETE")
