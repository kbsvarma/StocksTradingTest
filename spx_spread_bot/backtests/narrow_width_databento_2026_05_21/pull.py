"""Pull SPXW 0DTE put cbbo-1m data for narrow-width backtest grid.

Strategy: pull ONLY the dated symbols that exist on each trading day, in the
strike band that could possibly be used (OTM 0%-1.5% with widths 1-5pt).
Per-day cost is tiny because expired-symbol queries return ~0 bytes.

Run as:
    python -m spx_spread_bot.backtests.narrow_width_databento_2026_05_21.pull --pilot
    python -m spx_spread_bot.backtests.narrow_width_databento_2026_05_21.pull --confirm-full
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

OUT_DIR = Path(__file__).parent / "data_cbbo_1m"
SPX_SPOT_CACHE = Path(__file__).parent / "spx_intraday_cache.parquet"


def _spx_open_intraday() -> pd.DataFrame:
    """Get SPX 1-min intraday OHLC from yfinance for full backtest window.

    Cached because yfinance limits intraday history to 60 days per call;
    we'd need many calls to span 12 years. Fall back to daily close + ATR-based
    10:30 spot estimation if intraday unavailable.
    """
    if SPX_SPOT_CACHE.exists():
        return pd.read_parquet(SPX_SPOT_CACHE)

    # yfinance daily SPX close — we'll use the OPEN as our 10:30 spot proxy.
    # For an exact 10:30 spot we'd want intraday, but yfinance 1m only goes
    # 60 days back. Close enough for strike selection.
    print("[pull] fetching SPX daily history (yfinance)...")
    spx = yf.Ticker("^GSPC").history(start="2013-04-01", end="2026-05-22")
    spx = spx[["Open", "High", "Low", "Close"]].copy()
    spx.index = spx.index.tz_localize(None) if spx.index.tz else spx.index
    spx.to_parquet(SPX_SPOT_CACHE)
    print(f"[pull] cached {len(spx)} days to {SPX_SPOT_CACHE}")
    return spx


def _build_strike_list(spx_at_1030: float, width_max: int = 50,
                       otm_min_pct: float = 0.0, otm_max_pct: float = 3.0) -> list[float]:
    """Strike grid covering OTM 0%-3% × up to 50pt-wide BPS at 1pt granularity.

    Long leg of a 50pt spread at 1% OTM = strike ~2.0-2.5% below spot, so band
    extends to 3% to cover the long leg of even our widest OTM × widest spread test.
    """
    lo = spx_at_1030 * (1 - otm_max_pct / 100) - width_max - 10  # extra buffer
    hi = spx_at_1030 * (1 - otm_min_pct / 100) + 10
    # SPXW strikes are at every integer for 0DTE; use 1pt granularity
    return list(range(int(lo), int(hi) + 1))


def _format_spxw_symbol(expiry: datetime, strike: float) -> str:
    """Webull-like raw symbol: 'SPXW  YYMMDDP0SSSSS000'

    Format per OPRA OCC: ROOT(6) + EXP(YYMMDD) + C/P + STRIKE(5d $).(3d cents)
    """
    yy = expiry.strftime("%y%m%d")
    strike_int = int(round(strike))
    return f"SPXW  {yy}P{strike_int:05d}000"


def _trading_days(start: datetime, end: datetime, spx_df: pd.DataFrame) -> list[datetime]:
    """All trading days in window — use SPX history as the calendar."""
    idx = spx_df.index
    mask = (idx >= start) & (idx <= end)
    return list(idx[mask].to_pydatetime())


def _pull_one_day(client: db.Historical, day: datetime, spx_open: float,
                  out_dir: Path) -> tuple[bool, float, str]:
    """Pull cbbo-1m for one day's SPXW 0DTE put strikes. Return (ok, cost, msg)."""
    outfile = out_dir / f"spxw_cbbo_{day.strftime('%Y%m%d')}.parquet"
    if outfile.exists():
        return True, 0.0, f"already exists ({outfile.stat().st_size} bytes)"

    strikes = _build_strike_list(spx_open, width_max=5, otm_min_pct=0.0, otm_max_pct=1.5)
    symbols = [_format_spxw_symbol(day, k) for k in strikes]
    start_iso = day.strftime("%Y-%m-%dT13:30:00")
    end_iso = day.strftime("%Y-%m-%dT20:01:00")  # +1min to catch the close bar

    # Cost check (cheap, ~ms)
    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
            mode="historical-streaming",
        )
    except Exception as exc:
        return False, 0.0, f"cost query failed: {exc}"

    if cost > 0.50:
        return False, cost, f"single-day cost ${cost:.4f} > $0.50 sanity ceiling"

    # Actual pull (historical-streaming — pay-as-you-go, no batch wait)
    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
        df = data.to_df()
        if df.empty:
            return True, cost, f"empty (no quotes — likely no 0DTE expiry that day)"
        df.to_parquet(outfile)
        return True, cost, f"saved {len(df)} rows → {outfile.name}"
    except Exception as exc:
        return False, cost, f"pull failed: {exc}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="Run on 1 month only (June 2024)")
    ap.add_argument("--confirm-full", action="store_true", help="Run full 2013-04 → today")
    ap.add_argument("--dry-cost", action="store_true", help="Sum cost estimate without pulling")
    args = ap.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: set DATABENTO_API_KEY env var")
        sys.exit(1)
    client = db.Historical(key)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    spx_df = _spx_open_intraday()

    if args.pilot:
        days = _trading_days(datetime(2024, 6, 1), datetime(2024, 6, 30), spx_df)
    elif args.confirm_full or args.dry_cost:
        # 2020-01-01 → 2026-05-21: 6.5 years covering the modern 0DTE-every-weekday era.
        # Pre-2020 SPXW 0DTE was less consistent; this window matches recent strategy
        # studies and gives ~1638 trading days.
        days = _trading_days(datetime(2020, 1, 1), datetime(2026, 5, 21), spx_df)
    else:
        print("Specify --pilot, --confirm-full, or --dry-cost")
        sys.exit(1)

    print(f"[pull] target: {len(days)} trading days  ({days[0].date()} → {days[-1].date()})")
    total_cost = 0.0
    pulled = 0
    skipped_empty = 0
    failed = 0
    t0 = time.monotonic()

    for i, day in enumerate(days):
        try:
            spx_open = float(spx_df.loc[day, "Open"])
        except Exception:
            print(f"  [{i+1}/{len(days)}] {day.date()}: no SPX spot — skip")
            failed += 1
            continue

        if args.dry_cost:
            strikes = _build_strike_list(spx_open)
            symbols = [_format_spxw_symbol(day, k) for k in strikes]
            try:
                cost = client.metadata.get_cost(
                    dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
                    stype_in="raw_symbol",
                    start=day.strftime("%Y-%m-%dT13:30:00"),
                    end=day.strftime("%Y-%m-%dT20:01:00"),
                    mode="historical-streaming",
                )
                total_cost += cost
                if i % 50 == 0:
                    print(f"  [{i+1}/{len(days)}] {day.date()} spx_open={spx_open:.1f} "
                          f"cost=${cost:.5f}  running=${total_cost:.2f}")
            except Exception as exc:
                print(f"  [{i+1}/{len(days)}] cost query failed: {exc}")
            continue

        ok, cost, msg = _pull_one_day(client, day, spx_open, OUT_DIR)
        total_cost += cost
        if not ok:
            failed += 1
            print(f"  [{i+1}/{len(days)}] {day.date()}: FAIL — {msg}")
        elif "empty" in msg:
            skipped_empty += 1
            if i % 20 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()}: {msg}")
        else:
            pulled += 1
            if i % 20 == 0:
                print(f"  [{i+1}/{len(days)}] {day.date()} spx_open={spx_open:.1f}: "
                      f"{msg}  cost=${cost:.5f}  running=${total_cost:.2f}")

    dt = time.monotonic() - t0
    mode = "DRY-COST" if args.dry_cost else "PULL"
    print(f"\n[{mode}] done in {dt:.0f}s  pulled={pulled} empty={skipped_empty} "
          f"failed={failed} total_cost=${total_cost:.4f}")


if __name__ == "__main__":
    main()
