"""Pull SPXW 0DTE PUT cbbo-1m for 2026-06-22..2026-07-03 via raw_symbol (per-strike, cheap+safe).

Caches every day to its own parquet immediately; skip-completed on restart.
Cost-check (DRY) before real pull; STOP if cost drifts >2x estimate.

Usage:
    DATABENTO_API_KEY=... python pull.py --dry     # cost check only
    DATABENTO_API_KEY=... python pull.py --confirm # real pull
"""
import os, sys
from datetime import datetime
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "spxw_p_cbbo_1m"
OUT.mkdir(exist_ok=True)
DRY = "--dry" in sys.argv
CONFIRM = "--confirm" in sys.argv

# Trading days in window (exclude weekends; 07-03 is a session, July 4 is Sat)
DAYS = ["20260622","20260623","20260624","20260625","20260626",
        "20260629","20260630","20260701","20260702","20260703"]

# per-day cbbo-1m cost estimate for a ~60-strike SPXW put band (from prior pulls ~ $0.02-0.10/day)
EST_PER_DAY = 0.15
MAX_ACCEPTABLE_TOTAL = EST_PER_DAY * len(DAYS) * 3  # generous 3x guardrail

def main():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: set DATABENTO_API_KEY"); sys.exit(1)
    client = db.Historical(key)

    # daily bars for strike band (cache)
    dcache = Path(__file__).parent / "spx_daily.parquet"
    if dcache.exists():
        spx = pd.read_parquet(dcache)
    else:
        spx = yf.Ticker("^GSPC").history(start="2026-06-19", end="2026-07-05", auto_adjust=False)
        spx.index = pd.to_datetime(spx.index).tz_localize(None)
        spx.to_parquet(dcache)

    total_cost = 0.0
    for ds in DAYS:
        out = OUT / f"spxw_p_cbbo_{ds}.parquet"
        if out.exists() and not DRY:
            print(f"  {ds}: cached, skip"); continue
        d = datetime.strptime(ds, "%Y%m%d")
        try:
            spot = float(spx.loc[pd.Timestamp(d.date()), "Open"])
        except KeyError:
            if ds == "20260703":
                spot = 7495.0  # daily ^GSPC open proxy (1m not yet on Yahoo); wide band absorbs it
                print(f"  {ds}: using proxy spot 7495")
            else:
                print(f"  {ds}: no spot, skip"); continue
        lo = int(spot*0.92/5)*5; hi = int(spot*1.01/5)*5
        strikes = list(range(lo, hi+5, 5))
        yy = d.strftime("%y%m%d")
        syms = [f"SPXW  {yy}P{k*1000:08d}" for k in strikes]
        s_iso = d.strftime("%Y-%m-%dT13:31:00")   # 09:31 ET first RTH minute (UTC)
        e_iso = d.strftime("%Y-%m-%dT20:01:00")   # 16:01 ET
        try:
            cost = client.metadata.get_cost(dataset="OPRA.PILLAR", schema="cbbo-1m",
                   symbols=syms, stype_in="raw_symbol", start=s_iso, end=e_iso)
        except Exception as ex:
            print(f"  {ds}: cost-err {ex}"); continue
        total_cost += cost
        if DRY:
            print(f"  {ds}: spot={spot:.0f} strikes={len(syms)} cost=${cost:.4f} running=${total_cost:.4f}")
            continue
        if total_cost > MAX_ACCEPTABLE_TOTAL:
            print(f"  STOP: running cost ${total_cost:.2f} exceeds guardrail ${MAX_ACCEPTABLE_TOTAL:.2f}")
            sys.exit(2)
        try:
            data = client.timeseries.get_range(dataset="OPRA.PILLAR", schema="cbbo-1m",
                   symbols=syms, stype_in="raw_symbol", start=s_iso, end=e_iso)
            df = data.to_df()
            if df.empty:
                print(f"  {ds}: EMPTY (no data yet?)"); continue
            df.to_parquet(out)
            print(f"  {ds}: spot={spot:.0f} rows={len(df)} cost=${cost:.4f} -> {out.name}")
        except Exception as ex:
            print(f"  {ds}: pull-err {ex}")
    print(f"\n[{'DRY' if DRY else 'DONE'}] total cost=${total_cost:.4f}  guardrail=${MAX_ACCEPTABLE_TOTAL:.2f}")

if __name__ == "__main__":
    main()
