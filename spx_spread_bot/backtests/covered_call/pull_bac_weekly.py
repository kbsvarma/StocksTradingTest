"""Pull BAC weekly ATM call quotes Monday morning for covered-call backtest.

For each Monday in the date range:
  - Get BAC spot from yfinance daily OHLC
  - Determine ATM strike (rounded to $1)
  - Find next Friday expiry
  - Pull 1-min cbbo for that single option at 09:35 ET (= 13:35 UTC)
  - Also pull strikes near ATM (±$2) for robustness

Output: data/bac_cc_weekly.parquet — one row per (week, strike).
"""
import os, sys, argparse, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True, parents=True)

def make_call_sym(expiry: date, strike: float) -> str:
    """OCC format: BAC + YYMMDD + C + strike(8 = 5d.3d)."""
    root = "BAC".ljust(6)
    yy = expiry.strftime("%y%m%d")
    return f"{root}{yy}C{int(round(strike*1000)):08d}"

def find_mondays(start: date, end: date):
    """Return list of (monday, friday) tuples for each week in range."""
    pairs = []
    d = start
    # advance to first Monday
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
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end",   default="2026-05-22")
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if not (args.confirm or args.dry_cost):
        print("ERROR: specify --confirm or --dry-cost"); sys.exit(1)

    key = os.environ["DATABENTO_API_KEY"]
    client = db.Historical(key)

    # 1. BAC daily history via yfinance
    print(f"[BAC] fetching BAC daily OHLC {args.start}→{args.end}...")
    hist = yf.Ticker("BAC").history(start=args.start, end=args.end, auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    print(f"[BAC] got {len(hist)} trading days")

    # 2. Build week list
    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    week_pairs = find_mondays(start, end)
    print(f"[BAC] {len(week_pairs)} Mon-Fri weeks in range")

    # 3. For each Monday, build symbol list: ATM ± $3 (7 strikes), $1 grid
    symbols_per_week = []
    for mon, fri in week_pairs:
        ts = pd.Timestamp(mon)
        # find nearest trading day at-or-after Monday
        idx = hist.index.get_indexer([ts], method="bfill")[0]
        if idx < 0 or idx >= len(hist): continue
        actual_mon = hist.index[idx].date()
        if actual_mon > mon + timedelta(days=2):  # skip holiday weeks
            continue
        spot = float(hist.iloc[idx]["Open"])
        atm = round(spot)  # $1 grid
        strikes = [atm + i for i in range(-3, 4)]  # 7 strikes
        syms = [make_call_sym(fri, k) for k in strikes if k > 0]
        symbols_per_week.append((actual_mon, fri, spot, atm, syms))

    print(f"[BAC] {len(symbols_per_week)} valid weeks (after holidays)")
    print(f"[BAC] sample week: {symbols_per_week[0]}")

    # 4. Pull 1-min cbbo at 09:35 ET (13:35 UTC) for each (monday, symbols)
    total_cost = 0.0; pulled = 0; failed = 0
    rows = []
    t0 = time.monotonic()
    for i, (mon, fri, spot, atm, syms) in enumerate(symbols_per_week):
        start_iso = mon.strftime("%Y-%m-%dT13:35:00")
        end_iso   = mon.strftime("%Y-%m-%dT13:36:00")
        try:
            cost = client.metadata.get_cost(
                dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=syms,
                stype_in="raw_symbol", start=start_iso, end=end_iso,
            )
        except Exception as e:
            failed += 1
            if i % 10 == 0: print(f"  [{i+1}/{len(symbols_per_week)}] {mon} cost-err {e}")
            continue
        total_cost += cost
        if args.dry_cost:
            if i % 20 == 0:
                print(f"  [{i+1}/{len(symbols_per_week)}] {mon} spot=${spot:.2f} atm={atm} cost=${cost:.5f} running=${total_cost:.4f}", flush=True)
            continue
        try:
            data = client.timeseries.get_range(
                dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=syms,
                stype_in="raw_symbol", start=start_iso, end=end_iso,
            )
            df = data.to_df()
            if df.empty:
                failed += 1; continue
            df["monday"] = mon
            df["friday"] = fri
            df["spot"] = spot
            df["atm_strike"] = atm
            rows.append(df.reset_index())
            pulled += 1
        except Exception as e:
            failed += 1
        if i % 20 == 0:
            rate = (i+1) / (time.monotonic() - t0)
            print(f"  [{i+1}/{len(symbols_per_week)}] {mon} spot=${spot:.2f} cost=${total_cost:.4f} ({rate:.1f}/s)", flush=True)

    if args.dry_cost:
        print(f"\n[dry-cost] PROJECTED TOTAL: ${total_cost:.3f} ({len(symbols_per_week)} weeks)")
        return

    if rows:
        merged = pd.concat(rows, ignore_index=True)
        out_fp = OUT / "bac_cc_weekly.parquet"
        merged.to_parquet(out_fp)
        print(f"\n[done] {pulled} weeks pulled, {failed} failed, total=${total_cost:.3f} → {out_fp}")
        print(f"  rows={len(merged)}  unique_symbols={merged['symbol'].nunique()}")

if __name__ == "__main__":
    main()
