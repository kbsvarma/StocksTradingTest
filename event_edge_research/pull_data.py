"""Event-edge research data pull — ES.v.0 / NQ.v.0, GLBX.MDP3, 2010-06-06 -> 2026-07-03.

Design for resilience: pull in CALENDAR-YEAR chunks, write each chunk to its own
parquet immediately after download. Re-runnable: existing non-empty chunk files
are skipped, so a mid-run failure (rate limit, account lock, network) just resumes.
Never fabricates: any failed chunk is listed at the end; no gap is filled synthetically.
"""
import os
import sys
import time
from pathlib import Path

import databento as db

KEY = os.environ["DATABENTO_API_KEY"]
DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

START_FULL = "2010-06-06"
END_FULL = "2026-07-03"

client = db.Historical(KEY)

def year_chunks():
    chunks = []
    for y in range(2010, 2027):
        s = f"{y}-01-01" if y > 2010 else START_FULL
        e = f"{y + 1}-01-01" if y < 2026 else END_FULL
        chunks.append((y, s, e))
    return chunks

def pull(symbol, schema, tag):
    failed = []
    for y, s, e in year_chunks():
        out = DATA / f"{tag}_{schema}_{y}.parquet"
        if out.exists() and out.stat().st_size > 0:
            print(f"[skip] {out.name} exists")
            continue
        for attempt in range(3):
            try:
                store = client.timeseries.get_range(
                    dataset="GLBX.MDP3",
                    symbols=[symbol],
                    stype_in="continuous",
                    schema=schema,
                    start=s,
                    end=e,
                )
                df = store.to_df()
                if len(df) == 0:
                    print(f"[warn] {out.name}: 0 rows returned")
                df.to_parquet(out)
                print(f"[ok]   {out.name}: {len(df)} rows "
                      f"({df.index[0]} -> {df.index[-1]})" if len(df) else f"[ok] {out.name}: empty")
                break
            except Exception as exc:
                print(f"[err]  {tag} {schema} {y} attempt {attempt + 1}: {exc}")
                time.sleep(5 * (attempt + 1))
        else:
            failed.append((tag, schema, y))
    return failed

def main():
    all_failed = []
    # dailies first (cheap, gives instant full-range sanity picture)
    all_failed += pull("ES.v.0", "ohlcv-1d", "ES")
    all_failed += pull("NQ.v.0", "ohlcv-1d", "NQ")
    all_failed += pull("ES.v.0", "ohlcv-1m", "ES")
    all_failed += pull("NQ.v.0", "ohlcv-1m", "NQ")
    print("\n=== DONE ===")
    if all_failed:
        print("FAILED CHUNKS (data gaps, NOT filled):")
        for f in all_failed:
            print("  ", f)
        sys.exit(1)
    print("All chunks pulled cleanly.")

if __name__ == "__main__":
    main()
