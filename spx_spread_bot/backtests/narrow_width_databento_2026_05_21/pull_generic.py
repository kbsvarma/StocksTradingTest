"""Generic SPXW-style cbbo-1m puller for any options root.

Usage:
    python -m spx_spread_bot.backtests.narrow_width_databento_2026_05_21.pull_generic \\
        --root SPY --put-bands far_otm --start 2020-01-01 --end 2026-05-21 --confirm

    python -m spx_spread_bot.backtests.narrow_width_databento_2026_05_21.pull_generic \\
        --root NDXP --put-bands bps --start 2020-01-01 --end 2026-05-21 --confirm

put-bands:
    bps      → 0% to 1.5% OTM at native strike spacing × widths 1-5 (for BPS backtest)
    far_otm  → 1.5% to 6% OTM at native strike spacing (for tail-hedge analysis)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import databento as db
import pandas as pd
import yfinance as yf

ROOT_CONFIG = {
    # root: (yf_proxy, strike_spacing, scale_to_spx, output_subdir)
    "SPY":  ("SPY",   1.0,  10.0,   "spy_cbbo_1m"),       # SPY = SPX/10
    "NDXP": ("^NDX",  25.0, 1.0,    "ndxp_cbbo_1m"),     # NDXP is the 0DTE NDX root
    "NDX":  ("^NDX",  25.0, 1.0,    "ndx_cbbo_1m"),      # monthly NDX
    "XSP":  ("^GSPC", 1.0,  10.0,   "xsp_cbbo_1m"),      # mini SPX (10× SPX/10 reverse)
}

PUT_BANDS = {
    "bps":     (0.0, 1.5),    # entry strikes for BPS strategy
    "far_otm": (1.5, 6.0),    # far-OTM for tail hedging
}


def _spot_history(yf_symbol: str, start: str, end: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    print(f"[pull] fetching {yf_symbol} daily history ({start}→{end})...")
    df = yf.Ticker(yf_symbol).history(start=start, end=end)[["Open", "High", "Low", "Close"]]
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df.to_parquet(cache)
    return df


def _build_strikes(spot: float, scale: float, spacing: float,
                   band_min_pct: float, band_max_pct: float) -> list[float]:
    """Generate strike list for given OTM band, snapped to native spacing."""
    lo_pct = band_max_pct / 100  # furthest OTM → lowest strike
    hi_pct = band_min_pct / 100
    lo = spot * (1 - lo_pct)
    hi = spot * (1 - hi_pct)
    # Snap to spacing
    lo_snap = int(lo // spacing) * spacing
    hi_snap = int(hi // spacing + 1) * spacing
    n_steps = int((hi_snap - lo_snap) / spacing) + 1
    return [lo_snap + i * spacing for i in range(n_steps)]


def _format_symbol(root: str, expiry: datetime, strike: float) -> str:
    """OPRA OCC: ROOT(padded to 6) + EXP(YYMMDD) + P + STRIKE(5d $ . 3d cents)."""
    root_pad = root.ljust(6)
    yy = expiry.strftime("%y%m%d")
    # OPRA strike format: total 8 digits = 5 dollars + 3 cents
    strike_thousandths = int(round(strike * 1000))
    return f"{root_pad}{yy}P{strike_thousandths:08d}"


def _pull_one_day(client, day, spot, root, scale, spacing, band, out_dir):
    outfile = out_dir / f"{root.lower()}_cbbo_{day.strftime('%Y%m%d')}.parquet"
    if outfile.exists():
        return True, 0.0, "skip-exists"

    strikes = _build_strikes(spot, scale, spacing, *PUT_BANDS[band])
    symbols = [_format_symbol(root, day, k) for k in strikes]
    start_iso = day.strftime("%Y-%m-%dT13:30:00")
    end_iso = day.strftime("%Y-%m-%dT20:01:00")

    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
    except Exception as exc:
        return False, 0.0, f"cost: {exc}"
    if cost > 1.0:
        return False, cost, f"cost ${cost:.3f} > $1.00 ceiling"

    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
        df = data.to_df()
        if df.empty:
            return True, cost, "empty"
        df.to_parquet(outfile)
        return True, cost, f"{len(df)}r"
    except Exception as exc:
        return False, cost, f"pull: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, choices=list(ROOT_CONFIG))
    ap.add_argument("--put-bands", required=True, choices=list(PUT_BANDS))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--dry-cost", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: set DATABENTO_API_KEY"); sys.exit(1)
    client = db.Historical(key)

    yf_sym, spacing, scale, subdir = ROOT_CONFIG[args.root]
    base = Path(__file__).parent
    out_dir = base / subdir; out_dir.mkdir(exist_ok=True)
    cache = base / f"{args.root.lower()}_spot_cache.parquet"

    spot_df = _spot_history(yf_sym, args.start, args.end, cache)
    days = [pd.Timestamp(d) for d in spot_df.index
            if pd.Timestamp(args.start) <= pd.Timestamp(d) <= pd.Timestamp(args.end)]

    print(f"[pull] root={args.root} band={args.put_bands} days={len(days)}")
    if not (args.confirm or args.dry_cost):
        print("Specify --confirm or --dry-cost"); sys.exit(1)

    total_cost = 0.0; pulled = 0; empty = 0; failed = 0
    t0 = time.monotonic()
    for i, day in enumerate(days):
        try:
            spot = float(spot_df.loc[day, "Open"])
        except Exception:
            failed += 1; continue
        if args.dry_cost:
            strikes = _build_strikes(spot, scale, spacing, *PUT_BANDS[args.put_bands])
            symbols = [_format_symbol(args.root, day, k) for k in strikes]
            try:
                cost = client.metadata.get_cost(
                    dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                    stype_in="raw_symbol",
                    start=day.strftime("%Y-%m-%dT13:30:00"),
                    end=day.strftime("%Y-%m-%dT20:01:00"),
                )
                total_cost += cost
            except Exception:
                pass
            if i % 100 == 0:
                print(f"  [{i+1}/{len(days)}] running cost ${total_cost:.2f}")
            continue
        ok, cost, msg = _pull_one_day(client, day, spot, args.root, scale, spacing,
                                       args.put_bands, out_dir)
        total_cost += cost
        if not ok: failed += 1
        elif msg == "empty": empty += 1
        else: pulled += 1
        if (i+1) % 50 == 0 or msg.startswith("cost") or msg.startswith("pull"):
            print(f"  [{i+1}/{len(days)}] {day.date()} spot={spot:.1f}: {msg} "
                  f"running=${total_cost:.3f}")
    dt = time.monotonic() - t0
    print(f"\n[done] {args.root}/{args.put_bands}: {pulled} pulled, {empty} empty, "
          f"{failed} failed in {dt:.0f}s  total=${total_cost:.3f}")


if __name__ == "__main__":
    main()
