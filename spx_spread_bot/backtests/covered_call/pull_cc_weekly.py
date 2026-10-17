"""Generic weekly ATM call pull for covered-call backtest.
Usage: python pull_cc_weekly.py --ticker MO --start 2024-01-01 --end 2026-05-22 --confirm
"""
import os, sys, argparse, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True, parents=True)

# Strike-grid per ticker
GRID = {
    "BAC": 1.0, "MO": 1.0, "VZ": 1.0, "GILD": 1.0,
    "MRK": 1.0, "CSCO": 1.0, "CVS": 1.0, "PFE": 0.5,
    "BMY": 1.0, "TGT": 2.5, "AMGN": 2.5,
    "XLF": 1.0,   # Financial Select ETF — $40-50 range
    "SOFI": 0.5,  # SoFi Tech — $10-30 range
    "WFC": 1.0,   # Wells Fargo
    "OXY": 1.0,   # Occidental Petroleum
    "T": 0.5,     # AT&T — $20-30 range
    "HPQ": 0.5,   # HP Inc. — $20-30 range
    "MDT": 1.0,   # Medtronic — $70-100 range
    "VALE": 0.5,  # Vale SA — $10-20 range
}

def make_call_sym(ticker: str, expiry: date, strike: float) -> str:
    root = ticker.ljust(6)
    yy = expiry.strftime("%y%m%d")
    return f"{root}{yy}C{int(round(strike*1000)):08d}"

def find_mondays(start, end):
    pairs = []
    d = start
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= end:
        friday = d + timedelta(days=4)
        if friday <= end:
            pairs.append((d, friday))
        d += timedelta(days=7)
    return pairs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end",   default="2026-05-22")
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if not (args.confirm or args.dry_cost):
        print("ERROR: specify --confirm or --dry-cost"); sys.exit(1)

    ticker = args.ticker.upper()
    grid = GRID.get(ticker, 1.0)
    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    print(f"[{ticker}] grid=${grid}, fetching yfinance...")
    hist = yf.Ticker(ticker).history(start=args.start, end=args.end, auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    print(f"[{ticker}] {len(hist)} trading days")

    start = date.fromisoformat(args.start); end = date.fromisoformat(args.end)
    week_pairs = find_mondays(start, end)

    weeks = []
    for mon, fri in week_pairs:
        idx = hist.index.get_indexer([pd.Timestamp(mon)], method="bfill")[0]
        if idx < 0 or idx >= len(hist): continue
        actual_mon = hist.index[idx].date()
        if actual_mon > mon + timedelta(days=2): continue
        spot = float(hist.iloc[idx]["Open"])
        atm = round(spot / grid) * grid
        # 7 strikes around ATM at native grid
        strikes = [atm + i*grid for i in range(-3, 4)]
        syms = [make_call_sym(ticker, fri, k) for k in strikes if k > 0]
        weeks.append((actual_mon, fri, spot, atm, syms))

    print(f"[{ticker}] {len(weeks)} valid Mon-Fri weeks")
    total_cost = 0.0; pulled = 0; failed = 0; rows = []
    t0 = time.monotonic()
    for i, (mon, fri, spot, atm, syms) in enumerate(weeks):
        # Two pull windows per week: Monday 9:35 ET (entry) + Friday 15:55 ET (settlement)
        windows = [
            (mon.strftime("%Y-%m-%dT13:35:00"), mon.strftime("%Y-%m-%dT13:36:00"), "entry"),
            (fri.strftime("%Y-%m-%dT19:55:00"), fri.strftime("%Y-%m-%dT19:56:00"), "exit"),
        ]
        try:
            week_cost = 0.0
            for w_start, w_end, _ in windows:
                week_cost += client.metadata.get_cost(
                    dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=syms,
                    stype_in="raw_symbol", start=w_start, end=w_end,
                )
        except Exception as e:
            failed += 1; continue
        total_cost += week_cost
        if args.dry_cost:
            if i % 25 == 0: print(f"  [{i+1}/{len(weeks)}] {mon} ${total_cost:.4f}", flush=True)
            continue
        try:
            for w_start, w_end, w_label in windows:
                data = client.timeseries.get_range(
                    dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=syms,
                    stype_in="raw_symbol", start=w_start, end=w_end,
                )
                df = data.to_df()
                if df.empty: continue
                df["monday"] = mon; df["friday"] = fri
                df["spot"] = spot; df["atm_strike"] = atm
                df["window"] = w_label
                rows.append(df.reset_index())
            pulled += 1
        except Exception as e:
            failed += 1
        if i % 25 == 0:
            print(f"  [{i+1}/{len(weeks)}] {mon} spot=${spot:.2f} cost=${total_cost:.4f}", flush=True)

    dt = time.monotonic() - t0
    if args.dry_cost:
        print(f"\n[{ticker} dry-cost] PROJECTED: ${total_cost:.3f}")
        return
    if rows:
        merged = pd.concat(rows, ignore_index=True)
        out_fp = OUT / f"{ticker.lower()}_cc_weekly.parquet"
        merged.to_parquet(out_fp)
        print(f"\n[{ticker} done] {pulled} weeks pulled, {failed} failed, total=${total_cost:.3f} → {out_fp}")

if __name__ == "__main__":
    main()
