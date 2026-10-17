"""Verify ATM bot tags Telegram events distinctly from legacy."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_atm_events_get_atm_tag_prefix():
    """ATM bot's queue_event must prepend [ATM] to subject."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        tmp = Path(f.name)
    try:
        with patch("webull_bot_atm.event_queue.EVENT_QUEUE", tmp):
            from webull_bot_atm.event_queue import queue_event
            event_id = queue_event(
                severity="info",
                source="bot",
                subject="Entered position 5740/5690 cred $1.50",
                body="details",
                dedup_key="entry-2026-05-22",
            )
        assert event_id, "queue_event should return an id"
        line = tmp.read_text().strip()
        assert line, "Event should be written"
        event = json.loads(line)
        assert event["subject"].startswith("[ATM] "), f"Subject should start with [ATM], got: {event['subject']}"
        assert event["bot"] == "ATM", "bot field should be ATM"
        assert event["source"].startswith("atm:"), f"source should be prefixed atm:, got {event['source']}"
        assert event["dedup_key"].startswith("atm:"), f"dedup_key should be namespaced, got {event['dedup_key']}"
    finally:
        tmp.unlink(missing_ok=True)


def test_atm_dedup_key_doesnt_collide_with_legacy():
    """Same logical dedup key from ATM and legacy must be distinct."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        tmp = Path(f.name)
    try:
        with patch("webull_bot_atm.event_queue.EVENT_QUEUE", tmp):
            from webull_bot_atm.event_queue import queue_event
            queue_event(severity="info", source="bot", subject="X", dedup_key="entry-2026-05-22")
        lines = tmp.read_text().strip().splitlines()
        assert len(lines) == 1
        ev = json.loads(lines[0])
        # Legacy event with same logical dedup key would write "entry-2026-05-22"
        # ATM event writes "atm:entry-2026-05-22"
        assert ev["dedup_key"] == "atm:entry-2026-05-22"
    finally:
        tmp.unlink(missing_ok=True)


def test_atm_doesnt_double_tag():
    """If subject already starts with [ATM], don't double-tag."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        tmp = Path(f.name)
    try:
        with patch("webull_bot_atm.event_queue.EVENT_QUEUE", tmp):
            from webull_bot_atm.event_queue import queue_event
            queue_event(severity="info", source="bot", subject="[ATM] Already tagged", dedup_key="")
        ev = json.loads(tmp.read_text().strip())
        assert ev["subject"] == "[ATM] Already tagged", f"Should not double-tag: {ev['subject']}"
    finally:
        tmp.unlink(missing_ok=True)


def test_atm_critical_severity_preserved():
    """Severity must NOT be modified by tagging."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        tmp = Path(f.name)
    try:
        with patch("webull_bot_atm.event_queue.EVENT_QUEUE", tmp):
            from webull_bot_atm.event_queue import queue_event
            queue_event(severity="critical", source="monitor", subject="SL FIRED", dedup_key="sl-fire-1")
        ev = json.loads(tmp.read_text().strip())
        assert ev["severity"] == "critical"
    finally:
        tmp.unlink(missing_ok=True)
