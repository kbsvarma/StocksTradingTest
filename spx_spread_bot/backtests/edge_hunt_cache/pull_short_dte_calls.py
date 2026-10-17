"""Short-DTE CALL pull — twin of pull_short_dte_intraday.py for calls.
Same days (2-7 DTE expirations), 0-7% OTM CALL band, 09:45 + 15:55 snapshots.
"""
import os, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import databento as db
import pandas as pd

OUT_DIR = Path(__file__).parent / "short_dte_calls_data"
OUT_DIR.mkdir(exist_ok=True)
DATA_0DTE_P = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"

DRY = "--dry" in sys.argv
FULL = "--full" in sys.argv
TARGET_DTES = [2, 3, 4, 5, 6, 7]

def get_calendar():
    files = sorted(DATA_0DTE_P.glob("spxw_p_cbbo_*.parquet"))
    return sorted(datetime.strptime(f.stem.split("_")[-1], "%Y%m%d").date() for f in files)

def find_expirations(cal, ed):
    future = [d for d in cal if d > ed]
    out, seen = [], set()
    for dte in TARGET_DTES:
        tgt = ed + timedelta(days=dte)
        if not future: continue
        best = min(future, key=lambda x: abs((x - tgt).days))
        if abs((best - tgt).days) <= 2 and best not in seen:
            seen.add(best); out.append(best)
    return out

def sym(expiry, strike):
    return f"SPXW  {expiry.strftime('%y%m%d')}C{strike:05d}000"

def pull(client, symbols, day, hh, mm, label):
    s = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm:02d}:00")
    e = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm+1:02d}:00")
    try:
        c = client.metadata.get_cost(dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                                     stype_in="raw_symbol", start=s, end=e, mode="historical-streaming")
    except: return None, 0.0
    if DRY: return None, c
    try:
        data = client.timeseries.get_range(dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                                            stype_in="raw_symbol", start=s, end=e)
        df = data.to_df()
        if not df.empty: df["snapshot"] = label
        return df, c
    except: return None, c

def main():
    key = os.environ["DATABENTO_API_KEY"]
    client = db.Historical(key)
    spx_cache = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet"
    spx_df = pd.read_parquet(spx_cache)

    start, end = datetime(2022, 5, 1), datetime(2026, 5, 21)
    cal = get_calendar()
    mask = (spx_df.index >= start) & (spx_df.index <= end)
    days = list(spx_df.index[mask].to_pydatetime())
    print(f"[calls] {len(days)} days", flush=True)

    total, pulled, empty, failed = 0.0, 0, 0, 0
    t0 = time.monotonic()
    for i, day in enumerate(days):
        out = OUT_DIR / f"short_dte_calls_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not DRY: continue
        spx_open = float(spx_df.loc[day, "Open"])
        exps = find_expirations(cal, day.date())
        if not exps: failed += 1; continue
        # CALL strikes: 0-7% OTM (above spot)
        lo = (int(spx_open / 25)) * 25
        hi = int(spx_open * 1.07 / 25) * 25 + 25
        strikes = list(range(lo, hi + 1, 25))
        symbols = [sym(exp, k) for exp in exps for k in strikes]
        de, c1 = pull(client, symbols, day, 13, 45, "entry")
        dx, c2 = pull(client, symbols, day, 19, 55, "exit")
        total += c1 + c2
        if DRY:
            if i % 25 == 0: print(f"  [{i+1}/{len(days)}] {day.date()} sym={len(symbols)} cost=${total:.4f}", flush=True)
            continue
        parts = [d for d in (de, dx) if d is not None and not d.empty]
        if not parts: empty += 1
        else: pd.concat(parts).to_parquet(out); pulled += 1
        if i % 25 == 0:
            rate = (i+1)/(time.monotonic()-t0); eta = (len(days)-i-1)/rate if rate>0 else 0
            print(f"  [{i+1}/{len(days)}] {day.date()} cost=${total:.4f} ({rate:.2f}/s ETA {eta:.0f}s)", flush=True)
    dt = time.monotonic() - t0
    print(f"\n[calls] DONE {dt:.0f}s pulled={pulled} empty={empty} failed={failed} cost=${total:.4f}", flush=True)

if __name__ == "__main__":
    main()
