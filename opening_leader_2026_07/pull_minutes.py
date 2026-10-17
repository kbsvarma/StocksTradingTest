"""Stage B: windowed minute bars for the PIT universe, per trading day.

Window 13:25-15:35 UTC covers 09:30-10:30 ET across DST at ~1/3 the bytes of
full days. Requests by that day's instrument_ids (150 max). BUDGET GATE per
PREREG: exact extrapolated quote must be <= $45 or we stop.
Resumable: one parquet per year, skips completed days.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import databento as db
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
KEY = sys.argv[1]
QUOTE_ONLY = len(sys.argv) > 2 and sys.argv[2] == "--quote"

c = db.Historical(KEY)
U = pd.read_parquet(OUT / "universe.parquet")
idmap = pd.concat([pd.read_parquet(f) for f in
                   sorted(OUT.glob("idmap_*.parquet"))], ignore_index=True)
idmap["month"] = idmap["day"].str[:7]
want = idmap.merge(U[["month", "symbol"]],
                   left_on=["month", "raw_symbol"],
                   right_on=["month", "symbol"], how="inner")
by_day = {day: blk["instrument_id"].astype(int).tolist()
          for day, blk in want.groupby("day")}
days = sorted(by_day)
print(f"{len(days)} trading days, "
      f"median ids/day {int(pd.Series([len(v) for v in by_day.values()]).median())}")


def window(day):
    return f"{day}T13:25:00Z", f"{day}T15:35:00Z"


# ---- budget gate: quote 16 spread sample days, extrapolate
sample = days[::max(1, len(days) // 16)][:16]
qcost = 0.0
for day in sample:
    s, e = window(day)
    qcost += c.metadata.get_cost(dataset="XNAS.ITCH", schema="ohlcv-1m",
                                 symbols=by_day[day],
                                 stype_in="instrument_id", start=s, end=e)
est = qcost / len(sample) * len(days)
print(f"quoted sample avg ${qcost / len(sample):.4f}/day "
      f"-> full estimate ${est:.2f}")
if est > 45:
    print("BUDGET GATE FAILED (> $45) — aborting per PREREG")
    sys.exit(2)
if QUOTE_ONLY:
    sys.exit(0)


def fetch(day):
    s, e = window(day)
    for attempt in range(3):
        try:
            data = c.timeseries.get_range(
                dataset="XNAS.ITCH", schema="ohlcv-1m",
                symbols=by_day[day], stype_in="instrument_id",
                start=s, end=e)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)
    df = data.to_df()
    if not len(df):
        return None
    df = df.reset_index()[["ts_event", "instrument_id", "open", "high",
                           "low", "close", "volume"]]
    df["day"] = day
    return df

for year in sorted({d[:4] for d in days}):
    f = OUT / f"minutes_{year}.parquet"
    frames, done = [], set()
    if f.exists():
        prev = pd.read_parquet(f)
        frames.append(prev)
        done = set(prev["day"].unique())
    todo = [d for d in days if d[:4] == year and d not in done]
    if not todo:
        print(f"{year}: complete")
        continue
    n = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(fetch, d): d for d in todo}
        for fut in as_completed(futs):
            r = fut.result()
            if r is not None:
                frames.append(r)
            n += 1
            if n % 40 == 0:
                pd.concat(frames, ignore_index=True).to_parquet(f)
                print(f"{year}: {n}/{len(todo)}", flush=True)
    pd.concat(frames, ignore_index=True).to_parquet(f)
    print(f"{year} DONE ({n} days)", flush=True)
print("STAGE B DONE")
