"""Multi-DTE pilot pull — Tasty-style backtest data.

For each trading day in window, pull cbbo-1m at 9:45 AM ET for SPX puts at
TARGET DTEs (7, 14, 30, 45 calendar days out). Wide strike band (0-7% OTM)
to cover the various credit/delta points we'll test.

Pilot: June 2024 only — to estimate full backtest cost.
"""
import os, sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT_DIR = Path(__file__).parent / "multi_dte_data"
OUT_DIR.mkdir(exist_ok=True)
DRY = "--dry" in sys.argv
PILOT = "--pilot" in sys.argv  # June 2024 only
FULL  = "--full"  in sys.argv  # 2022-2026

TARGET_DTES = [7, 14, 30, 45]

def _expiry_for_dte(entry_day: datetime, target_dte: int) -> datetime:
    """Return SPX expiration date closest to entry_day + target_dte.
    SPX has weekly expirations Mon/Wed/Fri and monthly 3rd Friday.
    Approximation: pick the nearest Friday (most SPXW expirations are Fri)."""
    tgt = entry_day + timedelta(days=target_dte)
    # Walk to nearest Friday
    while tgt.weekday() != 4:  # Fri
        tgt += timedelta(days=1) if tgt.weekday() < 4 else timedelta(days=-1)
        if tgt.weekday() == 4: break
    return tgt

def _format_sym(expiry: datetime, strike: int) -> str:
    yy = expiry.strftime("%y%m%d")
    return f"SPXW  {yy}P{strike:05d}000"

def main():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY env var required"); sys.exit(1)
    client = db.Historical(key)

    spx_cache = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet"
    spx_df = pd.read_parquet(spx_cache)

    if PILOT:
        start = datetime(2024, 6, 3); end = datetime(2024, 6, 28)
    elif FULL:
        start = datetime(2022, 1, 3); end = datetime(2026, 5, 21)
    else:
        print("Specify --pilot or --full or --dry (+ --pilot)"); sys.exit(1)

    mask = (spx_df.index >= start) & (spx_df.index <= end)
    days = list(spx_df.index[mask].to_pydatetime())
    print(f"[multi-dte] {'DRY-COST' if DRY else 'PULL'} {len(days)} trading days {start.date()} → {end.date()}")
    print(f"[multi-dte] target DTEs: {TARGET_DTES}, strike band: 0-7% OTM")

    total_cost = 0.0
    pulled = empty = failed = 0

    for i, day in enumerate(days):
        out = OUT_DIR / f"spxw_multi_dte_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not DRY:
            continue

        spx_open = float(spx_df.loc[day, "Open"])
        # Wide strike band: 0% to 7% OTM (covers up to 45-DTE 30-delta)
        lo = int(spx_open * 0.93)
        hi = int(spx_open * 1.005)  # tiny buffer above spot for ITM short legs
        strikes = list(range(lo, hi + 1, 5))  # 5pt grid

        symbols = []
        for dte in TARGET_DTES:
            exp = _expiry_for_dte(day, dte)
            for k in strikes:
                symbols.append(_format_sym(exp, k))

        # Tight window: 9:45 AM ET = 13:45 UTC
        start_iso = day.strftime("%Y-%m-%dT13:45:00")
        end_iso = day.strftime("%Y-%m-%dT13:46:00")

        try:
            cost = client.metadata.get_cost(
                dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                stype_in="raw_symbol", start=start_iso, end=end_iso,
                mode="historical-streaming",
            )
        except Exception as e:
            print(f"  [{i+1}/{len(days)}] {day.date()}: cost query failed — {e}")
            failed += 1; continue

        total_cost += cost
        if DRY:
            if i % 5 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} symbols={len(symbols)} cost=${cost:.5f}  running=${total_cost:.4f}")
            continue

        # Real pull
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
            if i % 5 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} symbols={len(symbols)} rows={len(df)} cost=${cost:.5f}  running=${total_cost:.4f}")
        except Exception as e:
            print(f"  [{i+1}/{len(days)}] {day.date()}: pull failed — {e}")
            failed += 1

    print(f"\n[multi-dte] DONE total_cost=${total_cost:.4f} pulled={pulled} empty={empty} failed={failed}")
    if DRY and PILOT:
        # Extrapolate to full
        full_days_est = 1090  # ~5 years of trading days
        scale = full_days_est / len(days)
        print(f"[extrapolate] Full pull would be ~${total_cost*scale:.2f} for {full_days_est} days")

if __name__ == "__main__":
    main()
