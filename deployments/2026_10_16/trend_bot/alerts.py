"""Durable Telegram alert outbox with retry/delivery visibility."""
from __future__ import annotations

import os
import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTBOX = BASE / "state" / "alerts.db"
FALLBACK = BASE / "state" / "alerts_fallback.log"
DISABLED_FLAG = BASE / "state" / "alerts_disabled"


def _disabled() -> bool:
    """Kill switch: drop the sentinel file to silence Telegram entirely
    (2026-08-02, user turned off paging for the paper bot). Keeps the module
    stdlib-only and reversible — delete the file to re-enable."""
    return DISABLED_FLAG.exists()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OUTBOX, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("""
        CREATE TABLE IF NOT EXISTS alert_outbox (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created TEXT NOT NULL,
          text TEXT NOT NULL,
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          last_attempt TEXT,
          delivered TEXT
        )
    """)
    return con


def _credentials() -> tuple[str, str]:
    return (os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            os.environ.get("TELEGRAM_CHAT_ID", ""))


def _write_fallback(line: str) -> None:
    """Best-effort last resort; alerting must never raise into trading."""
    try:
        FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        with FALLBACK.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _safe_error(exc: Exception, *secrets: str) -> str:
    """Render an exception without persisting credentials embedded in URLs."""
    value = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            for form in (secret, urllib.parse.quote(secret, safe="")):
                value = value.replace(form, "[REDACTED]")
    return value[:500]


def _claim(alert_id: int) -> tuple[str, int] | None:
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                "SELECT text,attempts,status,last_attempt FROM alert_outbox "
                "WHERE id=?",
                (alert_id,)).fetchone()
            stale_before = (datetime.now(timezone.utc) -
                            timedelta(seconds=60)).isoformat(timespec="seconds")
            if (row is None or row[2] == "DELIVERED" or
                    (row[2] == "SENDING" and row[3] and
                     row[3] >= stale_before)):
                con.rollback()
                return None
            attempts = int(row[1]) + 1
            con.execute(
                "UPDATE alert_outbox SET status='SENDING',attempts=?,"
                "last_attempt=? WHERE id=?",
                (attempts, _utcnow(), alert_id))
            con.commit()
            return row[0], attempts
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


def _deliver(alert_id: int):
    claimed = _claim(alert_id)
    if claimed is None:
        return
    text, _ = claimed
    token, chat = _credentials()
    try:
        if not token or not chat:
            raise RuntimeError("Telegram credentials are not configured")
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": f"📈 trend_bot\n{text}",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Telegram HTTP {response.status}")
        con = _connect()
        try:
            with con:
                con.execute(
                    "UPDATE alert_outbox SET status='DELIVERED',delivered=?,"
                    "last_error=NULL WHERE id=?", (_utcnow(), alert_id))
        finally:
            con.close()
    except Exception as e:
        safe_error = _safe_error(e, token, chat)
        con = _connect()
        try:
            with con:
                con.execute(
                    "UPDATE alert_outbox SET status='FAILED',last_error=? "
                    "WHERE id=?", (safe_error, alert_id))
        finally:
            con.close()
        _write_fallback(
            f"{_utcnow()} alert_id={alert_id} delivery_failed={safe_error} "
            f"text={text!r}")


def send_alert(text: str) -> int | None:
    """Persist before attempting delivery; return durable outbox id."""
    if _disabled():
        return None
    try:
        con = _connect()
        try:
            with con:
                cur = con.execute(
                    "INSERT INTO alert_outbox(created,text,status) "
                    "VALUES (?,?,'PENDING')", (_utcnow(), text))
                alert_id = int(cur.lastrowid)
        finally:
            con.close()
    except Exception as e:
        _write_fallback(
            f"{_utcnow()} outbox_write_failed={_safe_error(e)} "
            f"text={text!r}")
        return None
    try:
        threading.Thread(target=_deliver, args=(alert_id,), daemon=True).start()
    except Exception as e:
        _write_fallback(
            f"{_utcnow()} alert_id={alert_id} "
            f"thread_start_failed={_safe_error(e)} "
            f"text={text!r}")
    return alert_id


def retry_pending(limit: int = 20) -> int:
    """Synchronously retry oldest undelivered alerts (watchdog entry point)."""
    if _disabled():
        return 0
    con = _connect()
    try:
        stale_before = (datetime.now(timezone.utc) -
                        timedelta(seconds=60)).isoformat(timespec="seconds")
        ids = [r[0] for r in con.execute(
            "SELECT id FROM alert_outbox WHERE status IN ('PENDING','FAILED') "
            "OR (status='SENDING' AND last_attempt<?) ORDER BY id LIMIT ?",
            (stale_before, limit))]
    finally:
        con.close()
    for alert_id in ids:
        _deliver(alert_id)
    return len(ids)


def delivery_health() -> dict:
    if _disabled():
        return {"undelivered": 0, "oldest": None, "max_attempts": 0}
    con = _connect()
    try:
        row = con.execute(
            "SELECT COUNT(*),MIN(created),MAX(attempts) FROM alert_outbox "
            "WHERE status!='DELIVERED'").fetchone()
        return {"undelivered": int(row[0]), "oldest": row[1],
                "max_attempts": int(row[2] or 0)}
    finally:
        con.close()
