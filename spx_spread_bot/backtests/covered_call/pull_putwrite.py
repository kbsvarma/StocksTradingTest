"""Pull SPY monthly PUT data for a defensive put-write backtest.

Strategy modeled: on each monthly expiration (3rd Friday), sell a ~30-DTE
OTM put expiring next 3rd Friday. Hold to expiry. Cash earns T-bill yield.
Settlement is deterministic: put payoff = max(0, strike - SPY_at_expiry),
so we only need the ENTRY price of each candidate put.

We pull a band of OTM put strikes (90%-100% of spot) at each monthly roll,
2013-2025, as daily bars (cheap). One chunked request.

Usage:
  export DATABENTO_API_KEY=...
  python pull_putwrite.py --dry-cost
  python pull_putwrite.py --confirm
"""
import os, sys, argparse, time
from datetime import date, timedelta
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True, parents=True)


def third_friday(year, month):
    d = date(year, month, 1)
    # first friday
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d + timedelta(days=14)  # third friday


def make_put_sym(expiry, strike):
    root = "SPY".ljust(6)
    yy = expiry.strftime("%y%m%d")
    return f"{root}{yy}P{int(round(strike*1000)):08d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2013)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    if not (args.confirm or args.dry_cost):
        print("ERROR: --confirm or --dry-cost"); sys.exit(1)

    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    # SPY spot history
    hist = yf.Ticker("SPY").history(start=f"{args.start_year}-01-01",
                                    end=f"{args.end_year}-12-31", auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    spot_by_date = {d.date(): float(r["Open"]) for d, r in hist.iterrows()}

    # Build monthly roll list: entry = this 3rd Friday, expiry = next 3rd Friday
    rolls = []
    for y in range(args.start_year, args.end_year + 1):
        for m in range(1, 13):
            entry = third_friday(y, m)
            ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
            expiry = third_friday(ny, nm)
            rolls.append((entry, expiry))

    syms = set()
    valid_rolls = 0
    for entry, expiry in rolls:
        # nearest trading day spot on/after entry
        spot = None
        for off in range(0, 5):
            d = entry + timedelta(days=off)
            if d in spot_by_date:
                spot = spot_by_date[d]; break
        if spot is None:
            continue
        valid_rolls += 1
        # OTM put strikes 90%..100% of spot, at $1 grid
        for pct in range(90, 101):
            k = round(spot * pct / 100)
            if k > 0:
                syms.add(make_put_sym(expiry, float(k)))
    syms = sorted(syms)
    print(f"[SPY putwrite] {valid_rolls} monthly rolls, {len(syms)} put symbols", flush=True)

    start = f"{args.start_year}-01-01"; end = f"{args.end_year}-12-31"
    CHUNK = 1500
    chunks = [syms[i:i+CHUNK] for i in range(0, len(syms), CHUNK)]
    t0 = time.monotonic()
    cost = 0.0
    for ch in chunks:
        cost += client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="ohlcv-1d", symbols=ch,
            stype_in="raw_symbol", start=start, end=end)
    print(f"[SPY putwrite] cost ({len(chunks)} chunks) = ${cost:.4f} ({time.monotonic()-t0:.1f}s)", flush=True)
    if args.dry_cost:
        print(f"[dry-cost] PROJECTED: ${cost:.4f}"); return

    parts = []
    for j, ch in enumerate(chunks):
        d = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="ohlcv-1d", symbols=ch,
            stype_in="raw_symbol", start=start, end=end).to_df()
        if not d.empty: parts.append(d)
        print(f"  chunk {j+1}/{len(chunks)}: {len(d):,} rows", flush=True)
    df = pd.concat(parts) if parts else pd.DataFrame()
    out = OUT / "spy_putwrite_monthly.parquet"
    df.to_parquet(out)
    print(f"[done] {len(df):,} rows, cost=${cost:.4f} → {out}")


if __name__ == "__main__":
    main()
