"""Repair pulls for the SPXW CBBO cache.

A. Winter-day missing final hour (fixed-UTC-window bug in original pull):
   for every day-file whose last bar ends ~15:00 EST, pull 20:00->21:05 UTC for
   that file's own symbol list and write to repairs/<side>/<date>.parquet.
C. Missing opposite-side files: derive symbols from the existing side (P<->C swap)
   and pull the full session for those days.

Never modifies the original cache. Resumable: skips existing non-empty outputs.
"""
import os
import time
from pathlib import Path

import databento as db
import pandas as pd

KEY = os.environ["DATABENTO_API_KEY"]
BASE = Path("../spx_spread_bot/backtests/narrow_width_databento_2026_05_21")
PUTS = BASE / "ndxp_p_cbbo_1m"
CALLS = BASE / "ndxp_c_cbbo_1m"
OUT = Path("data/repairs_ndxp")
client = db.Historical(KEY)

def last_bar_is_1500(f):
    df = pd.read_parquet(f, columns=["symbol"])  # cheap probe? need index; read index only
    return None

def get_last_bar_et(f):
    df = pd.read_parquet(f)
    return df.index.tz_convert("America/New_York").max()

def pull_symbols(symbols, start, end, out):
    for attempt in range(3):
        try:
            store = client.timeseries.get_range(
                dataset="OPRA.PILLAR", symbols=symbols, stype_in="raw_symbol",
                schema="cbbo-1m", start=start, end=end)
            df = store.to_df()
            df.to_parquet(out)
            return len(df)
        except Exception as e:
            print(f"[err] {out.name} attempt {attempt+1}: {e}")
            time.sleep(4 * (attempt + 1))
    return -1

def _winter_job(side, f, odir):
    d = f.stem.split("_")[-1]
    out = odir / f"{d}.parquet"
    if out.exists() and out.stat().st_size > 0:
        return "skip"
    last = get_last_bar_et(f)
    if not (last.hour == 15 and last.minute <= 5):
        return "skip"
    syms = sorted(pd.read_parquet(f, columns=["symbol"])["symbol"].unique())
    day = pd.Timestamp(d)
    n = pull_symbols(syms, day.strftime("%Y-%m-%dT20:00"), day.strftime("%Y-%m-%dT21:05"), out)
    return "fail" if n < 0 else "ok"

def repair_winter_hour():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    for side, D in (("P", PUTS), ("C", CALLS)):
        files = sorted(D.glob("*.parquet"))
        todo = []
        for f in files:
            d = f.stem.split("_")[-1]
            ts = pd.Timestamp(d).tz_localize("America/New_York")
            if ts.utcoffset().total_seconds() != -5 * 3600:
                continue  # summer, fine
            todo.append(f)
        print(f"{side}: {len(todo)} winter files to check/repair")
        odir = OUT / f"winter_hour_{side}"
        odir.mkdir(parents=True, exist_ok=True)
        counts = {"ok": 0, "fail": 0, "skip": 0}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_winter_job, side, f, odir): f for f in todo}
            for i, fut in enumerate(as_completed(futs)):
                counts[fut.result()] += 1
                if (i + 1) % 100 == 0:
                    print(f"  [{side}] {i+1}/{len(todo)}  {counts}")
        print(f"  [{side}] DONE: {counts}")

def repair_missing_side():
    pdates = {f.stem.split("_")[-1] for f in PUTS.glob("*.parquet")}
    cdates = {f.stem.split("_")[-1] for f in CALLS.glob("*.parquet")}
    odir = OUT / "missing_side"
    odir.mkdir(parents=True, exist_ok=True)
    jobs = [(d, "C") for d in sorted(pdates - cdates)] + [(d, "P") for d in sorted(cdates - pdates)]
    print(f"missing-side jobs: {jobs}")
    for d, want in jobs:
        out = odir / f"{want}_{d}.parquet"
        if out.exists() and out.stat().st_size > 0:
            continue
        src = (PUTS / f"spxw_p_cbbo_{d}.parquet") if want == "C" else (CALLS / f"spxw_c_cbbo_{d}.parquet")
        syms = sorted(pd.read_parquet(src, columns=["symbol"])["symbol"].unique())
        flip = "C" if want == "C" else "P"
        orig = "P" if want == "C" else "C"
        syms2 = [s.replace(f"{orig}0", f"{flip}0", 1) if f"{orig}0" in s else s for s in
                 [s[:12] + flip + s[13:] for s in syms]]  # OCC: root(6)+yymmdd(6)+CP(1)+strike(8)
        day = pd.Timestamp(d)
        n = pull_symbols(sorted(set(syms2)), day.strftime("%Y-%m-%dT13:25"), day.strftime("%Y-%m-%dT21:20"), out)
        print(f"  {want} {d}: {n} rows")

if __name__ == "__main__":
    repair_winter_hour()
    print("ALL REPAIRS DONE")
