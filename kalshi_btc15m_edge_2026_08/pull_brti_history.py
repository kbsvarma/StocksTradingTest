#!/usr/bin/env python3
"""Download entitled historical BRTI values through Kalshi's passthrough.

The output is a compact, atomic JSONL cache with one row per UTC hour. This is
read-only research code; it cannot submit or cancel orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"
CACHE = DATA / "brti_200ms_hours.jsonl"
CLIENT_DIR = REPO / "kalshi_unbroken_bot"
sys.path.insert(0, str(CLIENT_DIR))

from kalshi_client import Credentials, KalshiClient  # noqa: E402


UTC = dt.timezone.utc


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def hour_floor(value: dt.datetime) -> dt.datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def iso_millis(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:00:00.000Z")


def iter_hours(start: dt.datetime, end: dt.datetime):
    current = hour_floor(start)
    finish = hour_floor(end)
    while current <= finish:
        yield current
        current += dt.timedelta(hours=1)


def read_cache(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    with path.open() as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row["hour"]] = row
    return rows


def write_cache(path: Path, rows: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            for key in sorted(rows):
                handle.write(json.dumps(rows[key], separators=(",", ":")) + "\n")
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def pull_hour(
    client: KalshiClient, hour: dt.datetime, whole_seconds_only: bool = False
) -> dict:
    response = client._request(
        "GET",
        "/cfbenchmarks/history/values",
        params={"id": "BRTI", "timespan": "HOUR", "timestamp": iso_millis(hour)},
    )
    data = response.get("data") or {}
    if data.get("error"):
        raise RuntimeError(f"CF Benchmarks error: {data['error']}")
    payload = data.get("payload") or []
    values = [[int(row["time"]), row["value"]] for row in payload]
    if whole_seconds_only:
        values = [row for row in values if row[0] % 1000 == 0]
    minimum = 3500 if whole_seconds_only else 17000
    if len(values) < minimum:
        raise RuntimeError(f"Incomplete BRTI hour {iso_millis(hour)}: {len(values)} rows")
    return {
        "hour": iso_millis(hour),
        "server_time": data.get("serverTime"),
        "resolution_ms": min(
            (
                right[0] - left[0]
                for left, right in zip(values, values[1:])
                if right[0] > left[0]
            ),
            default=None,
        ),
        "values": values,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-07-27T20:00:00Z")
    parser.add_argument("--end", default="2026-08-04T04:00:00Z")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--whole-seconds-only", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    cached = read_cache(args.cache)
    hours = list(iter_hours(parse_utc(args.start), parse_utc(args.end)))
    missing = [hour for hour in hours if iso_millis(hour) not in cached]
    print(
        f"BRTI hours requested={len(hours)} "
        f"cached={len(hours) - len(missing)} missing={len(missing)}"
    )
    if not missing:
        return

    credentials = Credentials.load()

    def download(hour: dt.datetime) -> dict:
        client = KalshiClient(
            credentials, base_url="https://external-api.kalshi.com", dry_run=True
        )
        return pull_hour(client, hour, args.whole_seconds_only)

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download, hour): hour for hour in missing}
        for future in as_completed(futures):
            row = future.result()
            cached[row["hour"]] = row
            completed += 1
            if completed % 24 == 0 or completed == len(missing):
                write_cache(args.cache, cached)
                print(f"downloaded {completed}/{len(missing)}", flush=True)
            if args.workers == 1:
                time.sleep(0.27)


if __name__ == "__main__":
    main()
