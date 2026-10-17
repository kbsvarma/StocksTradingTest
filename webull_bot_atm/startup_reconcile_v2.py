"""Bot startup reconciliation — auto-recover from orphans/phantoms.

NOT YET DEPLOYED. Development only.

Runs at bot startup BEFORE main loop. Compares:
  - state.json (open_position, pending_order)
  - broker positions (live query)

Resolves discrepancies per the design matrix:

| State           | Broker      | Action                                          |
|-----------------|-------------|-------------------------------------------------|
| empty           | empty       | Normal start                                    |
| open_position   | matching    | Normal resume                                   |
| open_position   | different   | ALERT + halt — manual review                    |
| open_position   | empty       | Treat as closed externally; clear state         |
| empty           | has pos     | Check pending; if matches, auto-promote; else ALERT + halt |
| pending stale + match | yes   | Auto-promote pending → open                     |
| pending stale + no match |     | Clear pending (place_order failed silently)     |
| pending fresh + match    |     | Trust the fill; promote                         |
| pending fresh + no match |     | Don't touch — original bot still running place |

Auto-recovery uses BROKER'S actual position data (not pending's intended values)
for the new OpenPosition record.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)
PENDING_STALE_SECONDS = 90


@dataclass
class ReconcileDecision:
    action: str       # one of: NORMAL_START, NORMAL_RESUME, HALT, PROMOTE_PENDING, CLEAR_PENDING, CLEAR_STATE, DEFER_TO_BOT
    reason: str
    new_open_position: Optional[dict] = None  # if PROMOTE_PENDING, the constructed position
    alert_message: Optional[str] = None       # if needs telegram


def _holding_strike(h: dict, strike_resolver=None) -> Optional[float]:
    """Extract strike from holding, with fallback to resolver."""
    for key in ("strike_price", "strike", "strikePrice"):
        v = h.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
    if strike_resolver:
        try:
            return strike_resolver(h.get("instrument_id"))
        except Exception:
            return None
    return None


def _build_open_position_from_broker(
    broker_holdings: list[dict],
    pending: Optional[dict] = None,
    strike_resolver=None,
) -> Optional[dict]:
    """Construct an open_position dict from broker data.

    2026-05-22: Now accepts either:
      - LEGACY flat holdings (per-leg rows with instrument_id) — kept for back-compat
      - NEW combo positions (one row per spread with embedded legs[]) — preferred

    Uses pending hints (strikes, qty, expiry) when present to identify the
    matching spread. If broker_holdings looks like combos, delegates to
    arm_sl_from_broker_combo (broker = source of truth).
    """
    # Detect input shape: combos have option_strategy="VERTICAL" and legs[]
    combo_inputs = [
        h for h in broker_holdings
        if h.get("option_strategy") == "VERTICAL" and isinstance(h.get("legs"), list)
    ]
    if combo_inputs:
        # Combo path — preferred since 2026-05-22
        from webull_bot_atm.execution_v2 import arm_sl_from_broker_combo
        # If pending is provided, prefer the matching combo; otherwise take the first
        target = combo_inputs[0]
        if pending:
            expected_short = float(pending.get("short_strike", 0))
            expected_long = float(pending.get("long_strike", 0))
            for c in combo_inputs:
                strikes = sorted([float(l.get("option_exercise_price", 0) or 0)
                                  for l in c.get("legs", [])], reverse=True)
                if (len(strikes) == 2
                        and abs(strikes[0] - expected_short) < 0.01
                        and abs(strikes[1] - expected_long) < 0.01):
                    target = c
                    break
        try:
            stop_mult = float(pending.get("stop_multiplier", 2.0)) if pending else 2.0
            return arm_sl_from_broker_combo(
                target, stop_multiplier=stop_mult,
                entry_spx=(pending or {}).get("entry_spx", 0.0),
                entry_vix=(pending or {}).get("entry_vix", 0.0),
                client_order_id=(pending or {}).get("client_order_id", ""),
                spx_source=(pending or {}).get("spx_source", "unknown"),
                vix_source=(pending or {}).get("vix_source", "unknown"),
                chain_source=(pending or {}).get("chain_source", "unknown"),
            )
        except Exception as exc:
            log.warning(f"_build_open_position_from_broker: combo path failed: {exc}")
            return None

    # ── LEGACY FLAT-HOLDINGS PATH (kept for any callers still passing flat) ──
    if not pending:
        return None
    expected_short = float(pending["short_strike"])
    expected_long = float(pending["long_strike"])
    expected_qty = int(pending["quantity"])

    expected_short_iid = pending.get("short_iid", "")
    expected_long_iid = pending.get("long_iid", "")
    short_match = None
    long_match = None
    if expected_short_iid and expected_long_iid:
        for h in broker_holdings:
            iid = h.get("instrument_id", "")
            try:
                qty = int(h.get("qty", 0) or 0)
            except (ValueError, TypeError):
                continue
            if iid == expected_short_iid and qty == -expected_qty:
                short_match = h
            elif iid == expected_long_iid and qty == +expected_qty:
                long_match = h
        if short_match and long_match:
            log.info(
                f"[reconcile] IID fast-path matched: short_iid={expected_short_iid} "
                f"long_iid={expected_long_iid}"
            )

    if not (short_match and long_match):
        for h in broker_holdings:
            try:
                qty = int(h.get("qty", 0) or 0)
            except (ValueError, TypeError):
                continue
            strike = _holding_strike(h, strike_resolver)
            if strike is None:
                continue
            if qty == -expected_qty and abs(strike - expected_short) < 0.01 and short_match is None:
                short_match = h
            elif qty == +expected_qty and abs(strike - expected_long) < 0.01 and long_match is None:
                long_match = h

    if not (short_match and long_match):
        return None

    def _cost(h):
        for key in ("unit_cost", "cost_price", "average_cost"):
            v = h.get(key)
            if v is not None:
                try:
                    return abs(float(v))
                except (ValueError, TypeError):
                    continue
        return 0.0

    short_cost = _cost(short_match)
    long_cost = _cost(long_match)
    net_credit = round(short_cost - long_cost, 2)

    stop_multiplier = float(pending.get("stop_multiplier", 2.0))
    return {
        "symbol": pending["symbol"],
        "expiry": pending["expiry"],
        "short_strike": expected_short,
        "long_strike": expected_long,
        "quantity": expected_qty,
        "entry_credit": net_credit,
        "stop_price": round(net_credit * stop_multiplier, 2),
        "entry_spx": pending.get("entry_spx", 0.0),
        "entry_vix": pending.get("entry_vix", 0.0),
        "entry_ts": pending.get("ts", datetime.now(ET).isoformat()),
        "client_order_id": pending.get("client_order_id", ""),
        "yf_options_symbol": pending.get("yf_options_symbol", "^SPX"),
        "spx_source": pending.get("spx_source", "unknown"),
        "vix_source": pending.get("vix_source", "unknown"),
        "chain_source": pending.get("chain_source", "unknown"),
        "short_iid": short_match.get("instrument_id", ""),
        "long_iid": long_match.get("instrument_id", ""),
        "_auto_recovered": True,
    }


def _is_pending_stale(pending: dict) -> bool:
    """True if pending_order is older than PENDING_STALE_SECONDS."""
    if not pending or "ts" not in pending:
        return False
    try:
        pending_ts = datetime.fromisoformat(pending["ts"])
        age = (datetime.now(ET) - pending_ts).total_seconds()
        return age > PENDING_STALE_SECONDS
    except (ValueError, TypeError):
        return True  # if we can't parse, treat as stale


def decide_action(
    state: dict,
    broker_holdings: list[dict],
    strike_resolver=None,
) -> ReconcileDecision:
    """Pure function: decide what to do given state + broker reality.

    Returns ReconcileDecision. Caller applies side effects (write state, alert).
    """
    open_pos = state.get("open_position")
    pending = state.get("pending_order")
    has_broker_position = len(broker_holdings) > 0

    # Filter broker to ALLOWED options only (defensive)
    from webull_bot_atm.execution import ALLOWED_OPTION_SYMBOLS
    broker_holdings = [
        h for h in broker_holdings
        if h.get("symbol") in ALLOWED_OPTION_SYMBOLS
        and h.get("instrument_type") == "OPTION"
    ]

    # ── Case 1: state has open_position ─────────────────────────────────
    if open_pos:
        if not has_broker_position:
            return ReconcileDecision(
                action="CLEAR_STATE",
                reason="state has open_position but broker has nothing — closed externally?",
                alert_message=(
                    f"⚠️ Bot startup: state.open_position exists "
                    f"({int(open_pos['short_strike'])}/{int(open_pos['long_strike'])}P) "
                    f"but broker has NO matching position. Treating as closed externally."
                ),
            )

        # Verify broker has matching position
        expected_short = float(open_pos["short_strike"])
        expected_long = float(open_pos["long_strike"])
        match = _build_open_position_from_broker(
            broker_holdings, pending=open_pos, strike_resolver=strike_resolver,
        )
        if match:
            return ReconcileDecision(
                action="NORMAL_RESUME",
                reason="state.open_position matches broker positions",
            )
        return ReconcileDecision(
            action="HALT",
            reason="state has open_position but broker has DIFFERENT position",
            alert_message=(
                f"🚨 Bot startup: state.open_position says "
                f"{int(expected_short)}/{int(expected_long)} but broker shows different. "
                f"Manual review required. Bot HALTED."
            ),
        )

    # ── Case 2: state empty, no broker positions ───────────────────────
    if not has_broker_position:
        # Optional: clear stale pending if any
        if pending and _is_pending_stale(pending):
            return ReconcileDecision(
                action="CLEAR_PENDING",
                reason="stale pending exists with no matching broker position — place failed",
            )
        return ReconcileDecision(
            action="NORMAL_START",
            reason="empty state, empty broker — clean start",
        )

    # ── Case 3: state empty, broker has positions ──────────────────────
    if not pending:
        # No pending to match against — definite orphan, can't auto-recover
        details = ", ".join(
            f"{h.get('symbol')} qty={h.get('qty')}" for h in broker_holdings
        )
        return ReconcileDecision(
            action="HALT",
            reason="orphan position at broker, no pending_order to identify intent",
            alert_message=(
                f"🚨 Bot startup: broker has SPXW positions but state.json shows nothing. "
                f"No pending_order to identify intent. Cannot safely auto-recover.\n"
                f"Holdings: {details}\n"
                f"Manual review required. Bot HALTED."
            ),
        )

    # We have pending — try to match
    new_position = _build_open_position_from_broker(
        broker_holdings, pending=pending, strike_resolver=strike_resolver,
    )

    if not new_position:
        return ReconcileDecision(
            action="HALT",
            reason="pending exists but broker positions don't match its strikes",
            alert_message=(
                f"🚨 Bot startup: pending_order says "
                f"{int(pending['short_strike'])}/{int(pending['long_strike'])} but "
                f"broker has different positions. Cannot safely auto-recover."
            ),
        )

    # Match found — decide based on pending freshness
    if _is_pending_stale(pending):
        return ReconcileDecision(
            action="PROMOTE_PENDING",
            reason="stale pending matches broker — auto-recovery",
            new_open_position=new_position,
            alert_message=(
                f"✓ Bot startup: recovered orphan position "
                f"{int(pending['short_strike'])}/{int(pending['long_strike'])}P "
                f"credit=${new_position['entry_credit']} stop=${new_position['stop_price']} "
                f"(pending was stale; broker confirmed match)"
            ),
        )

    # Pending is fresh — bot may still be alive trying to place. Don't touch.
    return ReconcileDecision(
        action="DEFER_TO_BOT",
        reason="pending is fresh — assume bot is still actively placing; defer",
    )
