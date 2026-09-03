"""Operator control plane for on-demand brief generation.

The web terminal never runs the model pipeline directly and never inherits its
provider credentials.  It may only ask the user's systemd manager to start the
same hardened oneshot used by the schedule.  Requests are serialized, audited,
and rate-limited; the orchestrator owns the second single-flight lock.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ADVISOR_DATA_DIR", REPO / "advisor" / "data"))
LOGS = REPO / "advisor" / "logs"
AUDIT = LOGS / "brief_control.jsonl"
LOCK = DATA / ".brief_control.lock"
PIPELINE_STATUS = DATA / "pipeline_status.json"
DEFAULT_COOLDOWN_S = 300
UI_ARM_TTL_S = 60


def arm_remaining_s(deadline: object, *, now_ts: float) -> int:
    """Return a bounded whole-second arming TTL for the web control."""
    try:
        remaining = float(deadline) - float(now_ts)
        return max(0, min(UI_ARM_TTL_S, int(remaining)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _append_audit(row: dict) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def last_request() -> dict:
    try:
        for line in reversed(AUDIT.read_text(encoding="utf-8").splitlines()):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("outcome") == "accepted":
                return row
    except OSError:
        pass
    return {}


def status() -> dict:
    """Return display-safe pipeline/control status from local artifacts."""
    pipeline = _read_json(PIPELINE_STATUS)
    return {
        "state": pipeline.get("state", "unknown"),
        "stage": pipeline.get("stage", "unknown"),
        "date": pipeline.get("date"),
        "updated_at": pipeline.get("updated_at"),
        "reason": pipeline.get("reason"),
        "note": pipeline.get("note"),
        "run_id": pipeline.get("run_id"),
        "last_request": last_request(),
    }


def request_generation(*, actor: str = "portal_operator",
                       cooldown_s: int = DEFAULT_COOLDOWN_S,
                       runner=subprocess.run) -> dict:
    """Queue the systemd oneshot without waiting for the pipeline to finish."""
    now = datetime.now(ET)
    request_id = uuid.uuid4().hex
    base = {"schema_version": 1, "request_id": request_id,
            "requested_at": now.isoformat(), "actor": actor,
            "action": "generate_brief"}
    DATA.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            row = {**base, "outcome": "rejected", "reason": "control_busy"}
            _append_audit(row)
            return row

        previous = last_request()
        if previous.get("requested_at"):
            try:
                age = (now - datetime.fromisoformat(previous["requested_at"])).total_seconds()
                if age < cooldown_s:
                    row = {**base, "outcome": "rejected", "reason": "cooldown",
                           "retry_after_s": max(1, int(cooldown_s - age))}
                    _append_audit(row)
                    return row
            except (TypeError, ValueError):
                pass

        try:
            active = runner(["systemctl", "--user", "is-active",
                             "advisor-brief.service"], capture_output=True,
                            text=True, timeout=10)
            if active.stdout.strip() in {"active", "activating", "reloading"}:
                row = {**base, "outcome": "rejected", "reason": "already_running"}
                _append_audit(row)
                return row
            result = runner(["systemctl", "--user", "start", "--no-block",
                             "advisor-brief.service"], capture_output=True,
                            text=True, timeout=15)
        except Exception as exc:
            row = {**base, "outcome": "failed", "reason": "control_error",
                   "detail": type(exc).__name__}
            _append_audit(row)
            return row

        if result.returncode != 0:
            row = {**base, "outcome": "failed", "reason": "service_start_failed",
                   "returncode": result.returncode,
                   "detail": (result.stderr or result.stdout or "")[-300:]}
        else:
            row = {**base, "outcome": "accepted", "reason": "queued"}
        _append_audit(row)
        return row
