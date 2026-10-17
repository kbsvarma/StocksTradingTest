"""Stage A: XNAS.ITCH ohlcv-1d ALL_SYMBOLS 2018-05-01 -> 2026-07-30.
Chunked by year, parquet cache, resumable. Quoted cost $29.51 total."""
import sys
from pathlib import Path

import databento as db
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
KEY = sys.argv[1]

CHUNKS = [(f"{y}-01-01", f"{y + 1}-01-01") for y in range(2018, 2027)]
CHUNKS[0] = ("2018-05-01", "2019-01-01")
CHUNKS[-1] = ("2026-01-01", "2026-07-30")

c = db.Historical(KEY)
spent = 0.0
for start, end in CHUNKS:
    f = OUT / f"daily_{start[:4]}.parquet"
    if f.exists():
        print(f"skip {f.name} (cached)")
        continue
    cost = c.metadata.get_cost(dataset="XNAS.ITCH", schema="ohlcv-1d",
                               symbols="ALL_SYMBOLS", start=start, end=end)
    print(f"pull {start}..{end}  ${cost:.2f} ...", flush=True)
    data = c.timeseries.get_range(dataset="XNAS.ITCH", schema="ohlcv-1d",
                                  symbols="ALL_SYMBOLS",
                                  start=start, end=end)
    df = data.to_df()
    df.to_parquet(f)
    spent += cost
    print(f"  -> {len(df):,} rows, {df['symbol'].nunique():,} symbols, "
          f"cum ${spent:.2f}", flush=True)
print("STAGE A DONE")
