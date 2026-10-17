"""Chain snapshot pull v2: one multi-parent request per trading day (all 9 names),
15:54-15:57 ET window, filtered locally to <=14 DTE before writing.
Writes data/chains/<NAME>/<YYYYMMDD>.parquet (same layout the engine reads).
Resumable: a day is skipped if ALL 9 name-files exist. 3 workers (throttle-aware).
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

def day_done(d):
    tag = d.replace("-", "")
    return all((OUT / n / f"{tag}.parquet").exists() for n in NAMES)

def job(d):
    tag = d.replace("-", "")
    if day_done(d):
        return "skip"
    start = pd.Timestamp(f"{d}T15:54", tz="America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")
    end = pd.Timestamp(f"{d}T15:57", tz="America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")
    for attempt in range(4):
        try:
            df = client.timeseries.get_range(
                dataset="OPRA.PILLAR", symbols=[f"{n}.OPT" for n in NAMES],
                stype_in="parent", schema="cbbo-1m", start=start, end=end).to_df()
            break
        except Exception as e:
            if attempt == 3:
                return f"fail:{d}:{e}"
            time.sleep(5 * (attempt + 1))
    if len(df) == 0:
        for n in NAMES:
            (OUT / n).mkdir(parents=True, exist_ok=True)
            (OUT / n / f"{tag}.parquet").write_bytes(b"")
        return "nodata"
    sym = df["symbol"].astype(str)
    df = df.assign(root=sym.str.slice(0, 6).str.strip(),
                   expiry=pd.to_datetime("20" + sym.str.slice(6, 12), format="%Y%m%d"))
    day_ts = pd.Timestamp(d)
    df = df[(df.expiry >= day_ts) & (df.expiry <= day_ts + pd.Timedelta(days=14))]
    for n in NAMES:
        sub = df[df.root == n].drop(columns=["root"])
        (OUT / n).mkdir(parents=True, exist_ok=True)
        sub.to_parquet(OUT / n / f"{tag}.parquet")
    return "ok"

def main():
    days = trading_days()
    todo = [d for d in days if not day_done(d)]
    print(f"{len(todo)}/{len(days)} days to pull", flush=True)
    counts = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(job, d): d for d in todo}
        for i, f in enumerate(as_completed(futs)):
            r = f.result().split(":")[0]
            counts[r] = counts.get(r, 0) + 1
            if (i + 1) % 50 == 0:
                print(f"{i+1}/{len(todo)}  {counts}", flush=True)
    print("DONE", counts, flush=True)

if __name__ == "__main__":
    main()
