"""Official FRED macro snapshot with per-series freshness and provenance."""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "macro" / "fred_latest.json"
SERIES = {
    "DGS2": {"name": "US Treasury 2Y", "max_age_days": 5},
    "DGS10": {"name": "US Treasury 10Y", "max_age_days": 5},
    "T10YIE": {"name": "10Y breakeven inflation", "max_age_days": 5},
    "BAMLH0A0HYM2": {"name": "US high-yield option-adjusted spread", "max_age_days": 5},
    "NFCI": {"name": "Chicago Fed National Financial Conditions Index", "max_age_days": 10},
}


def _download(series_id: str, opener=urlopen) -> str:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    request = Request(url)
    with opener(request, timeout=12) as response:
        return response.read().decode("utf-8")


def _latest(text: str, series_id: str | None = None) -> tuple[str, float]:
    rows = list(csv.DictReader(io.StringIO(text)))
    for row in reversed(rows):
        raw = (row.get(series_id, "") if series_id else
               next((v for k, v in row.items() if k != "observation_date"), ""))
        if raw not in ("", ".", None):
            return row["observation_date"], float(raw)
    raise ValueError("no numeric observations")


def _download_bulk(opener=urlopen) -> dict[str, str]:
    """Fetch FRED's multi-series ZIP once and return series CSV payloads."""
    ids = ",".join(SERIES)
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={ids}"
    request = Request(url)
    with opener(request, timeout=30) as response:
        payload = response.read()
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if not name.endswith(".csv"):
                continue
            text = archive.read(name).decode("utf-8")
            header = next(csv.reader(io.StringIO(text)), [])
            for series_id in header[1:]:
                out[series_id] = text
    return out


def build(*, now: datetime | None = None, fetcher=None) -> dict:
    now = now or datetime.now(ET)
    observations, errors = {}, {}
    completed = []
    if fetcher is None:
        try:
            payloads = _download_bulk()
            for series_id in SERIES:
                try:
                    date, value = _latest(payloads[series_id], series_id)
                    completed.append((series_id, date, value))
                except Exception as exc:
                    errors[series_id] = f"{type(exc).__name__}: {exc}"
        except Exception as bulk_exc:
            # Some FRED edges intermittently fail the multi-frequency ZIP
            # response while serving individual CSVs normally. Fall back
            # sequentially to avoid the throttling seen with parallel calls.
            for series_id in SERIES:
                try:
                    date, value = _latest(_download(series_id), series_id)
                    completed.append((series_id, date, value))
                except Exception as exc:
                    errors[series_id] = (f"bulk {type(bulk_exc).__name__}; "
                                         f"single {type(exc).__name__}: {exc}")
    else:
        def one(series_id):
            date, value = _latest(fetcher(series_id), series_id)
            return series_id, date, value
        # Test/custom fetchers may still be parallelized; production uses one
        # official bulk request to respect the provider and bound latency.
        with ThreadPoolExecutor(max_workers=len(SERIES)) as pool:
            futures = {pool.submit(one, series_id): series_id for series_id in SERIES}
            for future in as_completed(futures):
                series_id = futures[future]
                try:
                    completed.append(future.result())
                except Exception as exc:
                    errors[series_id] = f"{type(exc).__name__}: {exc}"
    for series_id, date, value in completed:
        meta = SERIES[series_id]
        try:
            age = (now.date() - datetime.fromisoformat(date).date()).days
            observations[series_id] = {
                "name": meta["name"], "value": value, "observation_date": date,
                "age_days": age, "fresh": age <= meta["max_age_days"],
                "source": f"https://fred.stlouisfed.org/series/{series_id}",
            }
        except Exception as exc:
            errors[series_id] = f"{type(exc).__name__}: {exc}"
    if "DGS10" in observations and "DGS2" in observations:
        observations["CURVE_2S10S"] = {
            "name": "10Y minus 2Y Treasury spread",
            "value": round(observations["DGS10"]["value"] - observations["DGS2"]["value"], 4),
            "observation_date": min(observations["DGS10"]["observation_date"],
                                    observations["DGS2"]["observation_date"]),
            "age_days": max(observations["DGS10"]["age_days"], observations["DGS2"]["age_days"]),
            "fresh": observations["DGS10"]["fresh"] and observations["DGS2"]["fresh"],
            "source": "derived from FRED DGS10 and DGS2",
        }
    fresh = sum(1 for k, row in observations.items() if k in SERIES and row["fresh"])
    return {"schema_version": 1, "as_of": now.isoformat(),
            "provider": "Federal Reserve Bank of St. Louis FRED",
            "observations": observations, "errors": errors,
            "fresh_series": fresh, "required_series": len(SERIES),
            "ok": fresh >= 4 and not any(k in errors for k in ("DGS2", "DGS10"))}


def write(result: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(tmp, OUT)


def main() -> int:
    result = build()
    write(result)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
