"""Shared event queue — append-only JSON-lines file.

Producers (bot, watchdog, etc.) call queue_event(...) to append.
Consumer (telegram_service.py) reads, sends to Telegram, marks as sent
in a separate sent.log file. Never deletes from queue (audit trail).

Why this exists:
  Pre-2026-05-22: every component called alerts.send_alert() directly.
  alerts._post_blocking silently swallowed all exceptions. If a Telegram
  send failed (API down, rate limit, network blip), we'd never know.

  Now: producers JUST append to a local file (always succeeds). The
  service is the only thing that calls Telegram, and it LOGS every send
  attempt with the HTTP response. No silent failures.

File: /tmp/event_queue.jsonl  (append-only)

Each line is one event:
    {"id": "uuid", "ts": "ISO8601-ET", "severity": "info|warn|critical",
     "source": "bot|watchdog|monitor|...", "subject": "short title",
     "body": "long details (multi-line ok)", "dedup_key": "optional"}
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EVENT_QUEUE = Path("/tmp/event_queue.jsonl")
SEVERITY_LEVELS = ("info", "warn", "critical")


def queue_event(
    severity: str,
    source: str,
    subject: str,
    body: str = "",
    dedup_key: str = "",
) -> str:
    """Append an event to the queue. Returns event id.

    Atomic append via single open(a)+write+close. POSIX guarantees
    atomicity for writes <4KB (a single JSON line is always well under).
    Never raises — best-effort observability.
    """
    if severity not in SEVERITY_LEVELS:
        severity = "info"
    event = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(ET).isoformat(),
        "severity": severity,
        "source": source,
        "subject": subject,
        "body": body or subject,
        "dedup_key": dedup_key,
    }
    line = json.dumps(event) + "\n"
    try:
        with EVENT_QUEUE.open("a") as f:
            f.write(line)
    except Exception:
        # Last-resort fallback: print to stderr so nothing is fully silent
        import sys
        print(f"[event_queue] FAILED TO WRITE: {line.strip()}", file=sys.stderr, flush=True)
    return event["id"]


def read_events(after_id: str = "", limit: int = 100) -> list[dict]:
    """Read events from queue. Optionally skip everything up to (and incl) after_id.

    Returns list of event dicts in file order. Malformed lines skipped silently.
    """
    if not EVENT_QUEUE.exists():
        return []
    events = []
    skipping = bool(after_id)
    try:
        for line in EVENT_QUEUE.open():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if skipping:
                if event.get("id") == after_id:
                    skipping = False
                continue
            events.append(event)
            if len(events) >= limit:
                break
    except Exception:
        pass
    return events


def severity_rank(severity: str) -> int:
    """Higher = more urgent. For draining queue in priority order."""
    return {"critical": 2, "warn": 1, "info": 0}.get(severity, 0)
