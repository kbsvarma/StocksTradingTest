"""Reconcile watchdog v2 — single-cycle / cron-style.

NOT YET DEPLOYED. Development only.

Why this exists:
  2026-05-21: original reconcile_watchdog.py crashed at 01:50 ET with "Too many
  open files" after running 8+ hours. Root cause: each call to build_trade_client()
  opens a new HTTP connection pool + reads endpoints.json; nothing closes these
  on object lifetime, so FDs accumulated past macOS limit (256).

What this does differently:
  - Runs ONE check then exits cleanly (process death frees all resources)
  - Designed to be launched every 30 seconds by launchd StartInterval
  - No long-running state to leak

Compatible with existing reconcile_watchdog.py status file format so dashboard
doesn't change. Drop-in replacement once tested.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")
STATUS_FILE = Path("/tmp/reconcile_watchdog_status.json")
SEEN_FILE = Path("/tmp/reconcile_watchdog_seen.json")
URGENT_FILE = Path("/tmp/webull-alerts-urgent.jsonl")
EVENT_QUEUE = Path("/tmp/event_queue.jsonl")
LAST_RUN_FILE = Path("/tmp/reconcile_watchdog_last_run.json")
ESCALATION_INTERVAL_S = 1800  # 30 min — don't spam same orphan
ALERT_OPEN = dtime(9, 30)
ALERT_CLOSE = dtime(16, 15)
PENDING_FRESH_SECONDS = 90  # if pending_order < 90s old, skip alerting (bot mid-place)

# 2026-05-22 clock-gating: skip work outside active periods
ACTIVE_PERIODS = [
    # (start, end, min_seconds_between_runs)
    (dtime(8, 0),  dtime(9, 30),  300),    # pre-market: every 5 min
    (dtime(9, 30), dtime(16, 0),  60),     # market hours: every 60s
    (dtime(16, 0), dtime(16, 30), 120),    # settlement: every 2 min
]

TELEGRAM_ENABLED = os.environ.get("WEBULL_INSTANCE_NAME", "mac").lower() != "ec2"
PAPER_MODE = (
    os.environ.get("WEBULL_DRY_RUN") == "1"
    or os.environ.get("WEBULL_INSTANCE_NAME", "mac").lower() == "ec2"
)


def _log(msg: str) -> None:
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] [recon-v2] {msg}", flush=True)


def _should_run_now(now_et=None) -> tuple[bool, str]:
    """Clock-gating: should watchdog actually do work this invocation?

    Returns (run_now, reason). False means exit immediately.
    Uses LAST_RUN_FILE to enforce minimum cadence per active period.
    Outside market hours / weekends → False.

    now_et parameter exists for testing — defaults to current ET time.
    """
    if now_et is None:
        now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Sat=5, Sun=6
        return (False, "weekend")
    now_t = now_et.time()
    active_window = None
    for start, end, min_interval in ACTIVE_PERIODS:
        if start <= now_t < end:
            active_window = (start, end, min_interval)
            break
    if active_window is None:
        return (False, f"outside active windows (now={now_t.strftime('%H:%M')} ET)")

    # Check throttle vs last run
    min_interval = active_window[2]
    try:
        if LAST_RUN_FILE.exists():
            last_run = json.loads(LAST_RUN_FILE.read_text())
            last_ts = datetime.fromisoformat(last_run["ts"])
            elapsed = (now_et - last_ts).total_seconds()
            if elapsed < min_interval:
                return (False, f"throttled (last run {elapsed:.0f}s ago, min {min_interval}s)")
    except Exception:
        pass  # No last-run file or unparseable — proceed
    return (True, f"in window {active_window[0].strftime('%H:%M')}-{active_window[1].strftime('%H:%M')}, interval={min_interval}s")


def _mark_run() -> None:
    """Record this run's timestamp for throttle calculation."""
    try:
        LAST_RUN_FILE.write_text(json.dumps({"ts": datetime.now(ET).isoformat()}))
    except Exception:
        pass


def _queue_event(severity: str, source: str, subject: str, body: str,
                 dedup_key: str = "") -> None:
    """Append event to the shared event queue. Atomic append (POSIX guarantees
    for writes <4KB). Telegram service + bot read from this file."""
    import uuid
    event = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(ET).isoformat(),
        "severity": severity,
        "source": source,
        "subject": subject,
        "body": body,
        "dedup_key": dedup_key,
    }
    try:
        with EVENT_QUEUE.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as exc:
        _log(f"queue_event failed: {exc}")


