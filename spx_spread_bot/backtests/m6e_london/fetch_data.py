"""
fetch_data.py — Download M6E (Micro EUR/USD) 1-min bars from Databento.

Symbol : M6E.v.0  (volume-based continuous front-month roll, unadjusted)
Dataset: GLBX.MDP3 (CME Globex)
Schema : ohlcv-1m
Output : data/m6e_1m.csv  (timestamp, open, high, low, close, volume)

Usage:
    export DATABENTO_API_KEY='your_key'
    python fetch_data.py                      # full history ~7 years
    python fetch_data.py --dry-run            # cost estimate only
    python fetch_data.py --start-date 2022-01-01
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import databento as db
import pandas as pd
import pytz

DATASET       = "GLBX.MDP3"
SCHEMA        = "ohlcv-1m"
SYMBOL        = "M6E.v.0"
STYPE_IN      = "continuous"
TZ_NAME       = "US/Eastern"

# M6E tick details
# Contract : EUR 12,500
# Tick size: 0.000050 USD/EUR  (half-pip)
# Tick value: $0.625/tick
TICK_SIZE     = 0.000050
TICK_VALUE    = 0.625   # USD per tick per contract

UNAVAILABLE_END_RE = re.compile(r"before\s+([0-9T:\-\.]+Z)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Fetch M6E 1-min bars from Databento.")
    parser.add_argument("--start-date", default=None,
                        help="Start date YYYY-MM-DD (default: 7 years ago)")
    parser.add_argument("--end-date", default=None,
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--output-csv",
                        default=str(script_dir / "data" / "m6e_1m.csv"),
                        help="Output CSV path")
    parser.add_argument("--raw-cache-dir",
                        default=str(script_dir / "raw_dbn_cache"),
                        help="Cache dir for raw .dbn.zst files")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if cached CSV exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Estimate cost only, no download")
    return parser.parse_args()


def to_utc_bounds(start_date: date, end_date: date,
                  et_tz: pytz.BaseTzInfo) -> tuple[datetime, datetime]:
    start_et       = et_tz.localize(datetime.combine(start_date, time(0, 0)))
    end_exclusive  = et_tz.localize(datetime.combine(end_date + timedelta(days=1), time(0, 0)))
    return start_et.astimezone(timezone.utc), end_exclusive.astimezone(timezone.utc)


def fetch_with_backoff(client: db.Historical, symbol: str,
                       start_utc: datetime, end_utc: datetime,
                       cache_dir: Path, dry_run: bool) -> pd.DataFrame | None:
    """Fetch symbol with automatic end-date backoff on unavailable-data errors."""
    cache_key = f"{symbol}_{start_utc.date()}_{end_utc.date()}.dbn.zst"
    cache_path = cache_dir / cache_key

    if cache_path.exists() and not dry_run:
        print(f"  Loading from cache: {cache_path.name}")
        store = db.DBNStore.from_file(str(cache_path))
        return store.to_df()

    effective_end = end_utc
    for attempt in range(5):
        try:
            if dry_run:
                cost = client.metadata.get_cost(
                    dataset=DATASET, symbols=[symbol], schema=SCHEMA,
                    stype_in=STYPE_IN,
                    start=start_utc.isoformat(),
                    end=effective_end.isoformat(),
                )
                print(f"  Estimated cost for {symbol}: ${cost:.4f}")
                return None

            print(f"  Fetching {symbol} {start_utc.date()} → {effective_end.date()} ...")
            data = client.timeseries.get_range(
                dataset=DATASET, symbols=[symbol], schema=SCHEMA,
                stype_in=STYPE_IN,
                start=start_utc.isoformat(),
                end=effective_end.isoformat(),
                path=str(cache_path),
            )
            store = db.DBNStore.from_file(str(cache_path))
            return store.to_df()

        except Exception as e:
            msg = str(e)
            m = UNAVAILABLE_END_RE.search(msg)
            if m:
                new_end = datetime.fromisoformat(m.group(1).rstrip("Z")).replace(tzinfo=timezone.utc)
                print(f"  Data unavailable past {new_end.date()}, retrying with that end ...")
                effective_end = new_end
                cache_key = f"{symbol}_{start_utc.date()}_{effective_end.date()}.dbn.zst"
                cache_path  = cache_dir / cache_key
                continue
            raise

    return None


def dbn_df_to_ohlcv(df: pd.DataFrame, et_tz: pytz.BaseTzInfo) -> pd.DataFrame:
    """Normalise a Databento DataFrame to a clean OHLCV frame in ET."""
    if df is None or df.empty:
        return pd.DataFrame()

    # ts_event is nanoseconds UTC in some versions, datetime in others
    if "ts_event" in df.columns:
        ts = pd.to_datetime(df["ts_event"], utc=True, errors="coerce")
    else:
        ts = pd.to_datetime(df.index, utc=True, errors="coerce")

    # Handle both Series and Index
    if hasattr(ts, "dt"):
        ts_et = ts.dt.tz_convert(et_tz)
    else:
        ts_et = ts.tz_convert(et_tz)

    # Detect price scale: Databento uses fixed-point ×1e9 for futures
    # EUR/USD should be ~0.9–1.5; if raw values are >100 they need scaling
    scale = 1e9 if df["close"].iloc[0] > 100 else 1.0

    vol_col = "size" if "size" in df.columns else "volume" if "volume" in df.columns else None

    out = pd.DataFrame({
        "open":   df["open"].values  / scale,
        "high":   df["high"].values  / scale,
        "low":    df["low"].values   / scale,
        "close":  df["close"].values / scale,
        "volume": df[vol_col].values if vol_col else 0,
    }, index=ts_et)

    out.index.name = "timestamp"
    return out.sort_index()


def main() -> None:
    if not os.getenv("DATABENTO_API_KEY"):
        raise SystemExit(
            "DATABENTO_API_KEY not set.\n"
            "Run: export DATABENTO_API_KEY='db-xxxx'\n"
            "Then retry."
        )

    args   = parse_args()
    et_tz  = pytz.timezone(TZ_NAME)
    today  = date.today()

    start_date = date.fromisoformat(args.start_date) if args.start_date \
                 else today.replace(year=today.year - 7)
    end_date   = date.fromisoformat(args.end_date) if args.end_date else today

    output_csv = Path(args.output_csv)
    cache_dir  = Path(args.raw_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if output_csv.exists() and not args.force and not args.dry_run:
        print(f"Output already exists: {output_csv}")
        print("Use --force to re-fetch.")
        return

    print(f"M6E 1-min data fetch")
    print(f"  Symbol : {SYMBOL}")
    print(f"  Range  : {start_date} → {end_date}")
    print(f"  Output : {output_csv}")
    print()

    start_utc, end_utc = to_utc_bounds(start_date, end_date, et_tz)
    client = db.Historical()

    raw_df = fetch_with_backoff(client, SYMBOL, start_utc, end_utc,
                                cache_dir, args.dry_run)
    if args.dry_run or raw_df is None:
        return

    print(f"\n  Raw rows fetched: {len(raw_df):,}")

    ohlcv = dbn_df_to_ohlcv(raw_df, et_tz)

    if ohlcv.empty:
        raise SystemExit("No data after normalisation — check symbol or date range.")

    # Sanity check: EUR/USD should be 0.80–2.00
    bad = (ohlcv["close"] < 0.80) | (ohlcv["close"] > 2.00)
    if bad.sum() > 0:
        print(f"  Dropping {bad.sum()} bars outside 0.80–2.00 (bad ticks)")
        ohlcv = ohlcv[~bad]

    # Save 1-min directly (no resampling)
    ohlcv = ohlcv.reset_index()
    ohlcv["timestamp"] = ohlcv["timestamp"].map(lambda ts: ts.isoformat())
    ohlcv.to_csv(output_csv, index=False, float_format="%.6f")

    print(f"\nSaved {len(ohlcv):,} rows → {output_csv}")

    # Quick coverage stats
    df_check = pd.read_csv(output_csv, index_col=0)
    df_check.index = pd.to_datetime(df_check.index, utc=True).tz_convert(et_tz)
    trading_days = len(set(df_check.index.date))
    london_close = df_check.between_time("11:55", "12:05")
    print(f"EUR/USD range : {ohlcv['close'].min():.5f} – {ohlcv['close'].max():.5f}")
    print(f"Trading days  : {trading_days}")
    print(f"12pm ET bars  : {len(london_close):,} ({len(london_close)/trading_days:.1f}/day avg)")
    print(f"\nNext step: python backtest.py --data data/m6e_1m.csv")


if __name__ == "__main__":
    main()
