"""Pull June 2026 (through 2026-07-02) + the 7 genuinely-missing days.
Per day: definition schema -> 0DTE SPXW contracts in a generous strike window
(centered on previous official Cboe SPX close, -10%/+4%) -> cbbo-1m raw_symbol pull.
Writes day-files in the cache's exact format to data/new_days/.
Resumable; prints cost per day; never overwrites cache.
"""
import os
import time
from pathlib import Path

import databento as db
import pandas as pd

KEY = os.environ["DATABENTO_API_KEY"]
client = db.Historical(KEY)
OUT = Path("data/new_days")
OUT.mkdir(parents=True, exist_ok=True)

MISSING = ["2013-06-28", "2016-02-24", "2016-08-15", "2022-02-16",
           "2022-04-19", "2022-05-12", "2026-01-08"]

def june_days():
    import pandas_market_calendars as mcal
    nyse = mcal.get_calendar("NYSE")
    days = nyse.schedule(start_date="2026-06-01", end_date="2026-07-02").index
    return [d.strftime("%Y-%m-%d") for d in days]

def spx_prev_close():
    spx = pd.read_csv("data/spx_daily_cboe.csv", parse_dates=["DATE"]).set_index("DATE")["SPX"]
    return spx

def total_cost_tracker():
    return {"spent": 0.0}

def pull_day(day, spx, tracker):
    d = pd.Timestamp(day)
    tag = d.strftime("%Y%m%d")
    outs = {s: OUT / f"spxw_{s.lower()}_cbbo_{tag}.parquet" for s in ("P", "C")}
    if all(o.exists() and o.stat().st_size > 0 for o in outs.values()):
        return "skip"
    prev = spx[spx.index < d]
    if len(prev) == 0:
        print(f"[{day}] no prev SPX close — SKIP (logged)"); return "no-spx"
    center = float(prev.iloc[-1])
    lo, hi = center * 0.90, center * 1.04

    # 1) definitions for that day
    try:
        dfn = client.timeseries.get_range(
            dataset="OPRA.PILLAR", symbols=["SPXW.OPT"], stype_in="parent",
            schema="definition", start=day, end=(d + pd.Timedelta(days=1)).strftime("%Y-%m-%d")).to_df()
    except Exception as e:
        print(f"[{day}] definition pull failed: {e}"); return "def-fail"
    if len(dfn) == 0:
        print(f"[{day}] no SPXW definitions — no listing that day"); return "no-defs"
    dfn = dfn.drop_duplicates(subset=["raw_symbol"])
    # 0DTE filter via OCC symbol's embedded yymmdd — unambiguous, no tz semantics.
    # (expiration-timestamp filtering shifted a day after ET conversion and
    # contaminated files with next-day contracts — see DEVIATIONS.md)
    embedded = dfn["raw_symbol"].str.extract(r"SPXW\s*(\d{6})")[0]
    zero = dfn[embedded == d.strftime("%y%m%d")].copy()
    if len(zero) == 0:
        print(f"[{day}] no 0DTE expiry listed — structural, logged"); return "no-0dte"
    zero["strike"] = zero["strike_price"] if "strike_price" in zero.columns else zero["raw_symbol"].str[-8:].astype(int) / 1000.0
    sel = zero[(zero["strike"] >= lo) & (zero["strike"] <= hi)]
    for side in ("P", "C"):
        o = outs[side]
        if o.exists() and o.stat().st_size > 0:
            continue
        syms = sorted(sel[sel["instrument_class"] == side]["raw_symbol"].unique())
        if not syms:
            print(f"[{day}] {side}: no contracts in window"); continue
        start = d.strftime("%Y-%m-%dT13:25")
        end = d.strftime("%Y-%m-%dT21:20")
        cost = client.metadata.get_cost(dataset="OPRA.PILLAR", symbols=syms, stype_in="raw_symbol",
                                        schema="cbbo-1m", start=start, end=end)
        tracker["spent"] += cost
        for attempt in range(3):
            try:
                df = client.timeseries.get_range(dataset="OPRA.PILLAR", symbols=syms,
                                                 stype_in="raw_symbol", schema="cbbo-1m",
                                                 start=start, end=end).to_df()
                df.to_parquet(o)
                print(f"[{day}] {side}: {len(syms)} syms, {len(df)} rows, ${cost:.3f}")
                break
            except Exception as e:
                print(f"[{day}] {side} attempt {attempt+1}: {e}")
                time.sleep(4 * (attempt + 1))
    return "ok"

def main():
    spx = spx_prev_close()
    tracker = total_cost_tracker()
    days = MISSING + june_days()
    print(f"{len(days)} days to pull")
    for day in days:
        r = pull_day(day, spx, tracker)
        if r not in ("ok", "skip"):
            print(f"  -> {day}: {r}")
    print(f"\nTOTAL SPENT this run: ${tracker['spent']:.2f}")

if __name__ == "__main__":
    main()
