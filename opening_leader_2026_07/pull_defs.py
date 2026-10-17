"""Stage A2: per-day id->symbol map for the top-400 dollar-volume ids.

ITCH instrument_ids are per-day locate codes, and ALL_SYMBOLS pulls carry no
mappings, so we buy definitions ONLY for the ids we might ever trade
(top-400 by that day's close*volume): ~$4 total vs $97 for everything.
Resumable: appends one parquet per year, skips completed days.
"""
import sys
import time
from pathlib import Path

import databento as db
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
TOP = 400
KEY = sys.argv[1]

c = db.Historical(KEY)
spent = 0.0
for daily_f in sorted(OUT.glob("daily_*.parquet")):
    year = daily_f.stem.split("_")[1]
    map_f = OUT / f"idmap_{year}.parquet"
    done_days = set()
    frames = []
    if map_f.exists():
        prev = pd.read_parquet(map_f)
        frames.append(prev)
        done_days = set(prev["day"].unique())
    D = pd.read_parquet(daily_f)
    D["day"] = D.index.date.astype(str)
    D["dv"] = D["close"] * D["volume"]
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch(day, block):
        ids = (block.nlargest(TOP, "dv")["instrument_id"]
               .astype(int).tolist())
        end = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        for attempt in range(3):
            try:
                data = c.timeseries.get_range(
                    dataset="XNAS.ITCH", schema="definition",
                    symbols=ids, stype_in="instrument_id",
                    start=day, end=end)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(5)
        df = data.to_df()
        if not len(df):
            return None
        keep = pd.DataFrame({
            "day": day,
            "instrument_id": df["instrument_id"].astype(int),
            "raw_symbol": df["raw_symbol"].astype(str),
        })
        if "security_type" in df.columns:
            keep["security_type"] = df["security_type"].astype(str)
        return keep.drop_duplicates(["instrument_id"])

    todo = [(day, blk) for day, blk in D.groupby("day")
            if day not in done_days]
    n_new = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch, day, blk): day for day, blk in todo}
        for fut in as_completed(futs):
            keep = fut.result()
            if keep is None:
                continue
            frames.append(keep)
            n_new += 1
            if n_new % 50 == 0:
                pd.concat(frames, ignore_index=True).to_parquet(map_f)
                print(f"{year}: {n_new}/{len(todo)} days", flush=True)
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(map_f)
    print(f"{year} DONE ({n_new} new days)", flush=True)
print("STAGE A2 DONE")
