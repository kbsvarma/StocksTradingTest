"""Atomic, machine-readable status for the research publication pipeline."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
STATUS = REPO / "advisor" / "data" / "pipeline_status.json"


def classify_text(returncode: int, text: str = "") -> str:
    if returncode == 0:
        return "none"
    if returncode == 124:
        return "timeout"
    text = text.lower()
    if any(marker in text for marker in (
            "session limit", "usage limit", "rate limit", "hit your limit",
            "quota", "resets at", "resets ")):
        return "provider_quota"
    if any(marker in text for marker in (
            "login", "authentication", "unauthorized", "not authenticated")):
        return "provider_auth"
    if returncode == 3:
        return "output_validation"
    if returncode == 75:
        return "already_running"
    return "stage_failure"


def classify_failure(returncode: int, log_path: Path | None = None) -> str:
    text = ""
    if log_path and log_path.exists():
        try:
            text = log_path.read_text(errors="replace")[-12000:]
        except OSError:
            pass
    return classify_text(returncode, text)


def write_status(*, run_id: str, date: str, state: str, stage: str,
                 returncode: int | None = None, reason: str = "none",
                 note: str = "", started_at: str | None = None) -> dict:
    """Replace status atomically so readers never observe partial JSON."""
    old: dict = {}
    try:
        old = json.loads(STATUS.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    if old.get("run_id") != run_id:
        old = {"run_id": run_id, "date": date,
               "started_at": started_at or datetime.now(ET).isoformat()}
    row = {
        **old,
        "schema_version": 1,
        "date": date,
        "state": state,
        "stage": stage,
        "returncode": returncode,
        "reason": reason,
        "note": note,
        "updated_at": datetime.now(ET).isoformat(),
        # Pipeline completion attests publication, not investment actionability.
        # Only production_status may combine publication, calibration, portfolio,
        # runtime and release gates into an actionable decision.
        "validated_publication_available": state == "complete",
        "actionable_output_allowed": False,
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(row, indent=2) + "\n")
    os.replace(tmp, STATUS)
    return row
