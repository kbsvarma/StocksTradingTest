"""Short-DTE intraday pull: 2-7 DTE puts, 09:45 entry + 15:55 exit snapshots.

Lets us backtest "buy 2-7 DTE BPS at 9:45, close 15:55 same day".
"""
import os, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import databento as db
import pandas as pd

OUT_DIR = Path(__file__).parent / "short_dte_intraday_data"
OUT_DIR.mkdir(exist_ok=True)
DATA_0DTE_P = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"

DRY = "--dry" in sys.argv
PILOT = "--pilot" in sys.argv
FULL = "--full" in sys.argv

TARGET_DTES = [2, 3, 4, 5, 6, 7]

def get_calendar():
    files = sorted(DATA_0DTE_P.glob("spxw_p_cbbo_*.parquet"))
    return sorted(datetime.strptime(f.stem.split("_")[-1], "%Y%m%d").date() for f in files)

def find_expirations(calendar, entry_day):
    future = [d for d in calendar if d > entry_day]
    out = []
    for dte in TARGET_DTES:
        tgt = entry_day + timedelta(days=dte)
        if not future: continue
        best = min(future, key=lambda x: abs((x - tgt).days))
        if abs((best - tgt).days) <= 2:
            out.append((dte, best))
    # dedupe by expiration
    seen = set(); uniq = []
    for dte, exp in out:
        if exp not in seen:
            seen.add(exp); uniq.append((dte, exp))
    return uniq

def format_sym(expiry, strike):
    return f"SPXW  {expiry.strftime('%y%m%d')}P{strike:05d}000"

def pull_minute(client, symbols, day, hh, mm, label):
    start_iso = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm:02d}:00")
    end_iso = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm+1:02d}:00")
    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
            mode="historical-streaming",
        )
    except Exception:
        return None, 0.0
    if DRY:
        return None, cost
    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
        df = data.to_df()
        if not df.empty:
            df["snapshot"] = label
        return df, cost
    except Exception:
        return None, cost

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
    print(f"[calendar] {len(calendar)} expiration dates", flush=True)

    mask = (spx_df.index >= start) & (spx_df.index <= end)
    days = list(spx_df.index[mask].to_pydatetime())
    print(f"[short-dte] {'DRY' if DRY else 'PULL'} {len(days)} days {start.date()} → {end.date()}", flush=True)

    total_cost = 0.0
    pulled = empty = failed = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        out = OUT_DIR / f"short_dte_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not DRY:
            continue

        spx_open = float(spx_df.loc[day, "Open"])
        targets = find_expirations(calendar, day.date())
        if not targets:
            failed += 1; continue

        lo = int(spx_open * 0.93 / 25) * 25
        hi = (int(spx_open / 25) + 1) * 25
        strikes = list(range(lo, hi + 1, 25))

        symbols = [format_sym(exp, k) for _, exp in targets for k in strikes]

        # 09:45 ET = 13:45 UTC; 15:55 ET = 19:55 UTC
        df_entry, c1 = pull_minute(client, symbols, day, 13, 45, "entry")
        df_exit,  c2 = pull_minute(client, symbols, day, 19, 55, "exit")
        total_cost += c1 + c2

        if DRY:
            if i % 25 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} sym={len(symbols)} running=${total_cost:.4f}", flush=True)
            continue

        parts = [df for df in (df_entry, df_exit) if df is not None and not df.empty]
        if not parts:
            empty += 1
        else:
            pd.concat(parts).to_parquet(out)
            pulled += 1

        if i % 25 == 0:
            rate = (i+1) / (time.monotonic() - t0)
            eta = (len(days) - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1}/{len(days)}] {day.date()} sym={len(symbols)} cost=${total_cost:.4f} ({rate:.2f}/s ETA {eta:.0f}s)", flush=True)

    dt = time.monotonic() - t0
    print(f"\n[short-dte] DONE in {dt:.0f}s pulled={pulled} empty={empty} failed={failed} cost=${total_cost:.4f}", flush=True)

if __name__ == "__main__":
    main()
