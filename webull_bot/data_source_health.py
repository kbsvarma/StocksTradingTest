"""Data-source health tracker — fires Telegram alerts on IBKR↔yfinance transitions.

Each call site that fetches market data (spot, VIX, option chain, spread mark)
calls `report(source, up=...)` after every attempt. This module remembers the
last known state per source and fires Telegram alerts on state transitions —
WITH DEBOUNCE so brief blips don't generate spam.

Usage:
    from webull_bot import data_source_health as dsh
    dsh.report("ibkr", up=True)   # call after a successful IBKR fetch
    dsh.report("ibkr", up=False)  # call after a failed IBKR fetch (we fell back)

Debounce policy (per 2026-05-14 user request — Mac sees frequent brief
collisions when ad-hoc scripts grab the bot's IBKR clientId):

  - Up→Down transition: do NOT alert immediately. Only alert if the source
    stays DOWN for at least DOWN_ALERT_AFTER_SEC (30s) without recovering.
    This eats brief blips silently.

  - Down→Up transition: alert IMMEDIATELY (recovery is genuine good news,
    and we want a clear "back online" signal even after a true outage).

  - If state transitions Up→Down→Up within the debounce window, NO alerts
    fire — the blip never reached "down" status from the user's POV.

Source names are free-form strings (lowercase convention, e.g. "ibkr").

Telegram alerts use `webull_bot.alerts.send_alert` which is fire-and-forget
(daemon thread, swallows exceptions) so this module can never block or raise
into the trading path.

Thread-safety: a single lock protects the state dict. Safe across threads.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

_lock = threading.Lock()

# Per-source state record:
#   is_up: True (up) | False (down) | None (never observed)
#   first_down_ts: monotonic time of the most recent up→down transition
#                  (None if currently up or never observed down). Used to
#                  decide if we've crossed the debounce threshold.
#   down_alert_sent: True if we've already sent an "is down" alert for the
#                    current down period — prevents repeat alerts.
_state: dict[str, dict] = {}

# How long IBKR (or any source) must remain DOWN before we Telegram-alert.
# Brief blips (e.g. clientId collision when an ad-hoc script touches IBKR
# while the bot's monitor is running) typically resolve within 1-3 ticks
# (~3-10s). 30s is well above that, but well below "we should know about it
# before tomorrow's 10:30 entry window."
DOWN_ALERT_AFTER_SEC = 30.0


def _send(text: str) -> None:
    """Best-effort Telegram dispatch. Never raises."""
    try:
        from webull_bot.alerts import send_alert
        send_alert(text)
    except Exception:
        pass


def report(source: str, *, up: bool) -> None:
    """Record an observation; emit alerts per the debounce policy.

    `source` — short identifier, e.g. "ibkr". Case-insensitive for state key.
    `up` — True if the data fetch succeeded, False if it failed (fell back).
    """
    key = source.strip().lower()
    if not key:
        return

    name = key.upper()
    now = time.monotonic()

    # Decisions to emit OUTSIDE the lock (to avoid holding lock during I/O).
    emit_up = False
    emit_down = False
    is_first_observation = False

    with _lock:
        rec = _state.get(key)
        if rec is None:
            rec = {"is_up": None, "first_down_ts": None, "down_alert_sent": False}
            _state[key] = rec
            is_first_observation = True

        prev_up = rec["is_up"]

        if up:
            # Currently reported as up
            if prev_up is True:
                # Still up, nothing changes.
                pass
            elif prev_up is False:
                # Down→Up transition.
                # If we previously sent a down-alert, send "back online" now.
                # If the down was a brief blip (alert never sent), suppress
                # the recovery alert too — user never saw "down."
                if rec["down_alert_sent"]:
                    emit_up = True
                rec["is_up"] = True
                rec["first_down_ts"] = None
                rec["down_alert_sent"] = False
            else:
                # prev_up is None — first observation, source is up.
                rec["is_up"] = True
                # No alert: starting state is "up" → no anomaly to report.
        else:
            # Currently reported as down
            if prev_up is False:
                # Already down — check if we've crossed the alert threshold.
                if (
                    not rec["down_alert_sent"]
                    and rec["first_down_ts"] is not None
                    and (now - rec["first_down_ts"]) >= DOWN_ALERT_AFTER_SEC
                ):
                    emit_down = True
                    rec["down_alert_sent"] = True
                # else: still within debounce window OR alert already sent.
            elif prev_up is True:
                # Up→Down transition. Mark the time but DON'T alert yet
                # (debounce window opens now).
                rec["is_up"] = False
                rec["first_down_ts"] = now
                rec["down_alert_sent"] = False
            else:
                # prev_up is None — first observation, source is down. Treat
                # as start of debounce window; alert only if it stays down.
                rec["is_up"] = False
                rec["first_down_ts"] = now
                rec["down_alert_sent"] = False

    # I/O outside the lock
    if emit_up:
        _send(f"✅ {name} back online — primary data source restored.")
    elif emit_down:
        _send(
            f"⚠️ {name} unreachable for >{int(DOWN_ALERT_AFTER_SEC)}s — "
            f"using fallback data source. Investigate if this persists."
        )


def current(source: str) -> Optional[bool]:
    """Query last known state of a source (True/False/None=never observed)."""
    with _lock:
        rec = _state.get(source.strip().lower())
        return rec["is_up"] if rec else None


def snapshot() -> dict[str, Optional[bool]]:
    """Return a copy of the full state dict (for dashboards/diagnostics)."""
    with _lock:
        return {k: v["is_up"] for k, v in _state.items()}
