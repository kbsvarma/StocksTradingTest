"""Multi-DTE pull v2 — uses definition schema to find actual listed expirations.

Two-phase:
  Phase 1: Build expiration calendar for each entry day (cheap)
  Phase 2: Pull cbbo-1m at 9:45 AM for puts at target DTEs (cost-controlled)

Usage:
  python pull_multi_dte.py --dry --pilot      (cost estimate, 1 month)
  python pull_multi_dte.py --pilot            (actual pull, 1 month)
  python pull_multi_dte.py --full             (full 2022-2026)
"""
import os, sys, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd

OUT_DIR = Path(__file__).parent / "multi_dte_data"
OUT_DIR.mkdir(exist_ok=True)
EXP_CALENDAR = Path(__file__).parent / "spx_expiration_calendar.parquet"

DRY = "--dry" in sys.argv
PILOT = "--pilot" in sys.argv
FULL = "--full" in sys.argv

TARGET_DTES = [7, 14, 30, 45]

def build_expiration_calendar(client, start_date, end_date):
    """For each Monday in window, query SPXW expirations available that week.
    Cache the calendar. Cheap because definition queries are small."""
    if EXP_CALENDAR.exists():
        cal = pd.read_parquet(EXP_CALENDAR)
        cal_start = cal["query_date"].min()
        cal_end = cal["query_date"].max()
        # If cached calendar covers our window, use it
        if cal_start <= pd.Timestamp(start_date).date() and cal_end >= pd.Timestamp(end_date).date():
            print(f"[calendar] Using cached calendar ({len(cal)} rows, {cal_start} → {cal_end})")
            return cal
        print(f"[calendar] Cache exists but only covers {cal_start} → {cal_end}; rebuilding...")

    print(f"[calendar] Building expiration calendar {start_date} → {end_date}...")
    # Sample one Monday per month — enough to get the pattern
    sample_dates = []
    d = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    while d <= end:
        # Find Monday in this week
        while d.weekday() != 0:
            d += pd.Timedelta(days=1)
            if d > end: break
        if d <= end:
            sample_dates.append(d)
        d += pd.Timedelta(days=7)

    rows = []
    for i, qd in enumerate(sample_dates):
        try:
            data = client.timeseries.get_range(
                dataset="OPRA.PILLAR", schema="definition", symbols=["SPXW.OPT"],
                stype_in="parent",
                start=qd.strftime("%Y-%m-%d"),
                end=(qd + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            df = data.to_df()
            if df.empty: continue
            df["expiration"] = pd.to_datetime(df["expiration"]).dt.date
            expirys = sorted(set(df["expiration"]))
            for e in expirys:
                rows.append({"query_date": qd.date(), "expiration": e})
            if i % 10 == 0:
                print(f"  [{i+1}/{len(sample_dates)}] {qd.date()}: {len(expirys)} expirations")
        except Exception as exc:
            print(f"  [{i+1}/{len(sample_dates)}] {qd.date()}: FAIL {exc}")

    cal = pd.DataFrame(rows)
    cal.to_parquet(EXP_CALENDAR)
    print(f"[calendar] Saved {len(cal)} rows → {EXP_CALENDAR}")
    return cal

def find_expirations(cal, entry_day):
    """Return list of (target_dte, actual_expiry_date) for this entry day."""
    e = entry_day if isinstance(entry_day, date) else entry_day.date()
    # Find query_date closest to entry_day, take its expirations as the listed set
    cal_dates = sorted(cal["query_date"].unique())
    nearest_q = min(cal_dates, key=lambda x: abs((x - e).days))
    listed = sorted(cal[cal["query_date"]==nearest_q]["expiration"].unique())
    listed = [exp for exp in listed if exp > e]  # only future expirations

    out = []
    for dte in TARGET_DTES:
        tgt = e + timedelta(days=dte)
        # Find closest listed expiration to target
        if not listed: continue
        best = min(listed, key=lambda x: abs((x - tgt).days))
        # Reject if too far off
        if abs((best - tgt).days) <= 5:
            out.append((dte, best))
    return out

def format_sym(expiry, strike):
    yy = expiry.strftime("%y%m%d") if isinstance(expiry, datetime) else expiry.strftime("%y%m%d")
    return f"SPXW  {yy}P{strike:05d}000"

def main():
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY env var required"); sys.exit(1)
    client = db.Historical(key)

    spx_cache = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet"
    spx_df = pd.read_parquet(spx_cache)

    if PILOT:
        start, end = datetime(2024, 6, 3), datetime(2024, 6, 28)
    elif FULL:
        start, end = datetime(2022, 5, 1), datetime(2026, 5, 21)
    else:
        print("Specify --pilot or --full"); sys.exit(1)

    # Phase 1: expiration calendar
    cal = build_expiration_calendar(client, start, end)

    # Phase 2: pull cbbo-1m for each entry day's target expirations
    mask = (spx_df.index >= start) & (spx_df.index <= end)
    days = list(spx_df.index[mask].to_pydatetime())
    print(f"\n[multi-dte] {'DRY' if DRY else 'PULL'} {len(days)} trading days {start.date()} → {end.date()}")

    total_cost = 0.0
    pulled = empty = failed = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        out = OUT_DIR / f"spxw_multi_dte_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not DRY:
            continue

        spx_open = float(spx_df.loc[day, "Open"])

        # Find actual listed expirations for our target DTEs
        targets = find_expirations(cal, day.date())
        if not targets:
            print(f"  [{i+1}/{len(days)}] {day.date()}: no expirations resolved — skip")
            failed += 1; continue

        # Strike band: 0-7% OTM, $25 grid (multi-DTE uses $25, not $5 like 0DTE)
        lo = int(spx_open * 0.93 / 25) * 25
        hi = (int(spx_open / 25) + 1) * 25  # round up past spot
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
            print(f"  [{i+1}/{len(days)}] {day.date()}: cost FAIL — {e}")
            failed += 1; continue

        total_cost += cost
        if DRY:
            if i % 5 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} expirations={len(targets)} symbols={len(symbols)} cost=${cost:.5f}  running=${total_cost:.4f}")
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
                # Tag each row with its target DTE
                exp_to_dte = {exp: dte for dte, exp in targets}
                df["expiration"] = pd.to_datetime(df["expiration"]).dt.date if "expiration" in df.columns else None
                df["entry_date"] = day.date()
                df.to_parquet(out)
                pulled += 1
            if i % 5 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} symbols={len(symbols)} rows={len(df)} cost=${cost:.5f}  running=${total_cost:.4f}")
        except Exception as e:
            print(f"  [{i+1}/{len(days)}] {day.date()}: pull FAIL — {e}")
            failed += 1

    dt = time.monotonic() - t0
    print(f"\n[multi-dte] DONE in {dt:.0f}s  pulled={pulled} empty={empty} failed={failed} cost=${total_cost:.4f}")

    if DRY and PILOT:
        full_days_est = 1010  # ~4 years post-May-2022
        scale = full_days_est / len(days)
        print(f"[extrapolate] Full pull would be ~${total_cost*scale:.2f} for {full_days_est} days")

if __name__ == "__main__":
    main()
