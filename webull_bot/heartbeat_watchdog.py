"""Heartbeat watchdog — pings Telegram if the Webull bot's heartbeat is stale.

Runs as a separate scheduled job (its own launchd plist), NOT inside the bot
itself. Reason: if the bot crashes, an in-process watchdog crashes with it.
The watchdog has to live in a separate process to catch bot-down events.

Behavior:
  - Reads webull_bot/data/heartbeat.json (whichever path is configured)
  - If during market hours (09:30–16:00 ET on weekdays) and the heartbeat is
    older than STALE_THRESHOLD_MIN, fires a Telegram alert
  - Tracks last-alerted timestamp in a small state file so we don't spam:
    once an alarm fires, we wait at least RE_ALARM_COOLDOWN_MIN before sending
    the next one for the same outage
  - Silent outside market hours (heartbeat is expected to be stale overnight)

Usage:
    python -m webull_bot.heartbeat_watchdog
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from webull_bot.alerts import alert_bot_down, send_alert

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
_MARKET_OPEN  = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)

# Tunables
STALE_THRESHOLD_MIN   = 5     # alert if heartbeat older than this
RE_ALARM_COOLDOWN_MIN = 30    # wait this long before re-alarming for the same outage
HEARTBEAT_PATH = Path(__file__).resolve().parent.parent / "spx_spread_bot" / "data" / "webull_live" / "vG_spx_w50_vix25" / "heartbeat.json"
WATCHDOG_STATE = Path(__file__).resolve().parent.parent / "spx_spread_bot" / "data" / "webull_live" / "vG_spx_w50_vix25" / "watchdog_state.json"


def _in_market_hours(now_et: datetime) -> bool:
    if now_et.weekday() >= 5:   # Sat/Sun
        return False
    return _MARKET_OPEN <= now_et.time() < _MARKET_CLOSE


def _load_watchdog_state() -> dict:
    if not WATCHDOG_STATE.exists():
        return {}
    try:
        return json.loads(WATCHDOG_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_watchdog_state(d: dict) -> None:
    WATCHDOG_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WATCHDOG_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, WATCHDOG_STATE)


def _read_heartbeat_age_min() -> tuple[float, str]:
    """Return (age_in_minutes, last_ts_iso). age=inf if missing/unreadable."""
    if not HEARTBEAT_PATH.exists():
        return float("inf"), ""
    try:
        raw = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        ts = raw.get("ts", "")
        if not ts:
            return float("inf"), ""
        hb_time = datetime.fromisoformat(ts).astimezone(UTC)
        age_s = (datetime.now(UTC) - hb_time).total_seconds()
        return age_s / 60.0, ts
    except Exception:
        return float("inf"), ""


def main() -> int:
    now_et = datetime.now(ET)
    if not _in_market_hours(now_et):
        return 0   # silent outside market hours

    age_min, last_ts = _read_heartbeat_age_min()
    if age_min < STALE_THRESHOLD_MIN:
        # Healthy — clear any prior alarm tracking
        wd = _load_watchdog_state()
        if wd.get("alarm_active"):
            send_alert(f"✅  Webull bot heartbeat recovered ({now_et.strftime('%H:%M ET')})")
            wd["alarm_active"] = False
            wd["last_alarm_ts"] = ""
            _save_watchdog_state(wd)
        return 0

    # Stale heartbeat during market hours — should we alarm?
    wd = _load_watchdog_state()
    last_alarm = wd.get("last_alarm_ts", "")
    cooldown_ok = True
    if last_alarm:
        try:
            last = datetime.fromisoformat(last_alarm).astimezone(UTC)
            since = (datetime.now(UTC) - last).total_seconds() / 60.0
            cooldown_ok = since >= RE_ALARM_COOLDOWN_MIN
        except Exception:
            cooldown_ok = True

    if cooldown_ok:
        alert_bot_down(age_min, last_ts)
        wd["alarm_active"]  = True
        wd["last_alarm_ts"] = datetime.now(UTC).isoformat()
        _save_watchdog_state(wd)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
