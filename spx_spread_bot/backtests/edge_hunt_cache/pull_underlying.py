"""Generic short-DTE puller for any ETF/stock with weekly options.

Usage:
  python pull_underlying.py --ticker TLT --grid 1 --otm 0.07
  python pull_underlying.py --ticker GLD --grid 1 --otm 0.05
"""
import os, sys, time, argparse
from datetime import datetime, timedelta
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

DATA_0DTE_P = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
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

def make_sym(root, expiry, strike_dollars):
    """OCC-format option symbol. Root padded to 6 chars."""
    root6 = root.ljust(6)
    return f"{root6}{expiry.strftime('%y%m%d')}P{int(strike_dollars*1000):08d}"

def pull(client, symbols, day, hh, mm, label, dry):
    s = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm:02d}:00")
    e = day.strftime(f"%Y-%m-%dT{hh:02d}:{mm+1:02d}:00")
    try:
        c = client.metadata.get_cost(dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                                     stype_in="raw_symbol", start=s, end=e, mode="historical-streaming")
    except: return None, 0.0
    if dry: return None, c
    try:
        data = client.timeseries.get_range(dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                                            stype_in="raw_symbol", start=s, end=e)
        df = data.to_df()
        if not df.empty: df["snapshot"] = label
        return df, c
    except: return None, c

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--grid", type=float, default=1.0, help="strike grid in $")
    p.add_argument("--otm", type=float, default=0.07, help="OTM band pct")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--pilot", action="store_true")
    args = p.parse_args()

    key = os.environ["DATABENTO_API_KEY"]
    client = db.Historical(key)

    out_dir = Path(__file__).parent / f"{args.ticker.lower()}_short_dte_data"
    out_dir.mkdir(exist_ok=True)

    # Underlying prices via yfinance
    hist = yf.Ticker(args.ticker).history(start="2022-05-01", end="2026-05-22", auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)

    cal = get_calendar()

    if args.pilot:
        start, end = datetime(2024, 6, 3), datetime(2024, 6, 28)
    else:
        start, end = datetime(2022, 5, 2), datetime(2026, 5, 21)

    mask = (hist.index >= start) & (hist.index <= end)
    days = list(hist.index[mask].to_pydatetime())
    print(f"[{args.ticker}] {'DRY' if args.dry else 'PULL'} {len(days)} days grid=${args.grid} otm={args.otm*100:.0f}%", flush=True)

    total, pulled, empty, failed = 0.0, 0, 0, 0
    t0 = time.monotonic()
    for i, day in enumerate(days):
        out = out_dir / f"{args.ticker.lower()}_short_dte_{day.strftime('%Y%m%d')}.parquet"
        if out.exists() and not args.dry: continue
        try:
            open_px = float(hist.loc[day, "Open"])
        except: failed += 1; continue
        exps = find_expirations(cal, day.date())
        if not exps: failed += 1; continue
        # PUT band: 0 to otm% below spot
        lo = open_px * (1 - args.otm)
        # Round to grid
        if args.grid >= 1.0:
            lo_k = int(lo / args.grid) * args.grid
            hi_k = (int(open_px / args.grid) + 1) * args.grid
            strikes = [round(k, 2) for k in [lo_k + i*args.grid for i in range(int((hi_k-lo_k)/args.grid)+1)]]
        else:
            # sub-dollar grid (0.5)
            n = int((open_px - lo) / args.grid) + 2
            strikes = [round(lo + i*args.grid, 2) for i in range(n)]
        # filter to integer-grid where strike file format requires (most underlyings use $0.5 or $1 grid)
        symbols = [make_sym(args.ticker, exp, k) for exp in exps for k in strikes if k > 0]
        if len(symbols) > 1500: symbols = symbols[:1500]
        if not symbols: failed += 1; continue
        de, c1 = pull(client, symbols, day, 13, 45, "entry", args.dry)
        dx, c2 = pull(client, symbols, day, 19, 55, "exit", args.dry)
        total += c1 + c2
        if args.dry:
            if i % 5 == 0: print(f"  [{i+1}/{len(days)}] {day.date()} sym={len(symbols)} running=${total:.4f}", flush=True)
            continue
        parts = [d for d in (de, dx) if d is not None and not d.empty]
        if not parts: empty += 1
        else: pd.concat(parts).to_parquet(out); pulled += 1
        if i % 25 == 0:
            rate = (i+1)/(time.monotonic()-t0); eta = (len(days)-i-1)/rate if rate>0 else 0
            print(f"  [{i+1}/{len(days)}] {day.date()} cost=${total:.4f} ({rate:.2f}/s ETA {eta:.0f}s)", flush=True)
    dt = time.monotonic() - t0
    print(f"\n[{args.ticker}] DONE {dt:.0f}s pulled={pulled} empty={empty} failed={failed} cost=${total:.4f}", flush=True)

if __name__ == "__main__":
    main()