def _load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {"orphans": {}, "phantoms": {}}
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return {"orphans": {}, "phantoms": {}}


def _save_seen(seen: dict) -> None:
    try:
        # Atomic via temp + rename
        tmp = SEEN_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(seen, indent=2))
        tmp.rename(SEEN_FILE)
    except Exception as exc:
        _log(f"could not save seen file: {exc}")


def _write_status(payload: dict) -> None:
    try:
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(STATUS_FILE)
    except Exception as exc:
        _log(f"could not write status file: {exc}")


def _write_urgent(msg: str) -> None:
    """Backup alert path — written to file even if Telegram fails."""
    try:
        with URGENT_FILE.open("a") as f:
            f.write(json.dumps({
                "ts": datetime.now(ET).isoformat(),
                "source": "reconcile-watchdog-v2",
                "msg": msg,
            }) + "\n")
    except Exception:
        pass


def _load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    repo_root = Path(__file__).parent.parent
    sf = cfg.get("state_file", "")
    if sf and not Path(sf).is_absolute():
        cfg["state_file"] = str(repo_root / sf)
    return cfg


def _is_active(holding: dict) -> bool:
    """True if holding is a tradeable active position (not expired remnant)."""
    # Price-based filter (Webull positions don't carry expiry directly)
    try:
        lp = float(holding.get("last_price", 0) or 0)
        mv = abs(float(holding.get("market_value", 0) or 0))
        if lp < 0.05 and mv < 5.0:
            return False  # essentially worthless
    except (ValueError, TypeError):
        pass
    # If expiry IS provided
    exp = holding.get("option_expire_date") or holding.get("expire_date") or holding.get("expiry")
    if exp:
        now_et = datetime.now(ET)
        today_iso = now_et.date().isoformat()
        expiry_settled = now_et.time() >= dtime(16, 15)
        exp_s = str(exp)
        if exp_s < today_iso:
            return False
        if exp_s == today_iso and expiry_settled:
            return False
    return True


def _check_pending_fresh(state: dict) -> bool:
    """If bot is in the middle of place_spread (pending_order fresh), skip checks."""
    pending = state.get("pending_order")
    if not pending:
        return False
    try:
        pending_ts = datetime.fromisoformat(pending["ts"])
        age = (datetime.now(ET) - pending_ts).total_seconds()
        return age < PENDING_FRESH_SECONDS
    except (KeyError, ValueError, TypeError):
        return False


