"""Linux/systemd watchdog for the Advisor Terminal.

Checks the deterministic trust report plus service and pipeline liveness.  It
never attempts a trade or manufactures a brief.  Alerts are de-duplicated by
condition and day, then cleared after recovery so a recurrence is visible.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.production_status import assess

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
STATE = DATA / "watchdog_linux_state.json"
SERVICES = ("advisor-terminal.service", "advisor-quoted.service",
            "advisor-exitwatch.service")


def _now() -> datetime:
    return datetime.now(ET)


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"active_alerts": {}}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, STATE)


def _send(state: dict, key: str, message: str) -> None:
    alerts = state.setdefault("active_alerts", {})
    fingerprint = f"{_now().date().isoformat()}:{message}"
    if alerts.get(key) == fingerprint:
        return
    alerts[key] = fingerprint
    print(f"[watchdog] ALERT {key}: {message}", flush=True)
    try:
        from advisor import telegram_io
        telegram_io.send(f"🐕 ADVISOR WATCHDOG — {message}")
    except Exception as exc:
        print(f"[watchdog] notification delivery failed: {exc}", file=sys.stderr)


def _recover(state: dict, key: str) -> None:
    state.setdefault("active_alerts", {}).pop(key, None)


def check_services(state: dict) -> None:
    bad = []
    for unit in SERVICES:
        r = subprocess.run(["systemctl", "--user", "is-active", unit],
                           capture_output=True, text=True, timeout=10)
        if r.stdout.strip() != "active":
            bad.append(f"{unit}={r.stdout.strip() or 'unknown'}")
    if bad:
        _send(state, "services", "service failure: " + ", ".join(bad))
    else:
        _recover(state, "services")


def check_pipeline_stuck(state: dict) -> None:
    status_path = DATA / "pipeline_status.json"
    try:
        status = json.loads(status_path.read_text())
        age_min = (_now() - datetime.fromisoformat(status["updated_at"])).total_seconds() / 60
    except Exception:
        return
    if status.get("state") == "running" and age_min > 35:
        _send(state, "pipeline_stuck",
              f"pipeline stage {status.get('stage')} has no update for {age_min:.0f}m")
    else:
        _recover(state, "pipeline_stuck")


def check_trust(state: dict) -> None:
    report = assess()
    now = _now()
    # Before the morning SLO, missing publication is expected. Data/journal
    # blockers are never expected and are still paged immediately.
    blockers = list(report["blockers"])
    if now.weekday() <= 4 and now.strftime("%H:%M") < "09:50":
        blockers = [b for b in blockers if b != "publication"]
    if blockers:
        details = "; ".join(report["checks"][k]["detail"] for k in blockers)
        _send(state, "trust", f"trust gate BLOCKED: {details}")
    else:
        _recover(state, "trust")


def main() -> int:
    state = _load()
    for fn in (check_services, check_pipeline_stuck, check_trust):
        try:
            fn(state)
        except Exception as exc:
            _send(state, f"watchdog_{fn.__name__}",
                  f"watchdog check crashed: {fn.__name__}: {type(exc).__name__}")
    state["last_run"] = _now().isoformat()
    _save(state)
    print(f"[watchdog] complete {_now().isoformat()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
