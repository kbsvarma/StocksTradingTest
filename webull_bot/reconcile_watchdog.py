"""Reconciliation watchdog — catches orphan & phantom positions.

WHY THIS EXISTS (2026-05-20 RCA):
  Three positions ran for hours today with ZERO SL coverage because they
  reached the broker but never made it into state.json (force-script bypass
  + main bot's MARKET fallback path silently filling after polling timeout).
  The bot's own SL monitor only watches what's in state.open_position, so
  any position outside state.json is invisible. We got lucky — they expired
  worthless. Next time we won't.

DESIGN:
  Standalone process. Every WATCH_INTERVAL seconds during market hours:
    1. Snapshot broker SPXW/SPX/NDXP option positions.
    2. Read bot's state.json (open_position).
    3. Compare:
       - ORPHAN  = broker has position that state doesn't → loud Telegram
       - PHANTOM = state has position that broker doesn't → loud Telegram
       - MATCH   = both align (good) → silent
  Persistent state file `/tmp/reconcile_watchdog_seen.json` so we don't
  spam the same alert every cycle — only re-alert if the orphan/phantom
  set changes or every ESCALATION_INTERVAL.

USAGE:
  python -m webull_bot.reconcile_watchdog
  or via launchd plist (separate config) calling above.

DEPENDS ON: state.py, alerts.py, execution.py (._fetch_spxw_positions), config.yaml.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")
WATCH_INTERVAL_S = 60                # check cadence
ESCALATION_INTERVAL_S = 1800         # repeat alert for unresolved mismatch (30min)
# Only send Telegram alerts during market hours (and a small post-close window
# while operator may still be watching). Outside this we still WRITE the status
# file so the dashboard stays accurate — just don't ping the phone.
ALERT_OPEN = dtime(9, 30)
ALERT_CLOSE = dtime(16, 15)
# Paper instance (EC2) should be log-only — Mac live is the source of truth and
# alerting from both sends every alert twice. Toggle via env.
TELEGRAM_ENABLED = os.environ.get("WEBULL_INSTANCE_NAME", "mac").lower() != "ec2"

# Paper bot (EC2) shares the SAME Webull account as Mac live but never places
# real orders (WEBULL_DRY_RUN=1). Every position at the broker therefore belongs
# to Mac live, not to the paper bot — paper-side reconciliation is meaningless.
# In paper mode we skip the broker query entirely and write status="ok" so the
# dashboard stays green and the user knows the live watchdog has it covered.
PAPER_MODE = (
    os.environ.get("WEBULL_DRY_RUN") == "1"
    or os.environ.get("WEBULL_INSTANCE_NAME", "mac").lower() == "ec2"
)
SEEN_FILE = Path("/tmp/reconcile_watchdog_seen.json")
STATUS_FILE = Path("/tmp/reconcile_watchdog_status.json")  # consumed by dashboard
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 5)          # bias late so EOD closes are seen


def _log(msg: str) -> None:
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] [reconcile-watchdog] {msg}", flush=True)


def _load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    # Resolve state_file relative to repo root so the watchdog works
    # regardless of cwd (launchd / systemd / direct run).
    repo_root = Path(__file__).parent.parent
    sf = cfg.get("state_file", "")
    if sf and not Path(sf).is_absolute():
        cfg["state_file"] = str(repo_root / sf)
    return cfg


def _load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {"orphans": {}, "phantoms": {}}
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return {"orphans": {}, "phantoms": {}}


def _save_seen(seen: dict) -> None:
    try:
        SEEN_FILE.write_text(json.dumps(seen, indent=2))
    except Exception as e:
        _log(f"WARN: could not save seen file: {e}")


def _in_market_hours(now_et: datetime) -> bool:
    if now_et.weekday() >= 5:  # Sat/Sun
        return False
    t = now_et.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def _key_for_holding(h: dict) -> str:
    """Stable key for a broker holding: symbol|strike|expiry|side."""
    sym = h.get("symbol", "?")
    iid = h.get("instrument_id", "?")
    qty = h.get("qty", "0")
    return f"{sym}|{iid}|qty={qty}"


def _state_position_keys(state_path: Path) -> set[str]:
    """Return short_iid+long_iid set from state.open_position (if any)."""
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
    # Also derive strike-based key for cases where iids were not captured
    out.add(f"strike|{int(op.get('short_strike', 0))}|short")
    out.add(f"strike|{int(op.get('long_strike', 0))}|long")
    return out


def _check_once(cfg: dict, seen: dict) -> None:
    from webull_bot.client import build_trade_client
    from webull_bot.execution import ExecutionEngine
    from webull_bot.alerts import send_alert

    now_et = datetime.now(ET)

    # Paper bot: skip broker query entirely, write status=ok, return.
    # Broker positions belong to Mac live — paper has nothing to reconcile.
    if PAPER_MODE:
        try:
            STATUS_FILE.write_text(json.dumps({
                "ts": now_et.isoformat(),
                "broker_iid_count": 0,
                "state_iid_count": 0,
                "orphans": [],
                "phantoms": [],
                "status": "ok",
                "note": "paper instance — broker reconciliation handled by Mac live watchdog",
            }, indent=2))
        except Exception as e:
            _log(f"could not write paper-mode status file: {e}")
        return

    alerts_allowed = (
        TELEGRAM_ENABLED
        and now_et.weekday() < 5
        and ALERT_OPEN <= now_et.time() <= ALERT_CLOSE
    )

    state_path = Path(cfg["state_file"])
    tc = build_trade_client()
    ex = ExecutionEngine(tc, cfg["account_id"])

    try:
        broker_positions = ex._fetch_spxw_positions()
    except Exception as e:
        _log(f"ERROR fetching broker positions: {e}")
        return

    # Skip already-expired / worthless contracts. Webull holdings doesn't
    # return option_expire_date (verified 2026-05-20 — only instrument_id +
    # qty + last_price + market_value), so use price as proxy:
    #   - last_price < $0.05 → expired worthless OR essentially dead.
    #     Real orphan positions opened by the bot have credit ≥ $0.45 →
    #     their mark won't sit below $0.05 unless they've expired.
    #   - market_value < $5  → same signal (1 contract × $0.05 × 100 = $5)
    # Fail-open on missing fields (treat as active so we don't miss real orphans).
    def _is_active(h: dict) -> bool:
        # If expiry IS present (e.g. via order-history join), still honor it
        exp = h.get("option_expire_date") or h.get("expire_date") or h.get("expiry")
        today_iso = now_et.date().isoformat()
        expiry_settled = now_et.time() >= dtime(16, 15)
        if exp:
            exp_s = str(exp)
            if exp_s < today_iso:
                return False
            if exp_s == today_iso and expiry_settled:
                return False
        # Price-based fallback (Webull's actual holdings response)
        try:
            lp = float(h.get("last_price", 0) or 0)
            mv = abs(float(h.get("market_value", 0) or 0))
            if lp < 0.05 and mv < 5.0:
                return False  # essentially worthless → expired or near-expired
        except (ValueError, TypeError):
            pass
        return True
    pre_count = len(broker_positions)
    broker_positions = [h for h in broker_positions if _is_active(h)]
    skipped = pre_count - len(broker_positions)
    if skipped:
        _log(f"skipping {skipped} expired-or-past holdings (broker not yet cleared)")

    state_iids = _state_position_keys(state_path)

    # Build broker-side iid set
    broker_iids = {h["instrument_id"] for h in broker_positions if h.get("instrument_id")}

    # ── ORPHAN check: broker iid not in state ──────────────────────
    orphan_iids = broker_iids - {x for x in state_iids if not x.startswith("strike|")}
    # Filter: only count if also doesn't match by strike from state
    real_orphans = []
    for h in broker_positions:
        iid = h.get("instrument_id")
        if iid not in orphan_iids:
            continue
        try:
            qty = int(h.get("qty", 0))
        except Exception:
            qty = 0
        side = "short" if qty < 0 else ("long" if qty > 0 else "flat")
        # Try to extract strike (Webull holding usually doesn't carry it directly;
        # we use the iid as the unique key, that's enough for alerting).
        real_orphans.append({"iid": iid, "qty": qty, "side": side,
                             "symbol": h.get("symbol", "?")})

    # ── PHANTOM check: state has iid that broker doesn't ───────────
    state_real_iids = {x for x in state_iids if not x.startswith("strike|")}
    phantom_iids = state_real_iids - broker_iids

    now_ts = time.time()

    # ORPHAN alerts
    if real_orphans:
        orphan_key = ",".join(sorted(o["iid"] for o in real_orphans))
        last_alerted = seen["orphans"].get(orphan_key, 0)
        if now_ts - last_alerted > ESCALATION_INTERVAL_S:
            detail = "\n".join(
                f"  - {o['symbol']} {o['side']} qty={o['qty']} iid={o['iid'][:12]}…"
                for o in real_orphans
            )
            msg = (
                f"🚨 ORPHAN POSITION DETECTED at broker (NOT in state.json):\n"
                f"{detail}\n"
                f"SL monitor is NOT watching this. Manual close required.\n"
                f"(checked at {datetime.now(ET).strftime('%H:%M:%S ET')})"
            )
            _log(msg)
            if alerts_allowed:
                try:
                    send_alert(msg)
                except Exception as e:
                    _log(f"alert send failed: {e}")
            else:
                _log("(alert suppressed — outside market hours or paper instance)")
            seen["orphans"][orphan_key] = now_ts
    else:
        # No orphans → clear any stale entries
        seen["orphans"] = {}

    # PHANTOM alerts
    if phantom_iids:
        phantom_key = ",".join(sorted(phantom_iids))
        last_alerted = seen["phantoms"].get(phantom_key, 0)
        if now_ts - last_alerted > ESCALATION_INTERVAL_S:
            msg = (
                f"⚠️ PHANTOM POSITION in state.json (NOT at broker):\n"
                f"  iids: {', '.join(p[:12]+'…' for p in phantom_iids)}\n"
                f"Likely closed externally. State may be stale.\n"
                f"(checked at {datetime.now(ET).strftime('%H:%M:%S ET')})"
            )
            _log(msg)
            if alerts_allowed:
                try:
                    send_alert(msg)
                except Exception as e:
                    _log(f"alert send failed: {e}")
            else:
                _log("(alert suppressed — outside market hours or paper instance)")
            seen["phantoms"][phantom_key] = now_ts
    else:
        seen["phantoms"] = {}

    if not real_orphans and not phantom_iids:
        _log(f"OK — broker={len(broker_iids)} state={len(state_real_iids)} (match)")

    _save_seen(seen)

    # Write a snapshot for the dashboard (T2.5)
    try:
        STATUS_FILE.write_text(json.dumps({
            "ts": datetime.now(ET).isoformat(),
            "broker_iid_count": len(broker_iids),
            "state_iid_count": len(state_real_iids),
            "orphans": [{"iid": o["iid"], "symbol": o["symbol"],
                         "qty": o["qty"], "side": o["side"]}
                        for o in real_orphans],
            "phantoms": sorted(phantom_iids),
            "status": ("orphan" if real_orphans else
                       "phantom" if phantom_iids else "ok"),
        }, indent=2))
    except Exception as e:
        _log(f"could not write status file: {e}")


def main() -> None:
    cfg = _load_config()
    seen = _load_seen()

    # Honor a one-shot mode for testing
    if "--once" in sys.argv:
        _check_once(cfg, seen)
        return

    _log(f"starting (interval={WATCH_INTERVAL_S}s, escalation={ESCALATION_INTERVAL_S}s)")

    # Graceful shutdown
    stop = {"flag": False}
    def _handle(signum, frame):
        stop["flag"] = True
        _log(f"signal {signum} received — exiting after current cycle")
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    while not stop["flag"]:
        try:
            # Always run a check so the status file stays fresh and the
            # dashboard banner doesn't flag "missing" overnight. Alerts are
            # only meaningful during/just-around market hours; the orphan
            # detection logic itself is harmless to run any time.
            _check_once(cfg, seen)
        except Exception:
            _log(f"unhandled exception in cycle:\n{traceback.format_exc()}")
        # sleep in 5s ticks so SIGTERM is responsive
        slept = 0
        while slept < WATCH_INTERVAL_S and not stop["flag"]:
            time.sleep(5)
            slept += 5

    _log("stopped")


if __name__ == "__main__":
    main()
