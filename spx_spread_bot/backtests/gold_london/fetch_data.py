from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

import databento as db
import pandas as pd
import pytz


DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
PRIMARY_SYMBOL = "MGC.v.0"
FALLBACK_SYMBOL = "GC.v.0"
STYPE_IN = "continuous"
TZ_NAME = "US/Eastern"
UNAVAILABLE_END_RE = re.compile(r"before\s+([0-9T:\-\.]+Z)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Fetch and cache 10y MGC/GC 5m bars from Databento.")
    parser.add_argument("--start-date", default=None, help="Start date (YYYY-MM-DD). Default: 10 years ago from today.")
    parser.add_argument("--end-date", default=None, help="End date (YYYY-MM-DD). Default: today.")
    parser.add_argument(
        "--output-csv",
        default=str(script_dir / "data" / "mgc_5min.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--raw-cache-dir",
        default=str(script_dir / "raw_dbn_cache"),
        help="Directory for raw Databento DBN cache files.",
    )
    parser.add_argument("--force", action="store_true", help="Refetch even if cached CSV appears complete.")
    parser.add_argument("--dry-run", action="store_true", help="Estimate Databento cost only; do not download data.")
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def ten_years_ago(d: date) -> date:
    try:
        return d.replace(year=d.year - 10)
    except ValueError:
        # Handle Feb 29 -> Feb 28 on non-leap target year.
        return d.replace(year=d.year - 10, month=2, day=28)


def to_utc_bounds(start_date: date, end_date: date, et_tz: pytz.BaseTzInfo) -> tuple[datetime, datetime]:
    start_et = et_tz.localize(datetime.combine(start_date, time(0, 0)))
    end_exclusive_et = et_tz.localize(datetime.combine(end_date + timedelta(days=1), time(0, 0)))
    return start_et.astimezone(timezone.utc), end_exclusive_et.astimezone(timezone.utc)


def cache_covers_range(csv_path: Path, start_date: date, end_date: date) -> bool:
    if not csv_path.exists():
        return False
    try:
        cached = pd.read_csv(csv_path, usecols=["timestamp"])
    except Exception:
        return False
    if cached.empty or "timestamp" not in cached.columns:
        return False

    ts = pd.to_datetime(cached["timestamp"], utc=True, errors="coerce").dropna()
    if ts.empty:
        return False

    first_day = ts.min().date()
    last_day = ts.max().date()
    return first_day <= start_date and last_day >= end_date


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def cache_path_for_query(cache_dir: Optional[Path], query: dict) -> Optional[Path]:
    if cache_dir is None:
        return None
    payload = {
        "dataset": query.get("dataset"),
        "schema": query.get("schema"),
        "stype_in": query.get("stype_in"),
        "start": _json_safe(query.get("start")),
        "end": _json_safe(query.get("end")),
        "symbols": _json_safe(query.get("symbols")),
    }
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload_str.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    dataset = str(query.get("dataset", "dataset")).replace(".", "_")
    schema = str(query.get("schema", "schema")).replace("-", "_")
    return cache_dir / f"{dataset}__{schema}__{digest}.dbn.zst"


def backoff_end_from_error(exc: Exception, current_end: datetime) -> Optional[datetime]:
    msg = str(exc)
    msg_lower = msg.lower()

    if "data_end_after_available_end" in msg_lower:
        return current_end - timedelta(minutes=30)

    if "dataset_unavailable_range" in msg_lower:
        m = UNAVAILABLE_END_RE.search(msg)
        if m:
            parsed = pd.to_datetime(m.group(1), utc=True, errors="coerce")
            if pd.notna(parsed):
                parsed_dt = parsed.to_pydatetime()
                if parsed_dt >= current_end:
                    return current_end - timedelta(hours=1)
                return parsed_dt - timedelta(seconds=1)
        return current_end - timedelta(days=1)

    return None


def db_get_range_df(
    client: db.Historical,
    *,
    dataset: str,
    symbols: list[str],
    schema: str,
    stype_in: str,
    start: datetime,
    end: datetime,
    raw_cache_dir: Optional[Path],
) -> pd.DataFrame:
    end_candidate = end
    for _ in range(6):
        query = {
            "dataset": dataset,
            "symbols": symbols,
            "schema": schema,
            "start": start,
            "end": end_candidate,
            "stype_in": stype_in,
        }
        cache_path = cache_path_for_query(raw_cache_dir, query)

        if cache_path and cache_path.exists():
            try:
                print(f"Using raw DBN cache: {cache_path}")
                return db.DBNStore.from_file(cache_path).to_df()
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: failed to read raw DBN cache {cache_path.name}: {exc}; re-downloading.")
                cache_path.unlink(missing_ok=True)

        query_kwargs = dict(query)
        if cache_path:
            query_kwargs["path"] = str(cache_path)

        try:
            data = client.timeseries.get_range(**query_kwargs)
            out = data.to_df()

            if cache_path and not cache_path.with_suffix(cache_path.suffix + ".meta.json").exists():
                meta_path = cache_path.with_suffix(cache_path.suffix + ".meta.json")
                meta = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "query": _json_safe(query)}
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return out
        except Exception as exc:  # noqa: BLE001
            next_end = backoff_end_from_error(exc, end_candidate)
            if next_end is not None:
                end_candidate = next_end
                if end_candidate <= start:
                    print("Warning: unable to back off end time far enough for Databento availability window.")
                    raise
                print(
                    "Databento end-time constraint detected; retrying get_range with earlier end: "
                    f"{end_candidate.isoformat()}"
                )
                continue
            raise

    raise RuntimeError("Unable to fetch Databento range after multiple end-time backoff retries.")


