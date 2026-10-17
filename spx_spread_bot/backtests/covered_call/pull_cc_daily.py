"""Fast + cheap covered-call data pull.

Strategy:
- Use yfinance (1 call) to get daily spot history → find each Mon-Fri week's ATM.
- Build a list of only the ATM-band weekly CALL symbols we need (~7 per week).
- Make ONE daily-bar (ohlcv-1d) get_range call for the FULL date range with that
  symbol list. Daily granularity is all a weekly-hold covered call needs, and
  restricting to ATM-band symbols keeps cost low (vs pulling all strikes).

Usage:
  export DATABENTO_API_KEY=...
  python pull_cc_daily.py --ticker XLF --dry-cost
  python pull_cc_daily.py --ticker XLF --confirm
"""
import os, sys, argparse, time
from datetime import date, timedelta
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True, parents=True)

GRID = {
    "XLF": 1.0, "SOFI": 0.5, "WFC": 1.0, "OXY": 1.0, "T": 0.5,
    "HPQ": 0.5, "MDT": 1.0, "VALE": 0.5, "PFE": 0.5,
}


def make_call_sym(ticker, expiry, strike):
    root = ticker.ljust(6)
    yy = expiry.strftime("%y%m%d")
    return f"{root}{yy}C{int(round(strike*1000)):08d}"


def find_fridays(start, end):
    """All Mon-Fri pairs in range."""
    pairs = []
    d = start
    while d.weekday() != 0:
        d += timedelta(days=1)
    while d <= end:
        fri = d + timedelta(days=4)
        if fri <= end:
            pairs.append((d, fri))
        d += timedelta(days=7)
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-05-22")
    ap.add_argument("--band", type=int, default=3, help="strikes each side of ATM")
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if not (args.confirm or args.dry_cost):
        print("ERROR: specify --confirm or --dry-cost"); sys.exit(1)

    ticker = args.ticker.upper()
    grid = GRID.get(ticker, 1.0)
    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    # 1 yfinance call for spot history
    hist = yf.Ticker(ticker).history(start=args.start, end=args.end, auto_adjust=False)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)

    start = date.fromisoformat(args.start); end = date.fromisoformat(args.end)
    weeks = find_fridays(start, end)

    syms = set()
    for mon, fri in weeks:
        idx = hist.index.get_indexer([pd.Timestamp(mon)], method="bfill")[0]
        if idx < 0 or idx >= len(hist):
            continue
        spot = float(hist.iloc[idx]["Open"])
        atm = round(spot / grid) * grid
        for k in [atm + i * grid for i in range(-args.band, args.band + 1)]:
            if k > 0:
                syms.add(make_call_sym(ticker, fri, k))
    syms = sorted(syms)
    print(f"[{ticker}] {len(weeks)} weeks, {len(syms)} unique ATM-band call symbols", flush=True)

    # DataBento caps at 2000 symbols/request — chunk into batches.
    CHUNK = 1500
    chunks = [syms[i:i+CHUNK] for i in range(0, len(syms), CHUNK)]

    # Cost check (sum across chunks)
    t0 = time.monotonic()
    cost = 0.0
    for ch in chunks:
        cost += client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="ohlcv-1d", symbols=ch,
            stype_in="raw_symbol", start=args.start, end=args.end,
        )
    print(f"[{ticker}] daily cost (ATM band, {len(chunks)} chunks) = ${cost:.4f}  ({time.monotonic()-t0:.1f}s)", flush=True)

    if args.dry_cost:
        print(f"[{ticker} dry-cost] PROJECTED: ${cost:.4f}")
        return

    # Download each chunk, concat
    t1 = time.monotonic()
    parts = []
    for j, ch in enumerate(chunks):
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="ohlcv-1d", symbols=ch,
            stype_in="raw_symbol", start=args.start, end=args.end,
        )
        d = data.to_df()
        if not d.empty:
            parts.append(d)
        print(f"  chunk {j+1}/{len(chunks)}: {len(d):,} rows", flush=True)
    df = pd.concat(parts) if parts else pd.DataFrame()
    print(f"[{ticker}] downloaded {len(df):,} rows ({time.monotonic()-t1:.1f}s)", flush=True)
    out_fp = OUT / f"{ticker.lower()}_cc_daily.parquet"
    df.to_parquet(out_fp)
    print(f"[{ticker} done] cost=${cost:.4f} → {out_fp}")


if __name__ == "__main__":
    main()