def _state_position_keys(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    try:
        st = json.loads(state_path.read_text())
    except Exception:
        return set()
    op = st.get("open_position")
    if not op:
        return set()
    out = set()
    if op.get("short_iid"):
        out.add(op["short_iid"])
    if op.get("long_iid"):
        out.add(op["long_iid"])
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _strikes_match_state(op: dict, combo_strikes: list, active_leg_count: int) -> bool:
    """True if the monitored open_position is CONFIRMED present at the broker by
    combo strikes, so the per-leg-iid orphan/phantom flags are false positives
    and may be suppressed.

    Why this exists (2026-06-04): the bot stores positions by combo/strike, NOT
    per-leg instrument_id — fresh entries leave short_iid/long_iid EMPTY and
    combo-recoveries store the combo position_id in both. The watchdog's broker
    scan returns PER-LEG iids, so raw iid-diffing false-flags a fully-monitored
    position as both an orphan and a phantom. Strikes are representation-
    independent and are what the bot's own reconcile uses.

    SAFETY — this only ever SUPPRESSES, and only when ALL hold:
      * state has both strikes,
      * exactly 2 active broker legs (no extras — extras could be a real orphan),
      * a broker VERTICAL combo matches the state strikes exactly.
    Any uncertainty (missing strikes, extra legs, no combo data, no match)
    returns False -> the original alert still fires. A REAL orphan (different
    strikes, or extra legs) is never suppressed.
    """
    s = _f(op.get("short_strike"))
    l = _f(op.get("long_strike"))
    if s is None or l is None:
        return False
    if active_leg_count != 2:
        return False
    for cs, cl in combo_strikes:
        if abs(cs - s) < 0.01 and abs(cl - l) < 0.01:
            return True
    return False


def _broker_combo_strikes(ex, account_id: str) -> list:
    """[(short_strike, long_strike), ...] for active VERTICAL combos at the
    broker. [] on any error -> caller keeps the original (alerting) behavior."""
    out = []
    try:
        from webull_bot.safe_api import safe_call
        resp = safe_call(ex.trade.account_v2.get_account_position, account_id=account_id)
        if resp is None:
            return out
        body = resp.json()
        positions = body if isinstance(body, list) else body.get("data", [])
        for p in positions:
            if p.get("option_strategy") != "VERTICAL":
                continue
            ks = sorted(
                (float(leg.get("option_exercise_price", 0) or 0)
                 for leg in p.get("legs", [])),
                reverse=True,
            )
            if len(ks) == 2 and ks[0] > 0:
                out.append((ks[0], ks[1]))
    except Exception:
        pass
    return out


def main():
    """Single-cycle check then exit. Launchd respawns us every 30s.

    Clock-gating (2026-05-22): only does real work during active periods.
    Outside those, exits immediately. Drops API load by ~85%.
    """
    # Clock-gating: exit early if outside active windows or throttled
    run_now, reason = _should_run_now()
    if not run_now:
        _log(f"skip — {reason}")
        return 0
    _mark_run()

    try:
        now_et = datetime.now(ET)
        cfg = _load_config()

        # PAPER MODE: just write ok, don't bother broker
        if PAPER_MODE:
            _write_status({
                "ts": now_et.isoformat(),
                "status": "ok",
                "note": "paper instance — broker reconciliation handled by Mac live",
                "broker_iid_count": 0,
                "state_iid_count": 0,
                "orphans": [],
                "phantoms": [],
            })
            return 0

        # Load state
        state_path = Path(cfg["state_file"])
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                _log("could not parse state.json — skipping cycle")
                return 1
        else:
            state = {}

        # If bot is actively placing (fresh pending), skip — bot will record itself
        if _check_pending_fresh(state):
            _write_status({
                "ts": now_et.isoformat(),
                "status": "ok",
                "note": "skipped — bot has fresh pending_order (place in progress)",
                "broker_iid_count": -1,
                "state_iid_count": -1,
                "orphans": [],
                "phantoms": [],
            })
            return 0

        # Query broker
        from webull_bot.client import build_trade_client
        from webull_bot.execution import ExecutionEngine

        try:
            tc = build_trade_client()
            ex = ExecutionEngine(tc, cfg["account_id"])
            broker_positions = ex._fetch_spxw_positions()
        except Exception as exc:
            _log(f"broker query failed: {exc}")
            _write_status({
                "ts": now_et.isoformat(),
                "status": "broker_query_failed",
                "note": str(exc)[:200],
            })
            return 2

        # Filter to active positions
        active = [h for h in broker_positions if _is_active(h)]
        skipped = len(broker_positions) - len(active)
        if skipped:
            _log(f"filtered {skipped} expired/worthless holdings")

        # Build sets
        broker_iids = {h["instrument_id"] for h in active if h.get("instrument_id")}
        state_iids = _state_position_keys(state_path)

        orphans_iids = broker_iids - state_iids
        phantoms_iids = state_iids - broker_iids

        orphans_details = [
            {"iid": h["instrument_id"], "symbol": h.get("symbol", "?"),
             "qty": int(h.get("qty", 0) or 0),
             "side": "short" if int(h.get("qty", 0) or 0) < 0 else "long"}
            for h in active if h.get("instrument_id") in orphans_iids
        ]

        # ── Combo+strike confirmation (2026-06-04) ─────────────────────────
        # The bot stores positions by combo/strike, NOT per-leg iid (fresh
        # entries leave iids empty; combo-recoveries store the combo id), so raw
        # per-leg-iid diffing false-flags a fully-monitored position as orphan +
        # phantom. If the state position's strikes match a broker VERTICAL combo
        # and there are exactly 2 active legs, that's our monitored spread —
        # suppress the false positive. Real orphans (different strikes / extra
        # legs / no combo match) are NOT suppressed. See _strikes_match_state.
        if orphans_details or phantoms_iids:
            op = state.get("open_position") or {}
            if op:
                combo_strikes = _broker_combo_strikes(ex, cfg["account_id"])
                if _strikes_match_state(op, combo_strikes, len(active)):
                    _log(
                        f"combo strikes {int(_f(op.get('short_strike')) or 0)}/"
                        f"{int(_f(op.get('long_strike')) or 0)} match state — "
                        f"monitored position confirmed; suppressing iid false positive"
                    )
                    orphans_details = []
                    phantoms_iids = set()

        # Decide alert eligibility
        alerts_allowed = (
            TELEGRAM_ENABLED
            and now_et.weekday() < 5
            and ALERT_OPEN <= now_et.time() <= ALERT_CLOSE
        )

        now_ts = now_et.timestamp()
        seen = _load_seen()

        # ─── ORPHAN HANDLING — PURE OBSERVER MODE (2026-05-22) ─────────────
        # Watchdog NEVER modifies state.json. It only:
        #   1. Logs the orphan detection
        #   2. Writes to urgent file (audit log)
        #   3. Appends event to event queue (for telegram service + bot to see)
        #   4. Sends direct Telegram alert (back-compat until telegram service ships)
        # Bot's queue handler is responsible for any auto-recovery action.
        if orphans_details:
            orphan_key = ",".join(sorted(o["iid"] for o in orphans_details))
            last_alerted = seen["orphans"].get(orphan_key, 0)
            should_alert = (now_ts - last_alerted) > ESCALATION_INTERVAL_S

            detail = "\n".join(
                f"  - {o['symbol']} {o['side']} qty={o['qty']} iid={o['iid'][:12]}…"
                for o in orphans_details
            )
            msg = (
                f"🚨 ORPHAN POSITION DETECTED at broker (NOT in state.json):\n"
                f"{detail}\n"
                f"SL monitor is NOT watching this. Bot's queue handler will "
                f"attempt auto-recovery on its next tick.\n"
                f"(checked at {now_et.strftime('%H:%M:%S ET')})"
            )
            _log(msg)
            _write_urgent(msg)

            # Append to event queue (for telegram service + bot)
            try:
                _queue_event(
                    severity="critical",
                    source="reconcile_watchdog_v2",
                    subject="ORPHAN POSITION DETECTED",
                    body=msg,
                    dedup_key=f"orphan:{orphan_key}",
                )
            except Exception as exc:
                _log(f"event queue append failed: {exc}")

            # Direct Telegram alert (back-compat path — will be removed when telegram service is stable)
            if should_alert and alerts_allowed:
                try:
                    from webull_bot.alerts import send_alert
                    send_alert(msg)
                    seen["orphans"][orphan_key] = now_ts
                except Exception as e:
                    _log(f"telegram send failed: {e}")
            elif not alerts_allowed:
                _log("(telegram suppressed — outside market hours or paper instance)")
            else:
                _log(f"(suppressed — last alerted {int(now_ts - last_alerted)}s ago)")
        else:
            seen["orphans"] = {}

        # Phantom handling (symmetric, less critical)
        if phantoms_iids:
            phantom_key = ",".join(sorted(phantoms_iids))
            last_alerted = seen["phantoms"].get(phantom_key, 0)
            if (now_ts - last_alerted) > ESCALATION_INTERVAL_S and alerts_allowed:
                msg = (f"⚠️ PHANTOM POSITION in state.json (NOT at broker):\n"
                       f"  iids: {', '.join(p[:12]+'…' for p in phantoms_iids)}")
                _log(msg)
                _write_urgent(msg)
                try:
                    from webull_bot.alerts import send_alert
                    send_alert(msg)
                except Exception:
                    pass
                seen["phantoms"][phantom_key] = now_ts
        else:
            seen["phantoms"] = {}

        _save_seen(seen)

        # Status snapshot for dashboard
        status_val = "orphan" if orphans_details else "phantom" if phantoms_iids else "ok"
        _write_status({
            "ts": now_et.isoformat(),
            "status": status_val,
            "broker_iid_count": len(broker_iids),
            "state_iid_count": len(state_iids),
            "orphans": orphans_details,
            "phantoms": sorted(phantoms_iids),
        })
        if not orphans_details and not phantoms_iids:
            _log(f"OK — broker={len(broker_iids)} state={len(state_iids)} (match)")
        return 0

    except Exception:
        _log(f"unhandled exception:\n{traceback.format_exc()}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
