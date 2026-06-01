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
STALE_THRESHOLD_MIN   = 12    # alert if heartbeat older than this (bumped 5→12 on 2026-05-19; post-entry-cutoff bot sleeps 10min between heartbeats, was triggering false alarms)
RE_ALARM_COOLDOWN_MIN = 30    # wait this long before re-alarming for the same outage
# IBKR-down detection (added 2026-06-01): the bot can be ALIVE (fresh heartbeat)
# yet blind — falling back to yfinance because IBKR Gateway is down. That state
# was silent for a week. If the chain source is not IBKR for this long during
# market hours, alert so the operator can re-auth the gateway (2FA = manual tap).
IBKR_FALLBACK_THRESHOLD_MIN = 5
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


def _read_chain_source() -> str:
    """Return the heartbeat's chain_source ('ibkr'/'yfinance'/'unknown'/'')."""
    try:
        raw = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        return (raw.get("chain_source") or raw.get("spx_source") or "").lower()
    except Exception:
        return ""


def _check_ibkr_source(now_et: datetime, wd: dict) -> None:
    """Alert if the chain source has been non-IBKR for too long during market hours.

    Catches the 'bot alive but blind on yfinance fallback' state that was silent
    for a week. Tracks first-seen + last-alarm in the watchdog state, mutating
    `wd` in place (caller persists it). Never raises.
    """
    try:
        src = _read_chain_source()
        now_iso = datetime.now(UTC).isoformat()
        if src == "ibkr":
            # Healthy source — clear tracking + recovery ping if we'd alarmed.
            if wd.get("ibkr_alarm_active"):
                send_alert(f"✅  IBKR data feed recovered ({now_et.strftime('%H:%M ET')}) — bot back on real-time quotes")
            wd["ibkr_down_since"] = ""
            wd["ibkr_alarm_active"] = False
            wd["ibkr_last_alarm_ts"] = ""
            return
        if not src:
            return  # can't determine source — don't false-alarm
        # Non-IBKR (yfinance / unknown): start/continue the timer.
        down_since = wd.get("ibkr_down_since") or now_iso
        wd["ibkr_down_since"] = down_since
        try:
            down_min = (datetime.now(UTC) - datetime.fromisoformat(down_since).astimezone(UTC)).total_seconds() / 60.0
        except Exception:
            down_min = 0.0
        if down_min < IBKR_FALLBACK_THRESHOLD_MIN:
            return
        # Past threshold — alarm with its own cooldown.
        last_alarm = wd.get("ibkr_last_alarm_ts", "")
        cooldown_ok = True
        if last_alarm:
            try:
                since = (datetime.now(UTC) - datetime.fromisoformat(last_alarm).astimezone(UTC)).total_seconds() / 60.0
                cooldown_ok = since >= RE_ALARM_COOLDOWN_MIN
            except Exception:
                cooldown_ok = True
        if cooldown_ok:
            send_alert(
                f"🟠  IBKR data feed DOWN — bot is alive but on *{src}* fallback for "
                f"{int(down_min)} min. Re-auth IB Gateway (2FA tap) so the bot gets "
                f"real-time quotes + the option chain back. No qualifying spreads can "
                f"be found without IBKR."
            )
            wd["ibkr_alarm_active"] = True
            wd["ibkr_last_alarm_ts"] = now_iso
    except Exception:
        pass  # watchdog must never crash on the source check


def main() -> int:
    now_et = datetime.now(ET)
    if not _in_market_hours(now_et):
        return 0   # silent outside market hours

    age_min, last_ts = _read_heartbeat_age_min()
    if age_min < STALE_THRESHOLD_MIN:
        # Heartbeat is fresh (process alive). Clear any staleness alarm…
        wd = _load_watchdog_state()
        if wd.get("alarm_active"):
            send_alert(f"✅  Webull bot heartbeat recovered ({now_et.strftime('%H:%M ET')})")
            wd["alarm_active"] = False
            wd["last_alarm_ts"] = ""
        # …but ALSO check it isn't silently blind on yfinance fallback.
        _check_ibkr_source(now_et, wd)
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