def normalize_ohlcv_1m(df: pd.DataFrame, et_tz: pytz.BaseTzInfo) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    if "ts_event" in df.columns:
        ts_utc = pd.to_datetime(df["ts_event"], utc=True, errors="coerce")
    elif isinstance(df.index, pd.MultiIndex):
        level_name = "ts_event" if "ts_event" in df.index.names else df.index.names[-1]
        ts_utc = pd.to_datetime(df.index.get_level_values(level_name), utc=True, errors="coerce")
    else:
        ts_utc = pd.to_datetime(df.index, utc=True, errors="coerce")

    frame = df.copy()
    frame["_ts_utc"] = ts_utc
    frame = frame.dropna(subset=["_ts_utc"]).set_index("_ts_utc").sort_index()
    frame.index = frame.index.tz_convert(et_tz)

    column_sets = [
        {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"},
        {
            "open": "open_px_00",
            "high": "high_px_00",
            "low": "low_px_00",
            "close": "close_px_00",
            "volume": "volume",
        },
    ]
    selected_map = None
    for candidate in column_sets:
        if all(col in frame.columns for col in candidate.values()):
            selected_map = candidate
            break
    if selected_map is None:
        raise ValueError(f"Could not find OHLCV columns in Databento frame: {list(frame.columns)}")

    out = frame[list(selected_map.values())].rename(columns={v: k for k, v in selected_map.items()})
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[~out.index.duplicated(keep="last")]
    return out


def resample_5m(one_min: pd.DataFrame) -> pd.DataFrame:
    if one_min.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    bars = one_min.resample("5min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    bars["volume"] = bars["volume"].fillna(0).round(0).astype("int64")
    bars[["open", "high", "low", "close"]] = bars[["open", "high", "low", "close"]].round(2)
    bars.index.name = "timestamp"

    out = bars.reset_index()
    out["timestamp"] = out["timestamp"].map(lambda ts: ts.isoformat())
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


def estimate_cost(
    client: db.Historical,
    *,
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
) -> None:
    end_candidate = end_utc
    for _ in range(6):
        try:
            cost = client.metadata.get_cost(
                dataset=DATASET,
                symbols=[symbol],
                schema=SCHEMA,
                start=start_utc,
                end=end_candidate,
                stype_in=STYPE_IN,
            )
            print(f"Estimated Databento cost for {symbol}: ${float(cost):.4f}")
            return
        except Exception as exc:  # noqa: BLE001
            next_end = backoff_end_from_error(exc, end_candidate)
            if next_end is not None:
                end_candidate = next_end
                if end_candidate <= start_utc:
                    print(f"Warning: unable to estimate cost for {symbol} due to end-time availability mismatch.")
                    raise
                print(
                    "Databento end-time constraint detected; retrying get_cost with earlier end: "
                    f"{end_candidate.isoformat()}"
                )
                continue
            raise

    raise RuntimeError(f"Unable to estimate Databento cost for {symbol} after backoff retries.")


def main() -> int:
    args = parse_args()

    if not os.getenv("DATABENTO_API_KEY"):
        raise SystemExit(
            "DATABENTO_API_KEY is not set. Export it first, e.g.:\n"
            "export DATABENTO_API_KEY='your_key_here'"
        )

    et_tz = pytz.timezone(TZ_NAME)
    today_et = datetime.now(et_tz).date()
    start_date = parse_date(args.start_date) if args.start_date else ten_years_ago(today_et)
    end_date = parse_date(args.end_date) if args.end_date else today_et
    if end_date < start_date:
        raise SystemExit("--end-date must be >= --start-date")

    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    raw_cache_dir = Path(args.raw_cache_dir) if args.raw_cache_dir else None
    if raw_cache_dir:
        raw_cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"Requested range (ET): {start_date.isoformat()} -> {end_date.isoformat()}")
    print("Note: continuous contracts handle futures roll automatically.")

    if not args.force and cache_covers_range(output_csv, start_date, end_date):
        print("Using cached data")
        print(f"File: {output_csv.resolve()}")
        return 0

    start_utc, end_utc = to_utc_bounds(start_date, end_date, et_tz)
    now_utc = datetime.now(timezone.utc)
    if end_utc > now_utc:
        print(
            "Requested end is after current UTC time; "
            f"capping end from {end_utc.isoformat()} to {now_utc.isoformat()}."
        )
        end_utc = now_utc

    client = db.Historical()
    if args.dry_run:
        print("Dry run: estimating download cost only.")
        estimate_cost(client, symbol=PRIMARY_SYMBOL, start_utc=start_utc, end_utc=end_utc)
        estimate_cost(client, symbol=FALLBACK_SYMBOL, start_utc=start_utc, end_utc=end_utc)
        print("Note: GC.c.0 cost is only relevant if fallback is needed for pre-MGC history.")
        return 0

    print(f"Fetching 1m bars for {PRIMARY_SYMBOL}...")
    mgc_raw = db_get_range_df(
        client,
        dataset=DATASET,
        symbols=[PRIMARY_SYMBOL],
        schema=SCHEMA,
        stype_in=STYPE_IN,
        start=start_utc,
        end=end_utc,
        raw_cache_dir=raw_cache_dir,
    )
    print(f"Row count after {PRIMARY_SYMBOL} fetch: {len(mgc_raw)}")

    mgc_1m = normalize_ohlcv_1m(mgc_raw, et_tz)
    combined_1m = mgc_1m

    if mgc_1m.empty:
        print(f"Warning: no {PRIMARY_SYMBOL} data found in requested range; falling back to {FALLBACK_SYMBOL}.")
        gc_raw = db_get_range_df(
            client,
            dataset=DATASET,
            symbols=[FALLBACK_SYMBOL],
            schema=SCHEMA,
            stype_in=STYPE_IN,
            start=start_utc,
            end=end_utc,
            raw_cache_dir=raw_cache_dir,
        )
        print(f"Row count after {FALLBACK_SYMBOL} fetch: {len(gc_raw)}")
        gc_1m = normalize_ohlcv_1m(gc_raw, et_tz)
        if gc_1m.empty:
            print("Warning: Databento returned no data for both primary and fallback symbols.")
            empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
            empty.to_csv(output_csv, index=False)
            print(f"File saved: {output_csv.resolve()} (0 rows)")
            return 0
        combined_1m = gc_1m
    else:
        mgc_start_utc = mgc_1m.index.min().tz_convert("UTC")
        if mgc_start_utc > start_utc:
            print(
                f"{PRIMARY_SYMBOL} starts at {mgc_1m.index.min().isoformat()} ET; "
                f"backfilling earlier range with {FALLBACK_SYMBOL}."
            )
            gc_raw = db_get_range_df(
                client,
                dataset=DATASET,
                symbols=[FALLBACK_SYMBOL],
                schema=SCHEMA,
                stype_in=STYPE_IN,
                start=start_utc,
                end=mgc_start_utc,
                raw_cache_dir=raw_cache_dir,
            )
            print(f"Row count after fallback {FALLBACK_SYMBOL} fetch: {len(gc_raw)}")
            gc_1m = normalize_ohlcv_1m(gc_raw, et_tz)
            if gc_1m.empty:
                print("Warning: fallback returned no rows; continuing with MGC-only history.")
            else:
                combined_1m = pd.concat([gc_1m, mgc_1m]).sort_index()
                combined_1m = combined_1m[~combined_1m.index.duplicated(keep="last")]

    if combined_1m.empty:
        print("Warning: Databento returned no usable 1m rows for requested range.")
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        empty.to_csv(output_csv, index=False)
        print(f"File saved: {output_csv.resolve()} (0 rows)")
        return 0

    print(f"Row count after combined 1m normalization: {len(combined_1m)}")
    out = combined_1m.copy()
    out.index.name = "timestamp"
    out = out.reset_index()
    out["timestamp"] = out["timestamp"].map(lambda ts: ts.isoformat())
    out = out[["timestamp", "open", "high", "low", "close", "volume"]]
    print(f"Row count saved (1m): {len(out)}")

    out.to_csv(output_csv, index=False, float_format="%.2f")
    print(f"File saved: {output_csv.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
