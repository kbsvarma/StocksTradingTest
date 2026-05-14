"""Data-source health tracker — fires Telegram alerts on IBKR↔yfinance transitions.

Each call site that fetches market data (spot, VIX, option chain, spread mark)
calls `report(source, up=...)` after every attempt. This module remembers the
last known state per source name and fires a Telegram alert ONLY when the
state transitions. No spam — one alert per up→down or down→up flip.

Usage:
    from webull_bot import data_source_health as dsh
    dsh.report("ibkr", up=True)   # call after a successful IBKR fetch
    dsh.report("ibkr", up=False)  # call after a failed IBKR fetch (we fell back)

Source names are free-form strings. Convention: lowercase, e.g. "ibkr". The
displayed alert message uppercases the name.

Telegram alerts use `webull_bot.alerts.send_alert` which is fire-and-forget
(daemon thread, swallows exceptions) so this module can never block or raise
into the trading path.

Thread-safety: a single lock protects the state dict. Calls from the monitor
thread + main thread + ad-hoc CLI thread are safe.
"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
# source_name -> True (up) | False (down) | None (never observed)
_state: dict[str, Optional[bool]] = {}


def _send(text: str) -> None:
    """Best-effort Telegram dispatch. Never raises."""
    try:
        from webull_bot.alerts import send_alert
        send_alert(text)
    except Exception:
        pass


def report(source: str, *, up: bool) -> None:
    """Record an observation. If state transitioned, fire one alert.

    `source` — short identifier, e.g. "ibkr". Case-insensitive for state key.
    `up` — True if the data fetch succeeded, False if it failed (fell back).
    """
    key = source.strip().lower()
    if not key:
        return
    transitioned = False
    prev: Optional[bool] = None
    with _lock:
        prev = _state.get(key)
        if prev != up:
            _state[key] = up
            transitioned = True
    if not transitioned:
        return
    name = key.upper()
    if up:
        if prev is None:
            # First observation that succeeded — no alert (was unknown, not a recovery).
            return
        _send(f"✅ {name} back online — primary data source restored.")
    else:
        if prev is None:
            _send(f"⚠️ {name} not reachable — using fallback data source.")
        else:
            _send(f"⚠️ {name} just went down — falling back to alternate data source.")


def current(source: str) -> Optional[bool]:
    """Query last known state of a source (True/False/None=never observed)."""
    with _lock:
        return _state.get(source.strip().lower())


def snapshot() -> dict[str, Optional[bool]]:
    """Return a copy of the full state dict (for dashboards/diagnostics)."""
    with _lock:
        return dict(_state)
