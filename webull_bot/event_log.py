"""Structured JSON event log for fine-tuning analysis.

Every meaningful decision the bot makes gets one JSON line in
{log_dir}/events-YYYY-MM-DD.jsonl. Rotated daily by date in filename.
Designed to be aggregated, sliced, and replayed for parameter tuning.

Events emitted (one line each):
  signal_eval     — every scan tick with all gate results
  vix_skip        — VIX gate failed
  direction_skip  — direction filter failed
  no_spread       — chain scan returned no qualifying spread
  chain_scan      — full top-N spread candidates with bid/ask/mid
  picked_spread   — the spread chosen for entry
  entry_attempt   — order placed (real or dry-run)
  entry_filled    — order filled, with synthesized vs real price
  monitor_tick    — every spread mark check during monitoring
  stop_triggered  — 2× stop hit
  position_closed — exit booked (any reason)
  force_report    — when user runs --report
  bot_start       — process boot
  bot_stop        — process exit (best-effort)

Usage:
    from webull_bot.event_log import log_event
    log_event("signal_eval", spx=7400.5, vix=18.2, vix_ok=True, dir_ok=False, ...)

Failure mode: if log dir not writable, errors are swallowed (never blocks bot).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Default log dir — overridable via env WEBULL_EVENT_LOG_DIR
_LOG_DIR = Path(os.environ.get("WEBULL_EVENT_LOG_DIR", "/opt/webull-bot/logs/events"))
_lock = threading.Lock()


def _ensure_dir() -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _today_path() -> Path:
    today = datetime.now(ET).strftime("%Y-%m-%d")
    return _LOG_DIR / f"events-{today}.jsonl"


def log_event(event_type: str, **fields) -> None:
    """Append one JSON line to today's events file. Best-effort, never raises."""
    try:
        _ensure_dir()
        record = {
            "ts_utc": datetime.now(UTC).isoformat(),
            "ts_et":  datetime.now(ET).isoformat(),
            "event":  event_type,
            **fields,
        }
        # Convert any non-JSON-serializable to str (cheap fallback)
        line = json.dumps(record, default=str, separators=(",", ":"))
        with _lock:
            with open(_today_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass  # never block the bot
