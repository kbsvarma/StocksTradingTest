"""Pull weekly options data from DataBento for multi-ticker IC backtest.

Same shape as pull_avgo.py but parameterized by ticker.

Usage:
  export DATABENTO_API_KEY='db-xxx'
  python pull_multi.py --ticker NVDA --start 2026-02-01 --end 2026-05-22 --dry-cost --sample 8
  python pull_multi.py --ticker NVDA --start 2026-02-01 --end 2026-05-22 --confirm

Output: data/{ticker_lower}/{ticker_lower}_cbbo_YYYYMMDD.parquet
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import databento as db
import pandas as pd
import yfinance as yf


DATASET = "OPRA.PILLAR"
SCHEMA = "cbbo-1m"

# Default OTM range for multi (smaller than AVGO's 8% to control cost)
OTM_PCT_MAX = 0.05

# Per-ticker native strike spacing
TICKER_SPACING = {
    "NVDA":  1.0,
    "META":  5.0,
    "AAPL":  2.5,
    "MSFT":  5.0,
    "GOOGL": 2.5,
    "AMZN":  2.5,
    "TSLA":  5.0,
    "AVGO":  2.5,
    "IWM":   1.0,
    "JNJ":   2.5,
}


def _spot_history(ticker: str, start: str, end: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    print(f"[pull] fetching {ticker} daily history ({start}→{end})...")
    df = yf.Ticker(ticker).history(start=start, end=end)[["Open", "High", "Low", "Close"]]
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df.to_parquet(cache)
    return df


def _build_strikes(spot: float, spacing: float, otm_max: float) -> list[float]:
    lo = spot * (1 - otm_max)
    hi = spot * (1 + otm_max)
    lo_snap = int(lo // spacing) * spacing
    hi_snap = int(hi // spacing + 1) * spacing
    n_steps = int((hi_snap - lo_snap) / spacing) + 1
    return [round(lo_snap + i * spacing, 2) for i in range(n_steps)]


def _format_symbol(root: str, expiry: date, strike: float, right: str) -> str:
    root_pad = root.ljust(6)
    yy = expiry.strftime("%y%m%d")
    strike_thousandths = int(round(strike * 1000))
    return f"{root_pad}{yy}{right}{strike_thousandths:08d}"


def _find_expiries_near(day: date, max_dte: int = 21, dows=(0,2,4)) -> list[date]:
    """Weeklies on the given day-of-week set, up to max_dte days ahead."""
    expiries = []
    for d_ahead in range(0, max_dte + 1):
        target = day + timedelta(days=d_ahead)
        if target.weekday() in dows:
            expiries.append(target)
    return expiries


TICKER_EXPIRY_DOWS = {
    # Default M/W/F
    "AVGO":  (0, 2, 4),
    "NVDA":  (0, 2, 4),
    "META":  (0, 2, 4),
    "AAPL":  (0, 2, 4),
    "MSFT":  (0, 2, 4),
    "GOOGL": (0, 2, 4),
    "AMZN":  (0, 2, 4),
    "TSLA":  (0, 2, 4),
    # Daily M-F
    "IWM":   (0, 1, 2, 3, 4),
    # Friday only
    "JNJ":   (4,),
}


def _pull_one_day(client, ticker, day, spot, strikes, expiries, out_dir, max_day_cost=3.0):
    outfile = out_dir / f"{ticker.lower()}_cbbo_{day.strftime('%Y%m%d')}.parquet"
    if outfile.exists():
        return True, 0.0, "skip-exists"

    symbols = []
    for exp in expiries:
        for k in strikes:
            symbols.append(_format_symbol(ticker, exp, k, "P"))
            symbols.append(_format_symbol(ticker, exp, k, "C"))

    start_iso = day.strftime("%Y-%m-%dT13:30:00")
    end_iso = day.strftime("%Y-%m-%dT20:01:00")

    try:
        cost = client.metadata.get_cost(
            dataset=DATASET, schema=SCHEMA, symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
    except Exception as exc:
        return False, 0.0, f"cost-err: {exc}"

    if cost > max_day_cost:
        return False, cost, f"cost ${cost:.3f} > ${max_day_cost} ceiling"

    try:
        data = client.timeseries.get_range(
            dataset=DATASET, schema=SCHEMA, symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
        df = data.to_df()
        if df.empty:
            return True, cost, "empty"
        df.to_parquet(outfile)
        return True, cost, f"{len(df)}r {len(symbols)}sym"
    except Exception as exc:
        return False, cost, f"pull-err: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--start", default="2026-02-01")
    ap.add_argument("--end", default="2026-05-22")
    ap.add_argument("--otm-max", type=float, default=OTM_PCT_MAX)
    ap.add_argument("--strike-spacing", type=float, default=None,
                    help="override; else use TICKER_SPACING table")
    ap.add_argument("--max-day-cost", type=float, default=3.0)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out-subdir", default=None,
                    help="override output subdir (default: ticker_lower)")
    args = ap.parse_args()

    if not (args.confirm or args.dry_cost):
        print("ERROR: specify --confirm or --dry-cost")
        sys.exit(1)

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: set DATABENTO_API_KEY in environment")
        sys.exit(1)

    ticker = args.ticker.upper()
    spacing = args.strike_spacing if args.strike_spacing else TICKER_SPACING.get(ticker)
    if spacing is None:
        print(f"ERROR: no strike spacing known for {ticker}; pass --strike-spacing")
        sys.exit(1)

    client = db.Historical(key)
    base = Path(__file__).parent
    out_dir = base / "data" / (args.out_subdir if args.out_subdir else ticker.lower())
    out_dir.mkdir(exist_ok=True, parents=True)
    cache = base / f"{ticker.lower()}_spot_cache.parquet"

    spot_df = _spot_history(ticker, args.start, args.end, cache)
    all_days = [pd.Timestamp(d).to_pydatetime().date() for d in spot_df.index]
    all_days = [d for d in all_days if date.fromisoformat(args.start) <= d <= date.fromisoformat(args.end)]
    print(f"[{ticker}] {len(all_days)} trading days in range")

    days = all_days
    if args.sample > 0:
        step = max(1, len(all_days) // args.sample)
        days = all_days[::step][:args.sample]
        print(f"[{ticker}] sampling {len(days)} days for cost estimate")

    print(f"[{ticker}] strike range: ATM ± {args.otm_max*100:.0f}%, spacing ${spacing}")
    print(f"[{ticker}] dry-cost={args.dry_cost} confirm={args.confirm}")

    total_cost = 0.0
    pulled = empty = failed = skipped_ceiling = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        try:
            ts = pd.Timestamp(day)
            spot = float(spot_df.loc[ts, "Open"])
        except (KeyError, Exception) as e:
            failed += 1; continue

        strikes = _build_strikes(spot, spacing, args.otm_max)
        expiries = _find_expiries_near(day, max_dte=21, dows=TICKER_EXPIRY_DOWS.get(ticker, (0,2,4)))

        if args.dry_cost:
            symbols = []
            for exp in expiries:
                for k in strikes:
                    symbols.append(_format_symbol(ticker, exp, k, "P"))
                    symbols.append(_format_symbol(ticker, exp, k, "C"))
            start_iso = day.strftime("%Y-%m-%dT13:30:00")
            end_iso = day.strftime("%Y-%m-%dT20:01:00")
            try:
                cost = client.metadata.get_cost(
                    dataset=DATASET, schema=SCHEMA, symbols=symbols,
                    stype_in="raw_symbol", start=start_iso, end=end_iso,
                )
                total_cost += cost
            except Exception as exc:
                failed += 1; continue
            if (i + 1) % 20 == 0 or i == 0:
                print(f"  [{i+1}/{len(days)}] {day} spot=${spot:.0f} "
                      f"strikes={len(strikes)} exps={len(expiries)} "
                      f"sym={len(symbols)} day_cost=${cost:.3f} running=${total_cost:.2f}", flush=True)
        else:
            ok, cost, msg = _pull_one_day(
                client, ticker, day, spot, strikes, expiries, out_dir, max_day_cost=args.max_day_cost)
            total_cost += cost
            if not ok:
                if "ceiling" in msg: skipped_ceiling += 1
                else: failed += 1
            elif msg == "empty":
                empty += 1
            else:
                pulled += 1
            if (i + 1) % 10 == 0 or "err" in msg or "ceiling" in msg:
                print(f"  [{i+1}/{len(days)}] {day} spot=${spot:.0f}: {msg} running=${total_cost:.3f}", flush=True)

    dt = time.monotonic() - t0
    if args.dry_cost:
        per_day = total_cost / max(1, len(days))
        if args.sample > 0:
            projected = per_day * len(all_days)
            print(f"\n[{ticker} dry-cost] sampled {len(days)} days, mean=${per_day:.4f}/day")
            print(f"[{ticker} dry-cost] PROJECTED TOTAL: ${projected:.2f}")
        else:
            print(f"\n[{ticker} dry-cost] {len(days)} days, total=${total_cost:.2f}")
    else:
        print(f"\n[{ticker} done] {pulled} pulled, {empty} empty, {failed} failed, "
              f"{skipped_ceiling} skipped-ceiling — total=${total_cost:.3f}  ({dt:.0f}s)")


if __name__ == "__main__":
    main()
