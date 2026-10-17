"""Pull the 6 missing SPXW 0DTE put days (May 21-29 2026) in spxw_p_cbbo_1m format.
Full-minute cbbo (13:31-20:00 UTC) so we can simulate 10:30 entry + intraday SL.
"""
import os, sys, time
from datetime import datetime
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "spxw_p_cbbo_1m"
OUT.mkdir(exist_ok=True)
DRY = "--dry" in sys.argv

DAYS = ["20260521","20260522","20260526","20260527","20260528","20260529"]

def main():
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    spx = yf.Ticker("^GSPC").history(start="2026-05-15", end="2026-05-30", auto_adjust=False)
    spx.index = pd.to_datetime(spx.index).tz_localize(None)

    total = 0.0
    for ds in DAYS:
        out = OUT / f"spxw_p_cbbo_{ds}.parquet"
        if out.exists() and not DRY:
            print(f"  {ds}: exists, skip"); continue
        d = datetime.strptime(ds, "%Y%m%d")
        try:
            spot = float(spx.loc[pd.Timestamp(d.date()), "Open"])
        except KeyError:
            print(f"  {ds}: no spot, skip"); continue
        # strike band: spot*0.92 .. spot*1.01, $5 grid (covers 1% OTM short + 50pt long + SL room)
        lo = int(spot*0.92/5)*5; hi = int(spot*1.01/5)*5
        strikes = list(range(lo, hi+5, 5))
        yy = d.strftime("%y%m%d")
        syms = [f"SPXW  {yy}P{k*1000:08d}" for k in strikes]
        s_iso = d.strftime("%Y-%m-%dT13:31:00"); e_iso = d.strftime("%Y-%m-%dT20:01:00")
        try:
            cost = client.metadata.get_cost(dataset="OPRA.PILLAR", schema="cbbo-1m",
                   symbols=syms, stype_in="raw_symbol", start=s_iso, end=e_iso)
            total += cost
        except Exception as ex:
            print(f"  {ds}: cost-err {ex}"); continue
        if DRY:
            print(f"  {ds}: spot={spot:.0f} strikes={len(syms)} cost=${cost:.4f} running=${total:.4f}")
            continue
        try:
            data = client.timeseries.get_range(dataset="OPRA.PILLAR", schema="cbbo-1m",
                   symbols=syms, stype_in="raw_symbol", start=s_iso, end=e_iso)
            df = data.to_df()
            if df.empty: print(f"  {ds}: EMPTY"); continue
            df.to_parquet(out)
            print(f"  {ds}: spot={spot:.0f} rows={len(df)} cost=${cost:.4f} -> {out.name}")
        except Exception as ex:
            print(f"  {ds}: pull-err {ex}")
    print(f"\n[done] total=${total:.4f}")

if __name__ == "__main__":
    main()
