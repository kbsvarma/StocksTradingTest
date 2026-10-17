"""Pull daily EOD chain snapshots (15:54-16:00 ET, cbbo-1m, parent symbology)
for the 9 screened portfolio names, 2021-07-01 -> 2026-07-02.
Resumable (skips existing day-files); 8 workers; every file cached immediately.
Local filtering to <=14 DTE / strike window happens at ANALYSIS time — files
keep the full snapshot so nothing needs re-pulling if the grid changes.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import databento as db
import pandas as pd
import pandas_market_calendars as mcal

KEY = os.environ["DATABENTO_API_KEY"]
NAMES = ["MARA", "MSTR", "COIN", "SMCI", "RIVN", "HOOD", "SOFI", "PLTR", "NVDA"]
OUT = Path("data/chains")
client = db.Historical(KEY)

def trading_days():
    nyse = mcal.get_calendar("NYSE")
    return [d.strftime("%Y-%m-%d") for d in
            nyse.schedule(start_date="2021-07-01", end_date="2026-07-02").index]

def job(name, d):
    odir = OUT / name
    odir.mkdir(parents=True, exist_ok=True)
    out = odir / f"{d.replace('-','')}.parquet"
    if out.exists() and out.stat().st_size > 0:
        return "skip"
    start = pd.Timestamp(f"{d}T15:54", tz="America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")
    end = pd.Timestamp(f"{d}T16:01", tz="America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")
    for attempt in range(3):
        try:
            df = client.timeseries.get_range(
                dataset="OPRA.PILLAR", symbols=[f"{name}.OPT"], stype_in="parent",
                schema="cbbo-1m", start=start, end=end).to_df()
            df.to_parquet(out)
            return "ok"
        except Exception as e:
            msg = str(e)
            if "422" in msg or "no data" in msg.lower():
                out.write_bytes(b"")  # marker: no data that day (pre-listing etc.)
                return "nodata"
            time.sleep(3 * (attempt + 1))
    return "fail"

def main():
    days = trading_days()
    jobs = [(n, d) for n in NAMES for d in days]
    print(f"{len(jobs)} name-days")
    counts = {"ok": 0, "skip": 0, "fail": 0, "nodata": 0}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(job, n, d): (n, d) for n, d in jobs}
        for i, f in enumerate(as_completed(futs)):
            counts[f.result()] += 1
            if (i + 1) % 500 == 0:
                print(f"{i+1}/{len(jobs)}  {counts}", flush=True)
    print("DONE", counts)

if __name__ == "__main__":
    main()
