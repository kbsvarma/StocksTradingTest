"""Multi-DTE pull v2 — uses EXISTING 0DTE file list as expiration calendar.

Any date with a 0DTE put file IS a valid expiration. Eliminates slow definition queries.
"""
import os, sys, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd

OUT_DIR = Path(__file__).parent / "multi_dte_data"
OUT_DIR.mkdir(exist_ok=True)
DATA_0DTE_P = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"

DRY = "--dry" in sys.argv
PILOT = "--pilot" in sys.argv
FULL = "--full" in sys.argv

TARGET_DTES = [7, 14, 30, 45]

def get_calendar():
    """Calendar = all dates with existing 0DTE data (= valid expiration dates)."""
    files = sorted(DATA_0DTE_P.glob("spxw_p_cbbo_*.parquet"))
    dates = []
    for f in files:
        ds = f.stem.split("_")[-1]
        dates.append(datetime.strptime(ds, "%Y%m%d").date())
    return sorted(dates)

def find_expirations(calendar, entry_day):
    """For each target DTE, find the nearest expiration in calendar (after entry_day)."""
    future = [d for d in calendar if d > entry_day]
    out = []
    for dte in TARGET_DTES:
        tgt = entry_day + timedelta(days=dte)
        # Find nearest in future
        if not future: continue
        best = min(future, key=lambda x: abs((x - tgt).days))
        if abs((best - tgt).days) <= 5:  # within 5 days
            out.append((dte, best))
    return out

def format_sym(expiry, strike):
    yy = expiry.strftime("%y%m%d")
    return f"SPXW  {yy}P{strike:05d}000"

def main():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY env required"); sys.exit(1)
    client = db.Historical(key)

    spx_cache = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet"
    spx_df = pd.read_parquet(spx_cache)

    if PILOT:
        start, end = datetime(2024, 6, 3), datetime(2024, 6, 28)
    elif FULL:
        start, end = datetime(2022, 5, 1), datetime(2026, 5, 21)
    else:
        print("Specify --pilot or --full"); sys.exit(1)

    calendar = get_calendar()
    print(f"[calendar] {len(calendar)} valid expiration dates from existing 0DTE files")

    mask = (spx_df.index >= start) & (spx_df.index <= end)
    days = list(spx_df.index[mask].to_pydatetime())
    print(f"[multi-dte] {'DRY' if DRY else 'PULL'} {len(days)} days {start.date()} → {end.date()}\n", flush=True)

    total_cost = 0.0
    pulled = empty = failed = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        out = OUT_DIR / f"spxw_multi_dte_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not DRY:
            continue

        spx_open = float(spx_df.loc[day, "Open"])
        targets = find_expirations(calendar, day.date())
        if not targets:
            failed += 1; continue

        # $25 strike grid, 0-7% OTM
        lo = int(spx_open * 0.93 / 25) * 25
        hi = (int(spx_open / 25) + 1) * 25
        strikes = list(range(lo, hi + 1, 25))

        symbols = []
        for dte, exp in targets:
            for k in strikes:
                symbols.append(format_sym(exp, k))

        start_iso = day.strftime("%Y-%m-%dT13:45:00")
        end_iso = day.strftime("%Y-%m-%dT13:46:00")

        try:
            cost = client.metadata.get_cost(
                dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                stype_in="raw_symbol", start=start_iso, end=end_iso,
                mode="historical-streaming",
            )
        except Exception as e:
            failed += 1; continue

        total_cost += cost
        if DRY:
            if i % 25 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} symbols={len(symbols)} running=${total_cost:.4f}", flush=True)
            continue

        try:
            data = client.timeseries.get_range(
                dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                stype_in="raw_symbol", start=start_iso, end=end_iso,
            )
            df = data.to_df()
            if df.empty:
                empty += 1
            else:
                df.to_parquet(out)
                pulled += 1
            if i % 25 == 0:
                rate = (i+1) / (time.monotonic() - t0)
                eta = (len(days) - i - 1) / rate
                print(f"  [{i+1}/{len(days)}] {day.date()} symbols={len(symbols)} cost=${total_cost:.4f} ({rate:.1f}/s, ETA {eta:.0f}s)", flush=True)
        except Exception as e:
            failed += 1

    dt = time.monotonic() - t0
    print(f"\n[multi-dte] DONE in {dt:.0f}s  pulled={pulled} empty={empty} failed={failed} cost=${total_cost:.4f}")

if __name__ == "__main__":
    main()
