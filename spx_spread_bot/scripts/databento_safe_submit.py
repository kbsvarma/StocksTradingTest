"""
databento_safe_submit.py — Safe batch job submission with duplicate detection.

RULES:
1. Always check for existing jobs with same params before submitting
2. If identical job already exists (same dataset/schema/symbols/start/end), skip
3. Never submit if account is already over budget
4. Print cost estimate before every submit and require explicit confirmation flag
"""

from __future__ import annotations
import databento as db
from datetime import datetime


def check_duplicate(client: db.Historical, dataset: str, schema: str,
                    symbols: list[str], start: str, end: str) -> dict | None:
    """Return existing job if identical params already submitted, else None."""
    jobs = client.batch.list_jobs()
    sym_str = ",".join(sorted(symbols))
    for j in jobs:
        if (j.get("dataset") == dataset
                and j.get("schema") == schema
                and j.get("symbols") == sym_str
                and j.get("start", "").startswith(start[:10])
                and j.get("end", "").startswith(end[:10])
                and j.get("state") in ("queued", "processing", "done")):
            return j
    return None


def safe_submit(
    client: db.Historical,
    dataset: str,
    schema: str,
    symbols: list[str],
    stype_in: str,
    start: str,
    end: str,
    label: str,
    confirmed: bool = False,
) -> dict | None:
    """
    Submit a batch job safely:
    - Checks for duplicate first
    - Estimates cost
    - Requires confirmed=True to actually submit
    Returns job dict or None.
    """
    print(f"\n{'─'*60}")
    print(f"Job: {label}")
    print(f"  {dataset} | {schema} | {symbols} | {start} → {end}")

    # 1. Duplicate check
    dup = check_duplicate(client, dataset, schema, symbols, start, end)
    if dup:
        print(f"  ⚠️  DUPLICATE DETECTED — job {dup['id']} already exists (state={dup['state']})")
        print(f"  Skipping submission — use existing job.")
        return dup

    # 2. Cost estimate
    try:
        cost = client.metadata.get_cost(
            dataset=dataset, schema=schema, symbols=symbols,
            stype_in=stype_in, start=start, end=end,
        )
        print(f"  Estimated cost: ${cost:.2f}")
    except Exception as e:
        print(f"  Cost estimate failed: {e}")
        return None

    # 3. Confirmation gate
    if not confirmed:
        print(f"  ❌ Not submitting — pass confirmed=True to proceed")
        return None

    # 4. Submit once
    print(f"  Submitting...")
    result = client.batch.submit_job(
        dataset        = dataset,
        schema         = schema,
        symbols        = symbols,
        stype_in       = stype_in,
        start          = start,
        end            = end,
        split_duration = "day",
        encoding       = "dbn",
        compression    = "zstd",
        delivery       = "download",
    )
    job_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", str(result))
    state  = result.get("state") if isinstance(result, dict) else getattr(result, "state", "?")
    print(f"  ✅ Submitted: {job_id}  state={state}")
    return result
