#!/usr/bin/env python3
"""Heartbeat watchdog — runs every 5 min via launchd (com.trendbot.watchdog).

The bot writes state/heartbeat every <=5 min (subpoll). launchd KeepAlive
restarts a DEAD process, but a WEDGED one (alive, stuck in a blocking IB
call — first seen 2026-07-16 01:33Z, 20 min silent) fools it. This kills
the bot when the heartbeat is >15 min stale; launchd relaunches it and the
startup catch-up wake recovers the hour. Alert-only watchdogs page a human
at 9pm; this one fixes it and then mentions it.
"""
import gzip
import fcntl
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HB = BASE / "state" / "heartbeat"
STALE_S = 900
MIN_FREE_GB = 2.0        # disk-full broke the heartbeat on 2026-07-11
ERRLOG_MAX_MB = 50
JSONL_GZ_AFTER_DAYS = 30
ALERT_STATE = BASE / "state" / "watchdog_alerts.json"
LOCK = BASE / "state" / "trading.lock"


def _alert(msg: str, *, key: str | None = None,
           cooldown_s: float = 0) -> bool:
    try:
        state = {}
        if key and ALERT_STATE.exists():
            state = json.loads(ALERT_STATE.read_text())
            if time.time() - float(state.get(key, 0)) < cooldown_s:
                return False
        sys.path.insert(0, str(BASE))
        from alerts import retry_pending, send_alert
        alert_id = send_alert(msg)
        retry_pending(limit=20)
        if key and alert_id is not None:
            state[key] = time.time()
            tmp = ALERT_STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, sort_keys=True))
            tmp.replace(ALERT_STATE)
        return alert_id is not None
    except Exception:
        return False


def _locked_bot_pid() -> int | None:
    """Return the PID only if the kernel lock is held by that bot process."""
    try:
        with LOCK.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # A live process owns the kernel lock. Read the identity while
                # that ownership is still established, then verify its argv.
                lock_file.seek(0)
                pid = int(json.loads(lock_file.read())["pid"])
                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and "bot.py" in result.stdout:
                    return pid
            else:
                # The PID text is stale: no process owns the lock, so killing
                # any PID found in the file would risk PID-reuse collateral.
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError):
        return None
    return None


def check_disk():
    st = os.statvfs(BASE)
    free_gb = st.f_bavail * st.f_frsize / 1e9
    if free_gb < MIN_FREE_GB:
        _alert(f"🚨 trend_bot host disk LOW: {free_gb:.1f} GB free "
               f"(< {MIN_FREE_GB} GB) — state writes at risk",
               key="disk_low", cooldown_s=3600)


def rotate_logs():
    err = BASE / "state" / "bot.err.log"
    if err.exists() and err.stat().st_size > ERRLOG_MAX_MB * 1e6:
        # copy-truncate: the running process keeps its fd, so a move would
        # let the OLD file keep growing invisibly until restart (review).
        # Truncating in place bounds disk now; the writer's offset makes the
        # file sparse, which is fine.
        shutil.copyfile(err, err.with_suffix(".log.1"))
        with open(err, "r+") as f:
            f.truncate(0)
    cutoff = datetime.now(timezone.utc).timestamp() - JSONL_GZ_AFTER_DAYS * 86400
    for f in (BASE / "state" / "logs").glob("*.jsonl"):
        if f.stat().st_mtime < cutoff:
            with open(f, "rb") as src, gzip.open(f"{f}.gz", "wb") as dst:
                shutil.copyfileobj(src, dst)
            f.unlink()


HALT_GRACE_S = 3600      # a halt that self-clears within the hour is normal recovery


def check_halt():
    """Page while entries stay halted past the grace window."""
    try:
        state = json.loads((BASE / "state" / "state.json").read_text())
    except Exception:
        return
    seen = {}
    if ALERT_STATE.exists():
        try:
            seen = json.loads(ALERT_STATE.read_text())
        except Exception:
            seen = {}

    def _persist(d):
        tmp = ALERT_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, sort_keys=True))
        tmp.replace(ALERT_STATE)

    if not state.get("halt_entries"):
        if seen.pop("halt_first_seen", None) is not None:
            _persist(seen)          # halt cleared — reset the clock
        return

    now = time.time()
    first = float(seen.get("halt_first_seen") or 0)
    if not first:
        seen["halt_first_seen"] = now
        _persist(seen)
        return
    if now - first < HALT_GRACE_S:
        return
    hours = (now - first) / 3600
    reason = state.get("halt_reason")
    _alert(
        f"\U0001F6D1 trend_bot ENTRIES HALTED {hours:.1f}h - reason: {reason!r}. "
        f"No new entries until cleared (verify broker flat, then clear "
        f"halt_entries).",
        key="halt_entries", cooldown_s=21600)


def main():
    try:
        check_disk()
        rotate_logs()
        sys.path.insert(0, str(BASE))
        from alerts import delivery_health, retry_pending
        retry_pending(limit=20)
        health = delivery_health()
        if health["undelivered"]:
            with (BASE / "state" / "events.log").open("a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                        f"ALERT DELIVERY DEGRADED: {health}\n")
    except Exception as e:
        _alert(f"🚨 watchdog maintenance failed: {e}",
               key="watchdog_maintenance", cooldown_s=3600)
    try:
        check_halt()
    except Exception:
        pass
    if not HB.exists():
        pid = _locked_bot_pid()
        if pid is not None:
            # The timer's first run is five minutes after boot. A verified
            # bot process that still has not reached run_forever's initial
            # heartbeat is wedged during construction/startup; leaving it
            # alive defeats the watchdog precisely on a fresh host.
            os.kill(pid, signal.SIGKILL)
            _alert("🔧 trend_bot heartbeat was MISSING while a verified "
                   f"lock-owning bot process (pid {pid}) was alive — killed "
                   "it so the service manager can restart cleanly")
        else:
            _alert("🚨 trend_bot heartbeat file is MISSING and no verified "
                   "bot lock owner exists — service readiness unknown",
                   key="heartbeat_missing", cooldown_s=3600)
        return
    try:
        hb = datetime.fromisoformat(HB.read_text().strip())
        if hb.tzinfo is None:
            raise ValueError("heartbeat lacks timezone")
    except (ValueError, OSError):
        # r6 P1: a malformed heartbeat is a WEDGED/corrupt process signal,
        # not a reason to stand down — treat as maximally stale
        hb = datetime.fromtimestamp(0, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - hb).total_seconds()
    if age <= STALE_S:
        return
    pid = _locked_bot_pid()
    if pid is None:
        _alert(f"🚨 watchdog: heartbeat {age/60:.0f}min stale but the "
               "kernel-lock owner could not be verified — refusing broad "
               "process kill; inspect host now",
               key="stale_unverified", cooldown_s=900)
        return
    os.kill(pid, signal.SIGKILL)
    msg = (f"watchdog: heartbeat {age/60:.0f}min stale — killed wedged bot "
           f"(verified lock-owner pid {pid}); service manager relaunches + "
           "catch-up wake")
    with (BASE / "state" / "events.log").open("a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                f"{msg}\n")
    _alert(f"🔧 {msg}")

if __name__ == "__main__":
    main()
