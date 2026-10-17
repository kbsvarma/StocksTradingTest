"""One-shot reconciliation CLI — diagnostic only, no side effects.

Usage:
    python -m webull_bot.reconcile

Prints state.json open_position alongside Webull's actual SPXW/SPX/NDXP
holdings, then a drift verdict. Read-only. Safe to run at any time.

This is the manual companion to reconcile_watchdog_v2.py — same checks,
but human-readable and on-demand. Use during incidents / before any
manual intervention to know the actual ground truth.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from webull_bot.client import build_trade_client
from webull_bot.execution_v2 import PositionSnapshotter
from webull_bot.reconcile_watchdog_v2 import _is_active


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except Exception as exc:
        print(f"  ⚠️  Could not parse state.json: {exc}")
        return {}


def _fmt_pos(pos: dict | None) -> str:
    if not pos:
        return "(none)"
    short = pos.get("short_strike") or pos.get("short")
    long_ = pos.get("long_strike") or pos.get("long")
    # Try all possible credit field names from different code paths
    credit = (pos.get("entry_credit") or pos.get("net_credit")
              or pos.get("fill_price") or pos.get("credit"))
    stop = pos.get("stop_price")
    return f"{short}/{long_}P  credit=${credit}  stop=${stop}"


def main() -> int:
    cfg_path = Path(__file__).parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    repo_root = Path(__file__).parent.parent
    sf = cfg.get("state_file", "")
    state_path = Path(sf) if Path(sf).is_absolute() else repo_root / sf

    print("=" * 60)
    print("WEBULL BOT — ON-DEMAND RECONCILE")
    print("=" * 60)

    # 1) STATE.JSON
    print("\n=== STATE.JSON ===")
    print(f"  path: {state_path}")
    state = _load_state(state_path)
    open_pos = state.get("open_position")
    pending = state.get("pending_order")
    print(f"  open_position: {_fmt_pos(open_pos)}")
    print(f"  pending_order: {'yes' if pending else 'no'}"
          + (f"  (ts={pending.get('ts')})" if pending else ""))

    # 2) BROKER HOLDINGS
    print("\n=== WEBULL HOLDINGS (live API) ===")
    try:
        trade = build_trade_client()
        snap = PositionSnapshotter(trade, cfg["account_id"])
        all_holdings = list(snap.snapshot().values())
    except Exception as exc:
        print(f"  ⚠️  broker query failed: {exc}")
        return 2

    active = [h for h in all_holdings if _is_active(h)]
    filtered = len(all_holdings) - len(active)
    if filtered:
        print(f"  (filtered {filtered} expired/worthless remnants)")
    if not active:
        print("  (no active option positions)")
    for h in active:
        sym = h.get("symbol") or "?"
        iid = h.get("instrument_id")
        qty = h.get("qty")
        cost = h.get("unit_cost")
        last = h.get("last_price")
        exp = h.get("option_expire_date") or h.get("expire_date") or "?"
        strike = h.get("option_strike_price") or h.get("strike_price") or "?"
        side = "SHORT" if str(qty).startswith("-") else "LONG "
        print(f"  {sym} {side} qty={qty:>3} strike={strike:>7} "
              f"cost=${cost:<6} last=${last:<6} exp={exp} iid={iid}")

    # 3) DRIFT VERDICT
    print("\n=== DRIFT ANALYSIS ===")
    state_count = 0
    if open_pos:
        # Each spread is 2 legs (short + long)
        state_count = 2 * int(open_pos.get("qty", 1) or 1)
    broker_count = len(active)

    if state_count == broker_count == 0:
        print("  ✓ CLEAN: state has no position, broker has no active position")
        rc = 0
    elif state_count == broker_count:
        print(f"  ✓ MATCH: state and broker both show {broker_count} active leg(s)")
        rc = 0
    elif state_count == 0 and broker_count > 0:
        print(f"  🚨 DRIFT: state.json shows NO position; broker has {broker_count} active leg(s)")
        print(f"      → likely ORPHAN. Bot has no SL monitor on these.")
        print(f"      → check watchdog status: /tmp/reconcile_watchdog_status.json")
        rc = 1
    elif state_count > 0 and broker_count == 0:
        print(f"  🚨 DRIFT: state.json says position open ({state_count} legs); broker has none")
        print(f"      → likely PHANTOM (already closed/expired). state.json needs cleanup.")
        rc = 1
    else:
        print(f"  🚨 DRIFT: state.json shows {state_count} legs; broker shows {broker_count}")
        rc = 1

    # 4) Pending freshness
    if pending:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            ts = datetime.fromisoformat(pending["ts"])
            age = (datetime.now(ZoneInfo("America/New_York")) - ts).total_seconds()
            print(f"\n  pending_order age: {age:.0f}s "
                  + ("(fresh — bot mid-place)" if age < 90 else "(STALE — bot may have crashed mid-place)"))
        except Exception:
            pass

    print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
