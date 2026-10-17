"""Pull AVGO weekly options data from DataBento for IC backtest.

Strategy:
- AVGO had 3 expiries/week in 2025 (Mon/Wed/Fri)
- IC needs BOTH puts AND calls at strikes ATM ± 8%
- We pull cbbo-1m (1-minute consolidated best bid/offer) for each strike, each day
- This supports IC backtests at multiple OTM levels (1%, 2%, 3%, 5%) and wing widths (5, 10, 15, 25)

Usage:
  export DATABENTO_API_KEY='db-xxx'
  python pull_avgo.py --dry-cost                  # estimate cost first
  python pull_avgo.py --confirm                   # pull data
  python pull_avgo.py --confirm --start 2025-06-01 --end 2025-06-30  # partial pull

Cost ceiling: $2.00 per day to prevent runaway. Override with --max-day-cost.
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


AVGO_STRIKE_SPACING = 2.5  # AVGO strikes typically every $2.50 or $5 around ATM
DATASET = "OPRA.PILLAR"
SCHEMA = "cbbo-1m"

# How far OTM to pull on each side (8% gives room for 5% OTM short + 25pt wing)
OTM_PCT_MAX = 0.08


def _spot_history(start: str, end: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    print(f"[pull] fetching AVGO daily history ({start}→{end})...")
    df = yf.Ticker("AVGO").history(start=start, end=end)[["Open", "High", "Low", "Close"]]
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df.to_parquet(cache)
    return df


def _build_strikes(spot: float, spacing: float, otm_max: float) -> list[float]:
    """Generate strikes from -otm_max to +otm_max around spot, snapped to spacing."""
    lo = spot * (1 - otm_max)
    hi = spot * (1 + otm_max)
    lo_snap = int(lo // spacing) * spacing
    hi_snap = int(hi // spacing + 1) * spacing
    n_steps = int((hi_snap - lo_snap) / spacing) + 1
    return [round(lo_snap + i * spacing, 2) for i in range(n_steps)]


def _format_symbol(root: str, expiry: date, strike: float, right: str) -> str:
    """OPRA OCC format: ROOT(6) + YYMMDD + P/C + STRIKE(8 = 5d.3d)."""
    root_pad = root.ljust(6)
    yy = expiry.strftime("%y%m%d")
    strike_thousandths = int(round(strike * 1000))
    return f"{root_pad}{yy}{right}{strike_thousandths:08d}"


def _find_expiries_near(day: date, max_dte: int = 21) -> list[date]:
    """Generate likely AVGO expiries up to max_dte days from `day`.

    AVGO has weekly options on Mon, Wed, Fri.
    """
    expiries = []
    for d_ahead in range(0, max_dte + 1):
        target = day + timedelta(days=d_ahead)
        # AVGO weeklies: Mon=0, Wed=2, Fri=4
        if target.weekday() in (0, 2, 4):
            expiries.append(target)
    return expiries


def _pull_one_day(client, day, spot, strikes, expiries, out_dir, max_day_cost=2.0):
    outfile = out_dir / f"avgo_cbbo_{day.strftime('%Y%m%d')}.parquet"
    if outfile.exists():
        return True, 0.0, "skip-exists"

    # Build symbol list: puts + calls × strikes × expiries
    symbols = []
    for exp in expiries:
        for k in strikes:
            symbols.append(_format_symbol("AVGO", exp, k, "P"))
            symbols.append(_format_symbol("AVGO", exp, k, "C"))

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
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--otm-max", type=float, default=OTM_PCT_MAX)
    ap.add_argument("--strike-spacing", type=float, default=AVGO_STRIKE_SPACING)
    ap.add_argument("--max-day-cost", type=float, default=2.0)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--dry-cost", action="store_true")
    ap.add_argument("--sample", type=int, default=0,
                    help="N: sample only N days evenly across the range for cost estimate")
    args = ap.parse_args()

    if not (args.confirm or args.dry_cost):
        print("ERROR: specify --confirm or --dry-cost")
        sys.exit(1)

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: set DATABENTO_API_KEY in environment")
        sys.exit(1)

    client = db.Historical(key)
    base = Path(__file__).parent
    out_dir = base / "data"
    out_dir.mkdir(exist_ok=True)
    cache = base / "avgo_spot_cache.parquet"

    spot_df = _spot_history(args.start, args.end, cache)
    all_days = [pd.Timestamp(d).to_pydatetime().date() for d in spot_df.index]
    all_days = [d for d in all_days if date.fromisoformat(args.start) <= d <= date.fromisoformat(args.end)]
    print(f"[pull] {len(all_days)} trading days in range")

    # Sample for dry-cost if requested
    days = all_days
    if args.sample > 0:
        step = max(1, len(all_days) // args.sample)
        days = all_days[::step][:args.sample]
        print(f"[pull] sampling {len(days)} days for cost estimate")

    print(f"[pull] strike range: ATM ± {args.otm_max*100:.0f}%, spacing ${args.strike_spacing}")
    print(f"[pull] dry-cost={args.dry_cost} confirm={args.confirm}")

    total_cost = 0.0
    pulled = 0
    empty = 0
    failed = 0
    skipped_ceiling = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        try:
            ts = pd.Timestamp(day)
            spot = float(spot_df.loc[ts, "Open"])
        except (KeyError, Exception) as e:
            print(f"[{day}] no spot: {e}")
            failed += 1
            continue

        strikes = _build_strikes(spot, args.strike_spacing, args.otm_max)
        expiries = _find_expiries_near(day, max_dte=21)

        if args.dry_cost:
            # Just estimate cost, don't pull
            symbols = []
            for exp in expiries:
                for k in strikes:
                    symbols.append(_format_symbol("AVGO", exp, k, "P"))
                    symbols.append(_format_symbol("AVGO", exp, k, "C"))
            start_iso = day.strftime("%Y-%m-%dT13:30:00")
            end_iso = day.strftime("%Y-%m-%dT20:01:00")
            try:
                cost = client.metadata.get_cost(
                    dataset=DATASET, schema=SCHEMA, symbols=symbols,
                    stype_in="raw_symbol", start=start_iso, end=end_iso,
                )
                total_cost += cost
            except Exception as exc:
                print(f"[{day}] cost-err: {exc}")
                failed += 1
                continue
            if (i + 1) % 20 == 0 or i == 0:
                print(f"  [{i+1}/{len(days)}] {day} spot=${spot:.0f} "
                      f"strikes={len(strikes)} exps={len(expiries)} "
                      f"symbols={len(symbols)} day_cost=${cost:.3f} running=${total_cost:.2f}")
        else:
            ok, cost, msg = _pull_one_day(
                client, day, spot, strikes, expiries, out_dir, max_day_cost=args.max_day_cost)
            total_cost += cost
            if not ok:
                if "ceiling" in msg:
                    skipped_ceiling += 1
                else:
                    failed += 1
            elif msg == "empty":
                empty += 1
            else:
                pulled += 1
            if (i + 1) % 20 == 0 or "err" in msg or "ceiling" in msg:
                print(f"  [{i+1}/{len(days)}] {day} spot=${spot:.0f}: {msg} "
                      f"running=${total_cost:.3f}")

    dt = time.monotonic() - t0
    if args.dry_cost:
        per_day = total_cost / max(1, len(days))
        if args.sample > 0:
            projected = per_day * len(all_days)
            print(f"\n[dry-cost] sampled {len(days)} days, mean cost/day=${per_day:.4f}")
            print(f"[dry-cost] PROJECTED FULL-YEAR TOTAL: ${projected:.2f}")
        else:
            print(f"\n[dry-cost] {len(days)} days, total estimated cost=${total_cost:.2f}")
        print(f"  ({dt:.0f}s elapsed)")
    else:
        print(f"\n[done] {pulled} pulled, {empty} empty, {failed} failed, "
              f"{skipped_ceiling} skipped-ceiling — total=${total_cost:.3f}  ({dt:.0f}s)")


if __name__ == "__main__":
    main()
