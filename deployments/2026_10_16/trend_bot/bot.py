"""trend_bot main loop.

Hourly cadence: wake at HH:00:25 (bar just closed, next bar just opened),
plus a light 5-minute sub-poll for stop fills and heartbeat.

Per-wake order of operations per instrument (mirrors the reference engine's
bar-i sequence: pending-entry at open → roll → stop → trail → signal):
  1. refresh bars from the LIVE gateway
  2. reconcile resting-stop fills (the exchange did the work intra-hour)
  3. roll check (front contract changed while holding => force-close)
  4. trail update from the just-closed bar (stop never moves down)
  5. Monday: recompute rotation weights (data through d-1)
  6. signal on the just-closed bar => market entry NOW + arm stop (2s invariant)

Run:  python bot.py            (long-running; launchd keeps it alive)
      python bot.py --mode default|defensive|aggressive   (toggle + exit)
      python bot.py --once     (single wake, for smoke tests)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import strategy  # noqa: E402
from alerts import send_alert  # noqa: E402
from data import BarStore, IBKRData  # noqa: E402
from deep_log import DeepLog  # noqa: E402
from executor import make_executor  # noqa: E402
from market_calendar import CME_EARLY_CLOSE, is_market_open  # noqa: E402
from process_lock import ProcessLock, ProcessLockError  # noqa: E402
from rotation import (  # noqa: E402
                      missing_expected_hourly_bars, monday_of_week,
                      trailing_returns, validate_hourly_frame,
                      validate_hourly_refresh, validate_rotation_inputs,
                      weights_for)
from state_db import StateDB, release_provenance  # noqa: E402
from state_store import StateStore, utcnow  # noqa: E402

log = logging.getLogger("trend_bot")

# frozen regime boundaries — PREREG_REGIME_FORWARD.md (2026-07-06); never edit
VIX_ASLEEP, VIX_ENERGETIC = 13.43, 20.72


def vix_regime(v: float | None) -> str | None:
    if v is None:
        return None
    if v < VIX_ASLEEP:
        return "asleep"
    if v > VIX_ENERGETIC:
        return "energetic"
    return "normal"


def assert_clean_release() -> dict:
    """Fail before any operational state path is opened or created."""
    provenance = release_provenance()
    if (provenance["tree_dirty"] or not provenance["code_commit"] or
            not provenance["config_sha256"]):
        raise RuntimeError(
            "REFUSING dirty or unverifiable source tree before opening "
            "operational state; deploy an exact clean commit")
    return provenance


class TrendBot:
    def __init__(self, cfg_path: Path = BASE / "config.yaml",
                 client_id_offset: int = 0):
        self.cfg = yaml.safe_load(cfg_path.read_text())
        assert_clean_release()
        sdir = BASE / self.cfg["ops"]["state_dir"]
        self.store = BarStore(sdir)
        self.st = StateStore(sdir)
        self.dlog = DeepLog(sdir / "logs")
        ib = self.cfg["ibkr"]
        if client_id_offset:                      # --once beside the daemon
            ib = {**ib, "data_client_id": ib["data_client_id"] + client_id_offset,
                  "trade_client_id": ib["trade_client_id"] + client_id_offset}
            self.cfg["ibkr"] = ib
        self.data = IBKRData(ib["host"], ib["data_port"], ib["data_client_id"],
                             dlog=self.dlog)
        self.ex = make_executor(self.cfg, alert=send_alert, dlog=self.dlog)
        self.db = StateDB(sdir, venue=self.ex.venue)   # authoritative journal
        self.ex.db = self.db
        self.scfg = self.cfg["strategy"]
        self.all_instruments = {**self.cfg["core_instruments"],
                                **self.cfg["sleeve_instruments"],
                                **self.cfg.get("satellite_instruments", {})}
        if self.st.state["mode"] is None:
            self.st.state["mode"] = self.cfg["rotation"]["mode"]
        self._startup_done = False
        self._stale_alerted = set()
        self._consec_errors = {"data": 0, "executor": 0}
        self._wake_failed = False
        self._degraded = set()           # syms with no fresh bar this wake
        self._reconcile_ok = False       # pre-trade reconcile gate (fail closed)
        self.st.state.setdefault("pending_entries", {})
        self.st.state.setdefault("order_intents", {})
        self.st.state.setdefault("last_decision_bar", {})
        self._identity_bound = False

    def _bind_runtime_identity(self):
        """Bind DB + JSON to the connected venue/account and hydrate FSM.

        This runs before startup/reconnect reconciliation.  A paper-to-live or
        account mismatch is a hard startup failure, never a warning.
        """
        if getattr(self, "_identity_bound", False):
            return
        db = getattr(self, "db", None)
        account = getattr(self.ex, "account", None)
        if db is None or not account:       # lightweight test executors
            return
        prior = db.identity()
        if prior and (prior["venue"] != self.ex.venue or
                      prior["account"] != account):
            raise RuntimeError(
                "STATE IDENTITY MISMATCH: durable DB belongs to "
                f"{prior['venue']}/{prior['account']}, connected to "
                f"{self.ex.venue}/{account}; JSON projection was not changed")
        provenance = db.run_provenance()
        if provenance["tree_dirty"]:
            raise RuntimeError(
                "REFUSING dirty or unverifiable source tree; deploy an exact "
                "clean commit before connecting the trading state machine")
        generation = prior["generation"] if prior else None
        generation = self.st.bind_identity(
            self.ex.venue, account, generation=generation)
        db.bind_identity(account, self.ex.venue, generation=generation)
        db.bootstrap_positions({s: self.st.pos(s)
                                for s in self.all_instruments})
        durable = db.load_positions()
        if durable:
            self.st.hydrate_positions(durable)
        soak = db.current_soak()
        soak_start = soak["started"] if soak else None
        if self.st.state.get("soak_start") != soak_start:
            self.st.state["soak_start"] = soak_start
            self.st.save()
        if soak is None:
            self.st.state["soak_release_valid"] = None
            self._set_readiness(
                "soak", "NOT_STARTED",
                "qualification requires explicit ops/start_soak.py gate")
        elif soak.get("invalidated_at"):
            self.st.state["soak_release_valid"] = False
            self._set_readiness(
                "soak", "INVALID", f"epoch={soak['epoch_id']} invalidated "
                f"at {soak['invalidated_at']}: "
                f"{soak['invalidation_reason']}")
        elif (soak["code_commit"] != provenance["code_commit"] or
              soak["config_sha256"] != provenance["config_sha256"]):
            self.st.state["soak_release_valid"] = False
            self._set_readiness(
                "soak", "INVALID",
                "code/config changed after soak epoch; start a new epoch")
        else:
            self.st.state["soak_release_valid"] = True
            self._set_readiness(
                "soak", "ACTIVE", f"epoch={soak['epoch_id']} "
                f"started={soak['started']}")
        self.st.save()
        if self.st.load_error:
            self._degraded.add("projection_recovered")
            detail = (f"state.json recovered from durable FSM after "
                      f"{self.st.load_error}; quarantined="
                      f"{self.st.quarantined_path}")
            self.st.event(detail)
            send_alert(f"⚠️ {detail}")
            self._set_readiness("projection", "DEGRADED", detail[:200])
        self._identity_bound = True

    @staticmethod
    def _copy_position(p: dict) -> dict:
        return {k: v for k, v in dict(p).items() if k != "fsm_state"}

    def _transition_key(self, kind: str, sym: str, fill=None, *,
                        suffix: str | None = None) -> str:
        account = getattr(self.ex, "account", None) or "test"
        ids = sorted(x for x in getattr(fill, "exec_ids", []) if x) \
            if fill is not None else []
        if ids:
            identity = ",".join(ids)
        elif fill is not None and getattr(fill, "order_key", None):
            identity = (f"{fill.order_key}:"
                        f"{float(getattr(fill, 'filled_total', fill.qty))}")
        elif fill is not None:
            identity = (f"order:{fill.order_id}:qty:{float(fill.qty)}:"
                        f"px:{float(fill.px)}")
        else:
            identity = suffix or utcnow()
        if suffix and fill is not None:
            identity = f"{identity}:{suffix}"
        return f"{kind}:{account}:{sym}:{identity}"

    @staticmethod
    def _execution_rows(fill, side: str, *, local_symbol=None,
                        con_id=None) -> list[dict]:
        details = getattr(fill, "exec_details", None) or [
            (None, fill.qty, fill.px, fill.commission)]
        return [dict(side=side, qty=float(q), px=float(px), exec_id=xid,
                     commission=comm, local_symbol=local_symbol, con_id=con_id,
                     exec_time=getattr(fill, "exec_time", None),
                     order_id=getattr(fill, "order_id", None))
                for xid, q, px, comm in details if float(q) > 0]

    @staticmethod
    def _merge_fills(*fills):
        """Combine sequential close orders into one auditable transition."""
        from executor import Fill
        valid = [fill for fill in fills if fill is not None and fill.qty > 0]
        qty = sum(float(fill.qty) for fill in valid)
        if qty <= 0:
            return Fill(0.0, None, qty=0)
        px = sum(float(fill.qty) * float(fill.px) for fill in valid) / qty
        known_fees = [float(fill.commission) for fill in valid
                      if fill.commission is not None]
        merged = Fill(
            px, sum(known_fees) if known_fees else None, qty=qty,
            exec_time=next((fill.exec_time for fill in valid
                            if fill.exec_time is not None), None),
            order_id=valid[-1].order_id)
        details = []
        for fill in valid:
            rows = list(getattr(fill, "exec_details", []) or [])
            if rows:
                details.extend(rows)
            else:
                details.append((None, float(fill.qty), float(fill.px),
                                fill.commission))
        merged.exec_details = details
        merged.exec_ids = list(dict.fromkeys(
            xid for xid, *_ in details if xid))
        merged.order_key = "bounded-close:" + ":".join(
            str(getattr(fill, "order_key", None) or fill.order_id)
            for fill in valid)
        merged.filled_total = qty
        return merged

    def _commit_position_transition(self, event_key: str, sym: str,
                                    event_type: str, next_position: dict, *,
                                    fill=None, side: str | None = None,
                                    trade: dict | None = None,
                                    entry_exec_ids=(), exit_exec_ids=(),
                                    payload: dict | None = None,
                                    executions: list[dict] | None = None) -> bool:
        """DB-first commit; JSON/CSV update only after SQLite succeeds."""
        db = getattr(self, "db", None)
        clean = self._copy_position(next_position)
        executions = executions if executions is not None else (
            self._execution_rows(
                fill, side, local_symbol=clean.get("local_symbol"),
                con_id=clean.get("con_id"))
            if fill is not None and side else [])
        # ESTIMATED TIER (review r7 P0-1): a row without a broker execId is
        # not a journalable execution — it becomes explicit UNBASED quantity
        # in the event payload, which the basis_pnl correction gate must
        # later resolve. Without this, every degraded/estimated transition
        # (nightly-reset wipes, reconnect-blip fills) crash-looped on the
        # execId IntegrityError and the surviving stop was never handled.
        unbased = [r for r in executions if not r.get("exec_id")]
        executions = [r for r in executions if r.get("exec_id")]
        if unbased:
            uq = sum(float(r["qty"]) for r in unbased)
            upx = (sum(float(r["qty"]) * float(r["px"]) for r in unbased)
                   / uq if uq else None)
            payload = dict(payload or {})
            payload.setdefault("basis", "estimated")
            payload["unbased_qty"] = uq
            payload["estimated_px"] = upx
            self.st.event(f"{sym}: {event_type} carries {uq:.0f} unbased "
                          f"execution(s) @ ~{upx} — basis_pnl correction "
                          "required before the ledger is clean")
            self._degraded.add("journal-basis")
        inserted = True
        if db is not None:
            inserted = db.commit_transition(
                event_key, sym, event_type, clean, executions=executions,
                trade=trade, entry_exec_ids=entry_exec_ids,
                exit_exec_ids=exit_exec_ids,
                order_key=getattr(fill, "order_key", None) if fill else None,
                filled_total=(getattr(fill, "filled_total", None)
                              if fill else None), payload=payload)
            canonical = db.load_positions().get(sym, clean)
        else:
            canonical = clean
        self.st.state["positions"][sym] = self._copy_position(canonical)
        if inserted and trade is not None:
            try:
                self.st.record_trade(**trade)
            except Exception as e:
                # SQLite is canonical; a CSV projection can be regenerated.
                self.st.event(f"CSV trade projection failed: {e}")
                self._degraded.add("projection")
        self.st.save()
        return inserted

    def _commit_emergency_fill(self, label: str, fill, side: str,
                               *, con_id=None, local_symbol=None) -> str:
        """Journal a kill-switch execution without inventing entry basis."""
        event_key = self._transition_key("emergency", label, fill)
        db = getattr(self, "db", None)
        if db is not None:
            rows = self._execution_rows(
                fill, side, con_id=con_id, local_symbol=local_symbol)
            identified = [r for r in rows if r.get("exec_id")]
            dropped = [r for r in rows if not r.get("exec_id")]
            payload = {"reason": "kill_switch", "basis": "unmanaged"}
            if dropped:
                uq = sum(float(r["qty"]) for r in dropped)
                payload["unbased_qty"] = uq
                payload["basis"] = "estimated"
                self.st.event(f"{label}: emergency fill has {uq:.0f} "
                              "execution(s) without broker execIds — "
                              "journaled as unbased; correction required")
            if identified:
                db.commit_execution_event(
                    event_key, label, "EMERGENCY_FLATTEN",
                    executions=identified, payload=payload)
            elif dropped:
                # nothing journalable — the event REQUIRES execIds; alert
                # instead of crashing the emergency path (review r7)
                send_alert(f"🆘 {label}: emergency fill had NO broker "
                           "execIds — unjournaled; manual ledger correction "
                           "required")
        return event_key

    def _recover_emergency_fills(self) -> int:
        """Apply raw unmanaged close/cover executions after an interrupted DB write."""
        db = getattr(self, "db", None)
        if db is None:
            return 0
        grouped: dict[str, list[dict]] = {}
        for row in db.unapplied_emergency_executions():
            grouped.setdefault(row.get("order_ref") or row["sym"], []).append(row)
        recovered = 0
        pending = self.st.state.setdefault("kill_unresolved", {})
        for ref, rows in grouped.items():
            label = rows[0]["sym"]
            ids = sorted(r["exec_id"] for r in rows if r.get("exec_id"))
            key = (f"emergency-recovery:{getattr(self.ex, 'account', None)}:"
                   f"{label}:{','.join(ids)}")
            db.commit_execution_event(
                key, label, "EMERGENCY_FLATTEN_RECOVERY",
                executions=[dict(
                    side=r["side"], qty=r["qty"], px=r["px"],
                    exec_id=r.get("exec_id"), commission=r.get("commission"),
                    local_symbol=r.get("local_symbol"), con_id=r.get("con_id"),
                    exec_time=r.get("exec_time"), order_ref=ref,
                    order_id=r.get("order_id"), perm_id=r.get("perm_id"))
                    for r in rows],
                payload={"reason": "kill_switch_recovery",
                         "basis": "unmanaged"})
            for unresolved_key, item in list(pending.items()):
                if item.get("label") == label:
                    pending.pop(unresolved_key, None)
            recovered += len(rows)
        return recovered

    def _set_readiness(self, component: str, status: str, detail: str = ""):
        db = getattr(self, "db", None)
        if db is not None:
            try:
                db.set_readiness(component, status, detail)
            except Exception as e:
                self._degraded.add("readiness")
                self.dlog.log("account", event="readiness_write_error",
                              component=component, error=str(e)[:150])

    def _protection_readiness(self) -> tuple[str, str]:
        try:
            broker = self.ex.positions()
        except Exception as e:
            return "FAILED", f"broker positions unavailable: {e}"
        missing = []
        for sym, icfg in self.all_instruments.items():
            p = self.st.pos(sym)
            if not p.get("position"):
                continue
            held = broker.get(p.get("con_id"), 0)
            if held <= 0 or not self.ex.has_working_stop(
                    sym, held, con_id=p.get("con_id"),
                    stop_px=p.get("stop_px"),
                    px_tol=icfg["tick"] * 1e-6):
                missing.append(f"{sym}(broker={held},state={p['qty']})")
        return ("OK", "all open positions have canonical broker stops") \
            if not missing else ("FAILED", f"unprotected={missing}")

    def _recover_protection_halt(self) -> bool:
        """Release only a resolved protection halt after a fully healthy wake.

        Re-arming a stop or closing its position resolves the original hazard,
        but does not by itself prove that the whole account is safe. Re-read
        orders/positions and require the durable journal to be caught up. This
        audit never submits, cancels, or changes an order.
        """
        reason = self.st.state.get("halt_reason")
        if (not self.st.state.get("halt_entries") or
                reason not in {f"{s} position unprotected"
                               for s in self.all_instruments}):
            return False
        db = getattr(self, "db", None)
        if (db is None or self._degraded or not self._startup_done or
                not self._reconcile_ok or
                (self.st.dir / "KILL_SWITCH").exists() or
                self.st.state.get("order_intents") or
                self.st.state.get("kill_unresolved") or
                getattr(self.ex, "unresolved_duplicate_stops", None)):
            return False
        try:
            if not self.ex.is_connected():
                return False
            readiness = db.readiness()
            if any(readiness.get(k, {}).get("status") != "OK" for k in
                   ("provenance", "broker", "data", "accounting",
                    "protection", "reconciliation")):
                return False
            orders = self.ex.open_account_orders()
            if self._position_diff():
                return False
            held = {s: self.st.pos(s) for s in self.all_instruments
                    if self.st.pos(s).get("position")}
            seen = set()
            for item in orders:
                ref = str(item.get("ref") or "")
                parts = ref.split(":")
                if (len(parts) != 3 or parts[0] != "trendbot" or
                        parts[2] != "stop" or parts[1] not in held or
                        parts[1] in seen):
                    return False  # Includes orphan, foreign, and entry orders.
                sym = parts[1]
                p = held[sym]
                trade = item["trade"]
                order, status = trade.order, trade.orderStatus
                if (status.status not in ("PreSubmitted", "Submitted") or
                        order.action != "SELL" or order.orderType != "STP" or
                        order.account != self.ex.account or
                        trade.contract.conId != p["con_id"] or
                        order.orderId != p["stop_order_id"] or
                        float(order.totalQuantity) - float(status.filled or 0)
                        != p["qty"] or
                        not abs(float(order.auxPrice) - float(p["stop_px"]))
                        <= self.all_instruments[sym]["tick"] * 1e-6):
                    return False
                seen.add(sym)
            if (seen != set(held) or self._protection_readiness()[0] != "OK" or
                    db.pending_events() or db.unapplied_execution_count()):
                return False
            # Account refreshes pump callbacks; recheck mutable guards last.
            if (self.st.state.get("halt_reason") != reason or self._degraded or
                    self.st.state.get("order_intents") or
                    self.st.state.get("kill_unresolved") or
                    getattr(self.ex, "unresolved_duplicate_stops", None) or
                    (self.st.dir / "KILL_SWITCH").exists()):
                return False
        except Exception as e:
            self.dlog.log("account", event="protection_halt_recovery_blocked",
                          error=str(e)[:200])
            return False
        self.st.state["halt_entries"] = False
        self.st.state["halt_reason"] = None
        self.st.event(f"protection halt cleared ({reason}) — positions, stops "
                      "and journal verified; entries resumed")
        self.st.save()
        return True

    def _intent_execution_rows(self, sym: str,
                               intent: dict) -> tuple[list[dict], list[dict]]:
        """Return exact entry rows and plausible post-entry exit rows."""
        db = getattr(self, "db", None)
        if db is None:
            return [], []
        intent_id = intent.get("intent_id")
        ref = f"trendbot:{sym}:entry:{intent_id}"
        buys = [r for r in db.executions_for_ref(ref)
                if str(r.get("side", "")).upper() in ("BUY", "BOT")]
        entry_times = [pd.Timestamp(r["exec_time"]) for r in buys
                       if r.get("exec_time")]
        first_entry = min(entry_times) if entry_times else None
        con_id = intent.get("con_id")
        close_ref = f"trendbot:{sym}:close:{intent_id}"
        sells = []
        for row in db.unapplied_executions(sym, side="SELL"):
            if con_id and row.get("con_id") != con_id:
                continue
            row_ref = row.get("order_ref") or ""
            exact_ref = row_ref == close_ref
            protective_ref = row_ref == f"trendbot:{sym}:stop"
            legacy_close = row_ref == f"trendbot:{sym}:close"
            if not (exact_ref or protective_ref or legacy_close):
                continue
            if not exact_ref and first_entry is not None:
                try:
                    if (not row.get("exec_time") or
                            pd.Timestamp(row["exec_time"]) < first_entry):
                        continue
                except Exception:
                    continue
            sells.append(row)
        return buys, sells

    def _book_recovered_intent_exits(self, sym: str, intent: dict,
                                     held_qty: float) -> str:
        """Book entry exits already reflected in the adopted broker qty."""
        event_key = (f"entry-recovery-exit:"
                     f"{getattr(self.ex, 'account', None)}:{sym}:"
                     f"{intent.get('intent_id')}")
        db = getattr(self, "db", None)
        if db is not None and db.event_status(event_key) == "COMMITTED":
            return "booked"
        buys, sells = self._intent_execution_rows(sym, intent)
        if not buys:
            return "unresolved"
        bought = sum(float(r["qty"]) for r in buys)
        sold = sum(float(r["qty"]) for r in sells)
        expected_sold = bought - float(held_qty)
        if expected_sold < -1e-9 or abs(sold - expected_sold) > 1e-9:
            return "unresolved"
        if sold <= 0:
            return "none"
        icfg = self.all_instruments.get(sym)
        if icfg is None:
            return "unresolved"
        entry_px = sum(r["qty"] * r["px"] for r in buys) / bought
        exit_px = sum(r["qty"] * r["px"] for r in sells) / sold
        entry_ids = [r["exec_id"] for r in buys if r.get("exec_id")]
        exit_ids = [r["exec_id"] for r in sells if r.get("exec_id")]
        commission = sum(float(r.get("commission") or 0)
                         for r in buys + sells) or None
        first_entry = min((pd.Timestamp(r["exec_time"]) for r in buys
                           if r.get("exec_time")), default=None)
        trade = dict(
            exit_time=utcnow(), book=self._book(sym), sym=sym,
            trade_symbol=icfg["trade_symbol"], qty=int(sold),
            entry_time=(str(first_entry) if first_entry is not None
                        else intent.get("created")),
            entry_px=entry_px, exit_px=exit_px,
            reason="entry_partial_exit_recovery",
            pnl_points=exit_px - entry_px,
            pnl_usd=(exit_px - entry_px) * icfg["mult"] * sold,
            commission=commission, manual_commission=0,
            entry_weight=intent.get("qty"),
            local_symbol=intent.get("local_symbol"))
        nxt = dict(self.st.pos(sym))
        nxt["entry_fees_allocated"] = True
        self._commit_position_transition(
            event_key, sym, "ENTRY_PARTIAL_EXIT_RECOVERY", nxt,
            trade=trade, entry_exec_ids=entry_ids, exit_exec_ids=exit_ids,
            payload={"bought": bought, "sold": sold,
                     "broker_residual": held_qty})
        self.st.event(f"{sym}: booked {sold:.0f}x pre-adoption exit; "
                      f"{held_qty:.0f}x broker residual remains")
        return "booked"

    def _recover_flat_intent_roundtrip(self, sym: str, intent: dict, *,
                                       known_buy_qty: float = 0.0) -> str:
        """Account for a crash-window entry that is broker-flat now.

        Returns ``roundtrip``, ``no_fill``, or ``unresolved``. Flat broker
        quantity is not evidence that no trade happened; matched durable BUY
        and SELL executions are required before resolving a filled intent.
        """
        db = getattr(self, "db", None)
        intent_id = intent.get("intent_id")
        event_key = intent.get("event_key") or (
            f"entry-intent:{getattr(self.ex, 'account', None)}:{sym}:"
            f"{intent_id}")
        if db is None:
            return "unresolved" if known_buy_qty else "no_fill"
        buys, sells = self._intent_execution_rows(sym, intent)
        buy_qty = sum(float(r["qty"]) for r in buys)
        if not buys:
            return "unresolved" if known_buy_qty else "no_fill"

        entry_times = [pd.Timestamp(r["exec_time"]) for r in buys
                       if r.get("exec_time")]
        first_entry = min(entry_times) if entry_times else None
        sell_qty = sum(float(r["qty"]) for r in sells)
        if abs(sell_qty - buy_qty) > 1e-9:
            return "unresolved"

        icfg = self.all_instruments.get(sym)
        if icfg is None or buy_qty <= 0:
            return "unresolved"
        entry_px = sum(r["qty"] * r["px"] for r in buys) / buy_qty
        exit_px = sum(r["qty"] * r["px"] for r in sells) / sell_qty
        entry_ids = [r["exec_id"] for r in buys if r.get("exec_id")]
        exit_ids = [r["exec_id"] for r in sells if r.get("exec_id")]
        commission = sum(float(r.get("commission") or 0)
                         for r in buys + sells) or None
        trade = dict(
            exit_time=utcnow(), book=self._book(sym), sym=sym,
            trade_symbol=icfg["trade_symbol"], qty=int(buy_qty),
            entry_time=(str(first_entry) if first_entry is not None
                        else intent.get("created")),
            entry_px=entry_px, exit_px=exit_px,
            reason="entry_roundtrip_recovery",
            pnl_points=exit_px - entry_px,
            pnl_usd=(exit_px - entry_px) * icfg["mult"] * buy_qty,
            commission=commission, manual_commission=0,
            entry_weight=intent.get("qty"),
            local_symbol=intent.get("local_symbol"))
        from state_store import EMPTY_POS
        self._commit_position_transition(
            event_key, sym, "ENTRY_ROUNDTRIP_RECOVERY", dict(EMPTY_POS),
            trade=trade, entry_exec_ids=entry_ids, exit_exec_ids=exit_ids,
            payload={"recovery": "broker_flat_roundtrip",
                     "buy_qty": buy_qty, "sell_qty": sell_qty})
        self.st.event(f"{sym}: recovered broker-flat entry round trip "
                      f"{buy_qty:.0f}x {entry_px}->{exit_px}")
        send_alert(f"🔧 {sym}: recovered and booked crash-window round trip "
                   f"{buy_qty:.0f}x {entry_px}->{exit_px}")
        return "roundtrip"

    # ---------------- startup reconciliation ---------------------------
    def startup_reconcile(self):
        recovered = self.ex.recover_stops()
        if getattr(self.ex, "unresolved_duplicate_stops", None):
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = (
                f"unresolved duplicate stops: "
                f"{self.ex.unresolved_duplicate_stops}")
            self.st.event(f"DUPLICATE STOPS unresolved — entries halted: "
                          f"{self.ex.unresolved_duplicate_stops}")
        # crash-window recovery (review 2026-07-19 restructure):
        #   phase 1 — resolve WORKING entries (cancel + verified terminal);
        #             a fill-during-cancel forces a RESCAN of executions
        #   phase 2 — adopt executions AGGREGATED per intent ref (partials
        #             sum; wavg price), exactly once, exact-ref match
        #   intents for UNRESOLVED orders are PRESERVED for the next start
        intents = dict(self.st.state.get("order_intents") or {})
        db = getattr(self, "db", None)
        if db is not None and getattr(db, "account", None):
            # SQLite survives a crash between durable intent creation and the
            # JSON projection.  Merge every unresolved entry intent back into
            # the operational view before querying the broker.
            for event in db.pending_events(["ENTRY_INTENT"]):
                intent = dict(event["payload"])
                intent.setdefault("event_key", event["event_key"])
                if intent.get("intent_id"):
                    intents.setdefault(event["sym"], intent)
            # SQLite may have committed just before a crash prevented the
            # JSON cleanup. The DB is authoritative; do not recover it twice.
            for sym, intent in list(intents.items()):
                event_key = intent.get("event_key") or (
                    f"entry-intent:{getattr(self.ex, 'account', None)}:{sym}:"
                    f"{intent.get('intent_id')}")
                if (not intent.get("recovery_pending") and
                        db.event_status(event_key) in
                        {"COMMITTED", "RESOLVED", "CANCELLED", "FLAT"}):
                    intents.pop(sym, None)
            self.st.state["order_intents"] = intents
        unresolved: set[str] = set()
        today = utcnow()[:10]
        for s_i, it_i in intents.items():
            if str(it_i.get("created", ""))[:10] != today:
                # reqExecutions only covers TODAY (r6 #1): an intent from
                # a prior session cannot be correlated — never erase it
                unresolved.add(s_i)
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{s_i} intent predates today's execution window"
                self.st.event(f"{s_i}: intent from {it_i.get('created')} "
                              "cannot be correlated by a same-day scan — "
                              "preserved, entries halted (manual check)")
        items = (self.ex.recover_entries()
                 if hasattr(self.ex, "recover_entries") else [])
        fill_pending: set[str] = set()
        for item in [i for i in items if i["kind"] == "working_entry"]:
            sym = item["sym"]
            try:
                outcome = self.ex.cancel_and_verify(item["trade"])
            except Exception:
                outcome = "unknown"
            if outcome == "cancelled":
                self.st.event(f"{sym}: cancelled stale working entry "
                              f"(order {item['order_id']}) from prior run")
            elif outcome == "filled":
                self.st.event(f"{sym}: stale entry {item['order_id']} "
                              "FILLED during cancel — rescanning executions")
                fill_pending.add(sym)
            else:
                self.st.event(f"{sym}: stale entry {item['order_id']} "
                              "state UNRESOLVED — halting entries, intent "
                              "PRESERVED for next start")
                unresolved.add(sym)
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    "stale working entry unresolved at broker"
        if fill_pending:
            # the just-confirmed fills were NOT in the first scan
            items = self.ex.recover_entries()
        # an INCOMPLETE execution scan must not erase recovery keys: keep
        # every pending intent and halt until a clean scan (review r3 #2)
        scan_failed = any(i["kind"] == "scan_error" for i in items)
        if scan_failed:
            unresolved.update(intents.keys())
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = "execution scan incomplete"
            self.st.event("execution scan FAILED — all pending intents "
                          "preserved, entries halted")
        if hasattr(self.ex, "recover_bot_executions"):
            try:
                recovered_execs = self.ex.recover_bot_executions()
                if db is not None:
                    for row in recovered_execs:
                        db.execution(
                            row["sym"], row["side"], row["qty"], row["px"],
                            local_symbol=row.get("local_symbol"),
                            con_id=row.get("con_id"),
                            commission=row.get("commission"),
                            exec_id=row.get("exec_id"),
                            exec_time=row.get("time"),
                            order_ref=row.get("ref"),
                            account=row.get("account"))
            except Exception as e:
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = "broker execution scan incomplete"
                self.st.event(f"broker execution recovery scan FAILED: {e}")
        # Merge the broker scan with the durable raw journal. A callback may
        # have reached SQLite before a crash even when the next broker scan no
        # longer returns the execution; durable evidence must still drive
        # adoption. Deduplicate exact execIds across both sources.
        entry_items = [i for i in items if i["kind"] == "entry_execution"]
        if db is not None:
            for sym, intent in intents.items():
                ref = f"trendbot:{sym}:entry:{intent.get('intent_id')}"
                for row in db.executions_for_ref(ref):
                    if str(row.get("side", "")).upper() not in ("BUY", "BOT"):
                        continue
                    entry_items.append({
                        "kind": "entry_execution", "sym": sym, "ref": ref,
                        "con_id": row.get("con_id") or intent.get("con_id"),
                        "local_symbol": (row.get("local_symbol") or
                                         intent.get("local_symbol")),
                        "qty": row["qty"], "px": row["px"],
                        "exec_id": row.get("exec_id"),
                        "time": row.get("exec_time") or intent.get("created"),
                    })
        # aggregate executions per orderRef: partial fills are several
        # executions of ONE intent — adopt the SUM at the weighted price
        agg: dict[str, dict] = {}
        seen_entry_execs = set()
        for item in entry_items:
            identity = (item["ref"], item.get("exec_id") or
                        (item.get("qty"), item.get("px"), item.get("time")))
            if identity in seen_entry_execs:
                continue
            seen_entry_execs.add(identity)
            a = agg.setdefault(item["ref"], dict(
                sym=item["sym"], ref=item["ref"], qty=0.0, notional=0.0,
                con_id=item["con_id"], local_symbol=item["local_symbol"],
                time=item["time"]))
            a["qty"] += float(item["qty"])
            a["notional"] += float(item["qty"]) * float(item["px"])
            a["time"] = min(a["time"], item["time"])
        # broker positions BEFORE any adoption (review r5 #3): a BUY
        # execution does not imply an open position — it may have closed
        # pre-crash; the broker's net holding caps what we adopt
        try:
            pre_acct = self.ex.positions()
        except Exception:
            pre_acct = None
        if pre_acct is None:
            unresolved.update(intents.keys())
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = \
                "broker positions unavailable during entry recovery"

        # Execution-gap recovery: the broker API's default execution scan is
        # session-limited. If an intent's contract is held but neither the
        # broker nor durable journal returned the BUY, adopt broker quantity
        # and arm protection with explicitly estimated basis. This applies
        # across midnight and to same-session broker-history gaps.
        for sym, it in list(intents.items()):
            stale_intent = str(it.get("created", ""))[:10] != today
            exact_ref = f"trendbot:{sym}:entry:{it.get('intent_id')}"
            if not stale_intent and exact_ref in agg:
                continue
            con_id = it.get("con_id")
            held_now = (float(pre_acct.get(con_id, 0))
                        if pre_acct is not None and con_id else None)
            event_key = it.get("event_key") or (
                f"entry-intent:{getattr(self.ex, 'account', None)}:{sym}:"
                f"{it.get('intent_id')}")
            if held_now == 0 and not stale_intent:
                # Same-session broker-flat + no execution evidence is the
                # normal verified-no-fill path handled below.
                continue
            if held_now == 0:
                # A prior-session execution scan may no longer be available;
                # broker-flat alone cannot distinguish no-fill from a fully
                # round-tripped fill. Require durable execution proof.
                resolution = self._recover_flat_intent_roundtrip(
                    sym, it, known_buy_qty=-1.0)
                if resolution == "unresolved":
                    unresolved.add(sym)
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} flat intent has unmatched executions"
                    self.st.event(f"{sym}: broker flat but durable entry/exit "
                                  "executions do not pair — intent preserved")
                else:
                    unresolved.discard(sym)
                    if db is not None and resolution == "no_fill":
                        try:
                            db.update_event(
                                event_key, "RESOLVED",
                                payload={"resolution":
                                         "broker_flat_no_execution"})
                        except KeyError:
                            pass
                    self.st.event(f"{sym}: stale intent resolved — broker "
                                  f"flat ({resolution})")
                continue
            if held_now is None or held_now < 0 or sym not in self.all_instruments:
                continue
            if self.st.pos(sym)["position"]:
                continue
            icfg = self.all_instruments[sym]
            buys, _ = self._intent_execution_rows(sym, it)
            q_seen = sum(r["qty"] for r in buys)
            if q_seen:
                entry_px = sum(r["qty"] * r["px"] for r in buys) / q_seen
                entry_time = min((r.get("exec_time") for r in buys
                                  if r.get("exec_time")), default=it["created"])
                basis_quality = "broker_execution"
            else:
                entry_px = float(it["sig_close"])
                entry_time = it["created"]
                basis_quality = "estimated_from_signal"
            stop_px = strategy.floor_to_tick(
                strategy.initial_stop(entry_px, float(it["sig_atr"]),
                                      self.scfg["k_trail"]), icfg["tick"])
            try:
                if sym in recovered and self.ex.has_working_stop(
                        sym, held_now, con_id=con_id, stop_px=stop_px,
                        px_tol=icfg["tick"] * 1e-6):
                    oid = recovered[sym].order.orderId
                else:
                    if sym in recovered:
                        outcome = self.ex.cancel_stop(sym)
                        if outcome not in ("cancelled", "none"):
                            raise RuntimeError(
                                f"existing stop cancel {outcome}")
                    c = self.ex.contract_by_conid(con_id)
                    oid = self.ex.place_stop(sym, c, int(held_now), stop_px)
                nxt = dict(self.st.pos(sym))
                nxt.update(position=1, qty=int(held_now), entry_px=entry_px,
                           entry_time=entry_time, hw=entry_px, stop_px=stop_px,
                           sig_atr=it["sig_atr"], con_id=con_id,
                           local_symbol=it["local_symbol"], stop_order_id=oid,
                           entry_weight=it["qty"], entry_commission=None,
                           entry_exec_ids=[r["exec_id"] for r in buys
                                           if r.get("exec_id")],
                           entry_fees_allocated=False,
                           basis_quality=basis_quality,
                           basis_qty=int(held_now))
                if (basis_quality != "broker_execution" or
                        abs(q_seen - held_now) > 1e-9):
                    # Persist the second-phase recovery key BEFORE the entry
                    # adoption commit. A crash between adoption and exit
                    # accounting must not let COMMITTED hide the gap.
                    it["recovery_pending"] = True
                    self.st.state["order_intents"][sym] = it
                    self.st.save()
                state_event_key = (event_key if basis_quality ==
                                   "broker_execution" else
                                   f"recovery-protection:{event_key}")
                self._commit_position_transition(
                    state_event_key, sym, "ENTRY_RECOVERY", nxt,
                    entry_exec_ids=(nxt["entry_exec_ids"]
                                    if basis_quality == "broker_execution"
                                    else []),
                    payload={"recovery": "cross_midnight",
                             "basis_quality": basis_quality})
                recovered[sym] = True
                exit_result = (self._book_recovered_intent_exits(
                    sym, it, held_now) if basis_quality == "broker_execution"
                    else "unresolved")
                if exit_result == "unresolved":
                    unresolved.add(sym)
                    it["recovery_pending"] = True
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} recovered with incomplete execution provenance"
                else:
                    unresolved.discard(sym)
                self.st.event(f"{sym}: cross-midnight intent ADOPTED for "
                              f"broker qty {held_now:.0f}; stop {stop_px} "
                              f"({basis_quality})")
                send_alert(f"🔧 {sym}: cross-midnight orphan adopted and "
                           f"protected ({held_now:.0f}x @ stop {stop_px}); "
                           f"basis={basis_quality}")
            except Exception as e:
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{sym} stale intent protection failed"
                self.st.event(f"{sym}: stale intent recovery FAILED: {e}")
                send_alert(f"🆘 {sym}: broker holds {held_now} from stale "
                           f"intent but protection failed: {e} — MANUAL NOW")

        for a in agg.values():
            sym = a["sym"]
            p = self.st.pos(sym)
            it = intents.get(sym)
            # adopt ONLY the execution set whose orderRef carries THIS
            # intent's unique id — never an older same-symbol fill
            if (p["position"] or it is None or a["qty"] <= 0
                    or not str(a["ref"]).endswith(
                        str(it.get("intent_id", "\x00")))):
                continue
            if pre_acct is None:
                unresolved.add(sym)
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    "broker positions unavailable during adoption"
                self.st.event(f"{sym}: adoption deferred — broker "
                              "positions unavailable; intent preserved")
                continue
            a["px"] = a["notional"] / a["qty"]   # wavg BEFORE cap (r6 #4)
            held_now = float(pre_acct.get(a["con_id"], 0))
            if held_now <= 0:
                resolution = self._recover_flat_intent_roundtrip(
                    sym, it, known_buy_qty=a["qty"])
                if resolution == "unresolved":
                    unresolved.add(sym)
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} flat crash-window fill unmatched"
                    self.st.event(f"{sym}: crash-window BUY found and broker "
                                  "is flat, but exit executions are unmatched "
                                  "— intent preserved")
                continue
            gross_qty = float(a["qty"])
            a["qty"] = min(gross_qty, held_now)
            if icfg := self.all_instruments.get(sym):
                px = a.get("px") or (a["notional"] / a["qty"])
                stop_px = strategy.floor_to_tick(
                    strategy.initial_stop(px, it["sig_atr"],
                                          self.scfg["k_trail"]),
                    icfg["tick"])
                c = self.ex.contract_by_conid(a["con_id"])
                # a crash AFTER incremental protection armed a stop leaves
                # one already resting — adopting must REUSE or replace it,
                # never add a second (review r3 #1)
                if sym in recovered and self.ex.has_working_stop(
                        sym, int(a["qty"]), con_id=a["con_id"],
                        stop_px=stop_px, px_tol=icfg["tick"] * 1e-6):
                    oid = getattr(getattr(recovered[sym], "order", None),
                                  "orderId", None)
                    self.st.event(f"{sym}: adoption reusing the already-"
                                  "resting protection stop")
                elif sym in recovered:
                    outcome = self.ex.cancel_stop(sym)
                    if outcome not in ("cancelled", "none"):
                        unresolved.add(sym)
                        self.st.state["halt_entries"] = True
                        self.st.state["halt_reason"] = \
                            f"{sym} residual stop unresolved during adoption"
                        self.st.event(f"{sym}: residual stop cancel="
                                      f"{outcome} during adoption — no "
                                      "replacement placed; adoption deferred")
                        continue
                    oid = self.ex.place_stop(sym, c, int(a["qty"]), stop_px)
                else:
                    oid = self.ex.place_stop(sym, c, int(a["qty"]), stop_px)
                raw_execs = [i for i in entry_items if i["ref"] == a["ref"]]
                # The comprehension above can still contain the same execId
                # once from the broker and once from SQLite. Preserve one row
                # per broker execution for atomic application.
                dedup_raw, raw_execs_unique = set(), []
                for raw in raw_execs:
                    rid = raw.get("exec_id") or (
                        raw.get("qty"), raw.get("px"), raw.get("time"))
                    if rid not in dedup_raw:
                        dedup_raw.add(rid)
                        raw_execs_unique.append(raw)
                raw_execs = raw_execs_unique
                entry_ids = [i.get("exec_id") for i in raw_execs
                             if i.get("exec_id")]
                nxt = dict(p)
                nxt.update(position=1, qty=int(a["qty"]), entry_px=px,
                           entry_time=a["time"], hw=px, stop_px=stop_px,
                           sig_atr=it["sig_atr"], con_id=a["con_id"],
                           local_symbol=a["local_symbol"],
                           stop_order_id=oid, entry_weight=it["qty"],
                           entry_commission=None, entry_exec_ids=entry_ids,
                           entry_fees_allocated=False,
                           basis_quality="broker_execution",
                           basis_qty=int(a["qty"]))
                if abs(gross_qty - held_now) > 1e-9:
                    it["recovery_pending"] = True
                    self.st.state["order_intents"][sym] = it
                    self.st.save()
                event_key = it.get("event_key") or (
                    f"entry-intent:{getattr(self.ex, 'account', None)}:{sym}:"
                    f"{it.get('intent_id')}")
                self._commit_position_transition(
                    event_key, sym, "ENTRY_RECOVERY", nxt,
                    entry_exec_ids=entry_ids,
                    payload={"recovery": "same_session",
                             "order_ref": a["ref"]},
                    executions=[dict(
                        side="BUY", qty=float(raw["qty"]),
                        px=float(raw["px"]), exec_id=raw.get("exec_id"),
                        local_symbol=raw["local_symbol"],
                        con_id=raw["con_id"], exec_time=raw.get("time"),
                        order_ref=raw.get("ref")) for raw in raw_execs])
                exit_result = (self._book_recovered_intent_exits(
                    sym, it, held_now) if db is not None else
                    ("none" if abs(gross_qty - held_now) < 1e-9
                     else "unresolved"))
                if exit_result == "unresolved":
                    unresolved.add(sym)
                    it["recovery_pending"] = True
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} adoption exit provenance incomplete"
                    self.st.event(f"{sym}: adopted/protected broker residual "
                                  "but entry/exit quantities do not reconcile")
                else:
                    unresolved.discard(sym)
                # the adoption ARMED a stop — the re-arm sweep below must
                # see it, or it stacks a duplicate (review 2026-07-19 #1)
                recovered[sym] = True
                self.st.event(f"{sym}: ADOPTED crash-window entry "
                              f"{a['qty']:.0f}x @ {px} (aggregated), stop "
                              f"armed {stop_px}")
                send_alert(f"🔧 {sym}: adopted crash-window entry "
                           f"{a['qty']:.0f}x @ {px} + stop {stop_px}")
        # A prior pass may already have adopted/protected the broker residual
        # while preserving the intent for missing exit provenance. Retry the
        # accounting half without placing or changing another stop.
        for sym, it in intents.items():
            if sym not in unresolved or not self.st.pos(sym).get("position"):
                continue
            result = self._book_recovered_intent_exits(
                sym, it, self.st.pos(sym)["qty"])
            if result != "unresolved":
                unresolved.discard(sym)
        # a 'filled' cancellation outcome is only resolved once the fill was
        # actually ADOPTED — otherwise the position is real and untracked
        for sym in fill_pending:
            if not self.st.pos(sym)["position"]:
                unresolved.add(sym)
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{sym} filled-during-cancel not yet adopted"
                self.st.event(f"{sym}: fill-during-cancel NOT adopted — "
                              "intent preserved, entries halted")
        # A verified no-fill intent must also become terminal in SQLite;
        # clearing JSON alone would make it reappear from pending_events on
        # the next process start.
        if db is not None:
            for sym, it in intents.items():
                if sym in unresolved:
                    continue
                event_key = it.get("event_key") or (
                    f"entry-intent:{getattr(self.ex, 'account', None)}:{sym}:"
                    f"{it.get('intent_id')}")
                status = db.event_status(event_key)
                if status and status != "COMMITTED":
                    db.update_event(
                        event_key, "RESOLVED",
                        payload={"resolution": "startup_no_open_position"})
        # clear only RESOLVED intents; unresolved ones are the recovery key
        self.st.state["order_intents"] = {
            s: it for s, it in intents.items() if s in unresolved}
        if (not unresolved and self.st.state.get("halt_entries") and
                "intent predates" in str(
                    self.st.state.get("halt_reason", ""))):
            self.st.state["halt_entries"] = False
            self.st.state["halt_reason"] = None
        acct = self.ex.positions()
        self.dlog.log("account", event="startup_reconcile",
                      venue=self.ex.venue, venue_positions=acct,
                      state_positions={s: p for s, p in
                                       self.st.state["positions"].items()
                                       if p.get("position")})
        # unmanaged positions in the account (manual trades, prior orphans):
        managed = {self.st.pos(s)["con_id"] for s in self.all_instruments
                   if self.st.pos(s)["position"]}
        for key, q in acct.items():
            if q and key not in managed:
                self.st.event(f"UNMANAGED POSITION in account: {key} qty={q} "
                              "— not touching it; verify manually")
                send_alert(f"⚠️ unmanaged position in trading account: {key} "
                           f"qty={q} — the bot will not touch it")
        for sym, icfg in self.all_instruments.items():
            p = self.st.pos(sym)
            held = acct.get(p["con_id"], 0) if p["con_id"] else 0
            if p["position"] and held >= p["qty"]:
                target_qty = int(held)
                if target_qty != int(p["qty"]):
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} broker holding {target_qty} != state {p['qty']}"
                    self.st.event(f"{sym}: startup found excess broker "
                                  f"quantity {target_qty} vs state {p['qty']}; "
                                  "protecting all exposure and halting")
                if sym in recovered and self.ex.has_working_stop(
                        sym, target_qty, con_id=p.get("con_id"),
                        stop_px=p.get("stop_px"),
                        px_tol=icfg["tick"] * 1e-6):
                    self.st.event(f"{sym}: recovered resting stop after restart")
                elif sym in recovered:
                    outcome = self.ex.cancel_stop(sym)
                    if outcome not in ("cancelled", "none"):
                        self.st.state["halt_entries"] = True
                        self.st.state["halt_reason"] = \
                            f"{sym} invalid recovered stop unresolved"
                        self.st.event(f"{sym}: recovered stop identity INVALID "
                                      f"and cancel={outcome}; halted")
                        continue
                    c = self.ex.contract_by_conid(p["con_id"])
                    oid = self.ex.place_stop(
                        sym, c, target_qty, p["stop_px"])
                    nxt = dict(p)
                    nxt["stop_order_id"] = oid
                    self._commit_position_transition(
                        self._transition_key(
                            "stop-recovery", sym,
                            suffix=f"startup-invalid:{p['con_id']}:{oid}"),
                        sym, "STOP_RECOVERY", nxt,
                        payload={"reason": "invalid_recovered_identity"})
                    p = self.st.pos(sym)
                    self.st.event(f"{sym}: invalid recovered stop replaced "
                                  f"with canonical stop @ {p['stop_px']}")
                elif sym in (getattr(self.ex,
                                     "unresolved_duplicate_stops", None)
                             or {}):
                    # r6 #3: an unresolved duplicate may still be live —
                    # arming another is the oversell generator
                    self.st.event(f"{sym}: NOT re-arming — unresolved "
                                  "duplicate stop at broker; halted")
                else:
                    self.st.event(f"{sym}: position but NO resting stop — re-arming")
                    c = self.ex.contract_by_conid(p["con_id"])
                    # Use broker quantity, including any positive excess whose
                    # entry basis is unresolved. Safety coverage is exact even
                    # while reconciliation keeps entries halted.
                    oid = self.ex.place_stop(
                        sym, c, target_qty, p["stop_px"])
                    nxt = dict(p)
                    nxt["stop_order_id"] = oid
                    self._commit_position_transition(
                        self._transition_key(
                            "stop-recovery", sym,
                            suffix=f"startup-missing:{p['con_id']}:{oid}"),
                        sym, "STOP_RECOVERY", nxt,
                        payload={"reason": "missing_startup_stop",
                                 "broker_qty": target_qty,
                                 "state_qty": p["qty"]})
                    p = self.st.pos(sym)
                    send_alert(f"⚠️ {sym}: stop re-armed after restart @ {p['stop_px']}")
            elif p["position"] and held < p["qty"]:
                # Broker quantity is authoritative.  Apply exact durable
                # executions when available; a price-estimated delta remains
                # visible and halts entries rather than pretending accounting
                # certainty.
                exited = p["qty"] - int(held)
                exact = (db.unapplied_executions(sym, side="SELL")
                         if db is not None else [])
                exact = [r for r in exact if r.get("con_id") == p["con_id"]]
                exact_qty = sum(r["qty"] for r in exact)
                from executor import Fill
                if exact and abs(exact_qty - exited) < 1e-9:
                    exit_px = sum(r["qty"] * r["px"] for r in exact) / exact_qty
                    fill = Fill(exit_px,
                                sum(float(r.get("commission") or 0)
                                    for r in exact) or None,
                                qty=exited,
                                exec_time=min((r.get("exec_time") for r in exact
                                               if r.get("exec_time")),
                                              default=None),
                                exec_ids=[r["exec_id"] for r in exact
                                          if r.get("exec_id")])
                    fill.exec_details = [
                        (r.get("exec_id"), r["qty"], r["px"],
                         r.get("commission")) for r in exact]
                    fill.order_key = f"recovery:{sym}:{p['qty']}->{int(held)}"
                    fill.filled_total = exited
                    reason = "offline_broker_execution"
                else:
                    # estimate basis: stop px when known, else entry px
                    # (r7 P0-1 bonus: stop_px can be None on estimated-basis
                    # adoptions — a TypeError here wedged startup)
                    est_px = (p["stop_px"] if p.get("stop_px") is not None
                              else p["entry_px"])
                    fill = Fill(float(est_px), None, qty=exited)
                    fill.order_key = f"recovery:{sym}:{p['qty']}->{int(held)}"
                    fill.filled_total = exited
                    reason = "offline_exit_estimated"
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{sym} offline exit lacks exact execution provenance"
                    self.st.event(f"{sym}: offline broker delta {exited} lacks "
                                  "exact execution rows; price estimated, halted")
                if held <= 0:
                    # the position is GONE but the GTC stop may survive —
                    # cancel it BEFORE booking (r7 P0-1: the nightly-reset
                    # incident's naked-short generator; booking must never
                    # stand between a flat book and a live sell stop)
                    outcome = self.ex.cancel_stop(sym)
                    if outcome == "unknown":
                        self.st.state["halt_entries"] = True
                        self.st.state["halt_reason"] = \
                            f"{sym} flat-book stop cancel unresolved"
                        send_alert(f"🆘 {sym}: broker flat but surviving "
                                   "stop cancel UNRESOLVED — manual check")
                    elif outcome == "cancelled":
                        self.st.event(f"{sym}: surviving flat-book stop "
                                      "cancelled before booking")
                self._book_stop_exit(sym, icfg, p, fill, reason=reason)
                if held > 0:
                    p2 = self.st.pos(sym)
                    if not self.ex.has_working_stop(
                            sym, int(held), con_id=p2.get("con_id"),
                            stop_px=p2.get("stop_px"),
                            px_tol=icfg["tick"] * 1e-6):
                        outcome = self.ex.cancel_stop(sym)
                        if outcome not in ("none", "cancelled"):
                            raise RuntimeError(
                                f"{sym}: offline residual stop cancel "
                                f"{outcome}; startup cannot prove protection")
                        c = self.ex.contract_by_conid(p2["con_id"])
                        oid = self.ex.place_stop(
                            sym, c, int(held), p2["stop_px"])
                        nxt = dict(p2)
                        nxt["stop_order_id"] = oid
                        self._commit_position_transition(
                            self._transition_key(
                                "stop-recovery", sym,
                                suffix=f"{p2['con_id']}:{oid}:{p2['qty']}"),
                            sym, "STOP_RECOVERY", nxt,
                            payload={"reason": "offline_residual"})
        if db is not None:
            for event in db.pending_events(["ROLL"]):
                p = self.st.pos(event["sym"])
                held = acct.get(p.get("con_id"), 0) if p.get("con_id") else 0
                if not p.get("position") and not held:
                    db.update_event(
                        event["event_key"], "RESOLVED",
                        payload={"resolution": "startup_broker_flat"})
                else:
                    self.st.state["halt_entries"] = True
                    self.st.state["halt_reason"] = \
                        f"{event['sym']} roll recovery pending"
                    self.st.event(f"{event['sym']}: roll event "
                                  f"{event['event_key']} remains pending; "
                                  "broker residual is protected and roll "
                                  "will retry on an open-market wake")
        if self.st.state["paper_start"] is None:
            self.st.state["paper_start"] = utcnow()
            self.st.event("PAPER PHASE STARTED")
            send_alert("🚀 paper phase started — MNQ+MES+MYM core, MGC sleeve, "
                       f"mode={self.st.state['mode']}")
        self.st.save()
        self._ensure_protection()
        self._startup_done = True

    def _book(self, sym: str) -> str:
        if sym in self.cfg.get("satellite_instruments", {}):
            return "satellite"
        return "sleeve" if sym in self.cfg["sleeve_instruments"] else "core"


    # ---------------- breadth satellite (frozen 2026-08-03) ------------
    def _satellite_flatten(self, sym: str, icfg: dict):
        """EOD close of a satellite lot — flat by the 16:00 ET wake."""
        p = self.st.pos(sym)
        contract = self.ex.contract_by_conid(p["con_id"])
        fill = self.ex.close_market(sym, contract, p["qty"])
        rq = min(int(fill.qty or 0), p["qty"])
        if rq <= 0:
            raise RuntimeError(f"{sym}: satellite EOD close returned zero")
        held_after = int(self.ex.positions().get(p["con_id"], 0))
        if held_after != 0:
            raise RuntimeError(
                f"{sym}: satellite EOD residual {held_after} — retrying")
        pnl = (fill.px - p["entry_px"]) * icfg["mult"] * rq
        allocate_entry = not p.get("entry_fees_allocated", False)
        entry_ids = (list(p.get("entry_exec_ids") or [])
                     if allocate_entry else [])
        manual_entry_fee = ((p.get("entry_commission") or 0)
                            if allocate_entry and not entry_ids else 0)
        trade = dict(
            exit_time=utcnow(), book=self._book(sym), sym=sym,
            trade_symbol=icfg["trade_symbol"], qty=rq,
            entry_time=p["entry_time"], entry_px=p["entry_px"],
            exit_px=fill.px, reason="eod",
            pnl_points=fill.px - p["entry_px"], pnl_usd=pnl,
            commission=((fill.commission or 0) + manual_entry_fee) or None,
            manual_commission=manual_entry_fee,
            entry_weight=p["entry_weight"],
            local_symbol=p["local_symbol"])
        from state_store import EMPTY_POS
        self._commit_position_transition(
            self._transition_key("sat-eod", sym, fill), sym,
            "SAT_EOD_EXIT", dict(EMPTY_POS), fill=fill, side="SELL",
            trade=trade, entry_exec_ids=entry_ids,
            exit_exec_ids=getattr(fill, "exec_ids", []),
            payload={"closed_qty": rq})
        send_alert(f"🛰 {sym} satellite EOD flat @ {fill.px} "
                   f"(${pnl:+,.2f} on {rq})")

    def process_satellite(self, now_utc: pd.Timestamp):
        """Entries + EOD flatten for the breadth satellite. Stops, trailing,
        stop-fill booking, rolls, recovery and reconcile for _S positions all
        run through the normal per-symbol machinery (they are registered in
        all_instruments); only the decision logic lives here."""
        scfg = self.cfg.get("satellite") or {}
        sats = self.cfg.get("satellite_instruments") or {}
        if not scfg.get("enabled") or not sats:
            return
        now_et = now_utc.tz_convert("America/New_York")
        hour = now_et.hour
        today = now_et.strftime("%Y-%m-%d")
        market_open_now = is_market_open(now_utc.to_pydatetime())
        # CME early-close days end the session before flat_et; the calendar
        # marks them closed from 12:00 ET, so flatten during the 11:xx wakes
        # (audit 2026-08-03 §5: a fixed 16:00 gate held lots through the
        # closure and flattened only after the evening reopen).
        early_close = today in CME_EARLY_CLOSE
        for sym, icfg in sats.items():
            p = self.st.pos(sym)
            # EOD flatten first — no satellite lot survives the session
            if p["position"] and (hour >= int(scfg["flat_et"])
                                  or (early_close and hour >= 11)):
                if market_open_now:
                    self._satellite_flatten(sym, icfg)
                continue
            if p["position"] or not market_open_now:
                continue
            # v2 (2026-08-04, exhaustion survivor): 15m signal bars, entries
            # allowed from channel formation until entry_cutoff_et; stop and
            # buffer stay anchored to HOURLY ATR (native 15m stops gave the
            # whole edge back in the sweep). Runs from the hourly wake AND
            # the 5-min subpoll; the last-signal-bar mark keeps re-checks
            # from re-evaluating the same completed bar.
            if not (int(scfg["session_start_et"]) <= hour
                    < int(scfg["entry_cutoff_et"])):
                continue
            if (self.st.state["halt_entries"] or not self._reconcile_ok
                    or (self.st.dir / "KILL_SWITCH").exists()):
                continue
            marks = self.st.state.setdefault("satellite_day", {})
            if marks.get(sym) == today:
                continue                     # one signal per session
            parent = icfg["parent"]
            rot = self.st.state.get("rotation") or {}
            w = rot.get("weights") or {}
            benched = (int(w.get(parent, 1)) == 0
                       and any(int(v) > 0 for v in w.values()))
            if not benched:
                continue
            core_on = any(self.st.pos(s)["position"]
                          for s in self.cfg["core_instruments"])
            if not core_on:
                continue
            bar_min = int(scfg.get("bar_minutes", 15))
            front = self.data.front_contract(
                icfg["trade_symbol"], icfg["exchange"],
                icfg.get("min_days_to_expiry", 0))
            # roll-session gate (audit §4): the research engine skips the
            # session on which the contract rolls; mirror it by watching
            # the front conId and blocking entries for that session.
            rollmarks = self.st.state.setdefault("satellite_front", {})
            rm = rollmarks.get(sym) or {}
            if rm.get("conid") and rm["conid"] != front.conId:
                rm = {"conid": front.conId, "roll_session": today}
                rollmarks[sym] = rm
                self.st.save()
                self.dlog.log("decisions", sym=sym,
                              event="satellite_roll_session", day=today)
            elif not rm.get("conid"):
                rollmarks[sym] = rm = {"conid": front.conId}
                self.st.save()
            if rm.get("roll_session") == today:
                continue
            N = self.data.hourly_bars(front, duration="2 D",
                                      bar_size=f"{bar_min} mins")
            if not len(N):
                continue
            N = N[N.index < now_utc.floor(f"{bar_min}min")]   # completed only
            if not len(N):
                continue
            net = N.index.tz_convert("America/New_York")
            sess = N[(net.strftime("%Y-%m-%d") == today)
                     & (net.hour >= int(scfg["session_start_et"]))
                     & (net.hour < int(scfg["flat_et"]))]
            if len(sess) < int(scfg["min_prior_bars"]) + 1:
                continue
            # freshness gate (audit §3): only act when the latest EXPECTED
            # completed bar is present — a lagging history response must not
            # let an old bar be traded as a live breakout.
            expected = (now_utc.floor(f"{bar_min}min")
                        - pd.Timedelta(minutes=bar_min))
            if sess.index[-1] != expected:
                continue
            sig_ts = sess.index[-1].isoformat()
            sigmarks = self.st.state.setdefault("satellite_sig_bar", {})
            if sigmarks.get(sym) == sig_ts:
                continue                     # this bar already evaluated
            sigmarks[sym] = sig_ts
            B = self.store.bars(parent)
            if not len(B):
                continue
            frame = strategy.signal_frame(B, self.scfg)
            atr_last = float(frame["atr"].iloc[-1])
            vol_ok = bool(frame["vol_ok"].iloc[-1])
            if pd.isna(atr_last) or not vol_ok:
                continue
            sess_high = float(sess["high"].iloc[:-1].max())
            sig_close = float(sess["close"].iloc[-1])
            if sig_close <= sess_high + float(scfg["buffer_atr"]) * atr_last:
                continue
            marks[sym] = today               # signal consumed for the session
            self.st.save()
            self.dlog.log("decisions", sym=sym, event="satellite_signal",
                          sig_close=sig_close, sess_high=sess_high,
                          atr=atr_last, parent=parent, bar=sig_ts)
            self._enter(sym, icfg, front, int(scfg["qty"]),
                        sig_close, atr_last, tag=" [satellite]")

    # ---------------- rotation ----------------------------------------
    def maybe_rebalance(self, now_utc: pd.Timestamp):
        rcfg = self.cfg["rotation"]
        this_mon = monday_of_week(now_utc)
        rot = self.st.state["rotation"]
        if rot and pd.Timestamp(rot["week"]) >= this_mon:
            return
        # rank on COMPLETED bars: refresh core series first, else the stored
        # final Sunday bar is a 25-second-old forming snapshot from the prior
        # wake and the d-1 close is wrong (review M2)
        refresh_failed = []
        for s, ic in self.cfg["core_instruments"].items():
            try:
                front = self.data.front_contract(
                    ic["trade_symbol"], ic["exchange"],
                    ic.get("min_days_to_expiry", 0))
                fresh = self.data.hourly_bars(front, duration="3 D")
                expected = now_utc.floor("h") - pd.Timedelta(hours=1)
                invalid = validate_hourly_refresh(fresh, expected)
                if invalid is None:
                    gaps = missing_expected_hourly_bars(
                        fresh.index, is_market_open)
                    if gaps:
                        invalid = (f"missing {len(gaps)} open-session bars; "
                                   f"first={gaps[0]}")
                if invalid is None:
                    fresh = fresh.assign(contract=front.localSymbol)
                    self.store.append(s, fresh, source="ibkr")
                else:
                    refresh_failed.append(s)
                    self.dlog.log("rotation", event="rebalance_refresh_invalid",
                                  sym=s, error=invalid)
            except Exception as e:
                refresh_failed.append(s)
                self.dlog.log("rotation", event="rebalance_refresh_failed",
                              sym=s, error=str(e)[:150])
        if refresh_failed:
            # r6 P1: never commit a rotation week on failed/uneven inputs —
            # retry next wake (the week stays uncommitted until clean)
            self.st.event(f"REBALANCE DEFERRED — refresh failed for "
                          f"{refresh_failed}")
            return
        closes = {s: self.store.daily_closes(s)
                  for s in self.cfg["core_instruments"]}
        input_errors = validate_rotation_inputs(
            closes, now_utc, rcfg["lookback_days"])
        if input_errors:
            self.st.event(f"REBALANCE DEFERRED — invalid/aligned-input gate: "
                          f"{input_errors}")
            self.dlog.log("rotation", event="rebalance_inputs_invalid",
                          errors=input_errors)
            return
        rets = trailing_returns(closes, now_utc, rcfg["lookback_days"])
        res = weights_for(rets, self.st.state["mode"],
                          rcfg["unsure_spread_pp"], rcfg["total_core_contracts"])
        res["week"] = this_mon.isoformat()
        res["computed_at"] = utcnow()
        res["lookback"] = rcfg["lookback_days"]
        self.st.state["rotation"] = res
        # shadow lookbacks: forward side-by-side record, observation only
        for sl in rcfg.get("shadow_lookbacks", []):
            s_rets = trailing_returns(closes, now_utc, sl)
            s_res = weights_for(s_rets, self.st.state["mode"],
                                rcfg["unsure_spread_pp"],
                                rcfg["total_core_contracts"])
            self.dlog.log("rotation", week=str(this_mon.date()), shadow=True,
                          lookback=sl, mode=s_res["mode"],
                          applied=s_res["applied"], ranking=s_res["ranking"],
                          returns=s_res["returns"],
                          spread_pp=s_res["spread_pp"], unsure=s_res["unsure"],
                          weights=s_res["weights"])
        self.dlog.log("rotation", week=str(this_mon.date()), mode=res["mode"],
                      lookback=rcfg["lookback_days"],
                      applied=res["applied"], ranking=res["ranking"],
                      returns=res["returns"], spread_pp=res["spread_pp"],
                      unsure=res["unsure"], weights=res["weights"],
                      inputs={s: {"n_days": int(len(c)),
                                  "last_date": str(c.index[-1].date()) if len(c) else None,
                                  "last_close": float(c.iloc[-1]) if len(c) else None}
                              for s, c in closes.items()})
        self.st.event(f"REBALANCE week={this_mon.date()} mode={res['mode']} "
                      f"applied={res['applied']} ranking={res['ranking']} "
                      f"spread={res['spread_pp']}pp weights={res['weights']}")
        send_alert(f"📊 weekly rotation: {res['applied']} "
                   f"{'/'.join(res['ranking'])} spread={res['spread_pp']}pp "
                   f"weights={res['weights']}")
        self.st.save()

    def weight(self, sym: str) -> int:
        if sym in self.cfg["sleeve_instruments"]:
            return int(self.cfg["sleeve_instruments"][sym]["qty"])
        rot = self.st.state["rotation"]
        if not rot:
            return 0
        return int(rot["weights"].get(sym, 0))

    # ---------------- per-instrument wake logic ------------------------
    def process_instrument(self, sym: str, icfg: dict, now_utc: pd.Timestamp):
        p = self.st.pos(sym)
        front = self.data.front_contract(
            icfg["trade_symbol"], icfg["exchange"],
            icfg.get("min_days_to_expiry", 0))

        # 1. bars — first live pull bridges the seed→live gap with a wide request
        cur = self.store.load(sym)
        has_live = len(cur) and (cur["source"] == "ibkr").any()
        new = self.data.hourly_bars(front, duration="3 D" if has_live else "1 W")
        if len(new):
            invalid = validate_hourly_frame(new)
            if invalid is None:
                gaps = missing_expected_hourly_bars(new.index, is_market_open)
                if gaps:
                    invalid = (f"missing {len(gaps)} open-session bars; "
                               f"first={gaps[0]}")
            if invalid:
                self._degraded.add(sym)
                self.st.event(f"{sym}: live bar refresh REJECTED — {invalid}")
                self.dlog.log("bars", event="refresh_rejected", sym=sym,
                              error=invalid)
            else:
                # stamp contract identity so roll seams are auditable in the
                # stored series (docs/ROLL_SPLICE_SPEC.md)
                new = new.assign(contract=front.localSymbol)
                self.store.append(sym, new, source="ibkr")
        B = self.store.bars(sym)
        if not len(B):
            return
        last_bar_ts = B.index[-1]
        self.st.state["last_bar"][sym] = last_bar_ts.isoformat()
        stored = self.store.load(sym)
        complete_stored = stored[stored.index < now_utc.floor("h")]
        live_index = complete_stored.loc[
            complete_stored["source"] == "ibkr"].index
        history_gaps = missing_expected_hourly_bars(
            live_index, is_market_open)
        gap_key = f"{sym}:history-gap"
        if history_gaps:
            self._degraded.add(sym)
            if gap_key not in self._stale_alerted:
                self._stale_alerted.add(gap_key)
                self.st.event(
                    f"{sym}: INTERNAL BAR GAP — {len(history_gaps)} "
                    f"open-session hour(s), first={history_gaps[0]}; "
                    "signals and trailing frozen until repaired")
                send_alert(
                    f"🚨 {sym}: hourly history has {len(history_gaps)} "
                    "open-session gap(s); signals/trailing frozen, broker "
                    "stop remains active")
            self.dlog.log("bars", event="internal_gap", sym=sym,
                          count=len(history_gaps),
                          first=str(history_gaps[0]))
        else:
            self._stale_alerted.discard(gap_key)

        # stale-feed guard: only stale if the market has ALSO been open long
        # enough that a fresh bar should exist (prevents the Sunday-18:00
        # false positive, where the last bar is legitimately Friday's)
        age_h = (now_utc - last_bar_ts).total_seconds() / 3600 - 1  # bar START ts
        npd = now_utc.to_pydatetime()
        market_long_open = (is_market_open(npd)
                            and is_market_open(npd - timedelta(hours=2)))
        stale = age_h > self.cfg["ops"]["stale_feed_hours"] and market_long_open
        if stale and sym not in self._stale_alerted:
            send_alert(f"⚠️ {sym}: stale feed — last bar {last_bar_ts} "
                       f"({age_h:.1f}h old). Entries blocked.")
            self._stale_alerted.add(sym)
        if not stale:
            self._stale_alerted.discard(sym)

        # 2. resting-stop fill?
        fill = self.ex.check_stop_filled(sym)
        if fill is not None and p["position"]:
            self._book_stop_exit(sym, icfg, p, fill)
            p = self.st.pos(sym)

        # 3. roll check (market order — defer to an open-market wake)
        if (p["position"] and p["con_id"] and front.conId != p["con_id"]
                and is_market_open(now_utc.to_pydatetime())):
            old = self.ex.contract_by_conid(p["con_id"])
            account = getattr(self.ex, "account", None) or "test"
            roll_key = (p.get("roll_event_key") or
                        f"roll:{account}:{sym}:{p['con_id']}->{front.conId}")
            db = getattr(self, "db", None)
            if db:
                db.begin_event(
                    roll_key, sym, "ROLL", status="ROLL_PENDING",
                    payload={"old_con_id": p["con_id"],
                             "new_con_id": front.conId,
                             "old_local_symbol": p["local_symbol"],
                             "new_local_symbol": front.localSymbol,
                             "requested_qty": p["qty"]})
            if not p.get("roll_event_key"):
                pending = dict(p)
                pending["roll_event_key"] = roll_key
                pending["fsm_state"] = "ROLL_PENDING"
                self._commit_position_transition(
                    f"{roll_key}:pending", sym, "ROLL_PENDING", pending,
                    payload={"roll_event_key": roll_key})
                p = self.st.pos(sym)
            fill = self.ex.close_market(sym, old, p["qty"])
            rq = min(int(fill.qty or 0), p["qty"])
            if rq <= 0:
                raise RuntimeError(f"{sym}: roll close returned zero quantity")
            try:
                held_after = int(self.ex.positions().get(p["con_id"], 0))
            except Exception as e:
                if db:
                    db.update_event(roll_key, "UNRESOLVED",
                                    payload={"error": str(e)[:200]})
                raise RuntimeError(
                    f"{sym}: roll fill occurred but residual broker quantity "
                    "is unverifiable") from e
            if held_after != p["qty"] - rq:
                if db:
                    db.update_event(
                        roll_key, "UNRESOLVED",
                        payload={"expected_residual": p["qty"] - rq,
                                 "broker_residual": held_after})
                raise RuntimeError(
                    f"{sym}: roll residual mismatch expected "
                    f"{p['qty'] - rq}, broker {held_after}")
            pnl = (fill.px - p["entry_px"]) * icfg["mult"] * rq
            allocate_entry = not p.get("entry_fees_allocated", False)
            entry_ids = (list(p.get("entry_exec_ids") or [])
                         if allocate_entry else [])
            manual_entry_fee = ((p.get("entry_commission") or 0)
                                if allocate_entry and not entry_ids else 0)
            trade = dict(
                exit_time=utcnow(), book=self._book(sym), sym=sym,
                trade_symbol=icfg["trade_symbol"], qty=rq,
                entry_time=p["entry_time"], entry_px=p["entry_px"],
                exit_px=fill.px, reason="roll",
                pnl_points=fill.px - p["entry_px"], pnl_usd=pnl,
                commission=((fill.commission or 0) + manual_entry_fee) or None,
                manual_commission=manual_entry_fee,
                entry_weight=p["entry_weight"],
                local_symbol=p["local_symbol"])
            if held_after > 0:
                # close_market cancel-verified the old stop.  A partial roll
                # must re-protect the broker-confirmed residual before state is
                # advanced or the wake returns.
                oid = self.ex.place_stop(
                    sym, old, held_after, p["stop_px"])
                nxt = dict(p)
                nxt.update(qty=held_after, stop_order_id=oid,
                           basis_qty=max(0, int(p.get("basis_qty")
                                                if p.get("basis_qty") is not None
                                                else p["qty"]) - rq),
                           entry_fees_allocated=(
                               p.get("entry_fees_allocated", False)
                               or allocate_entry),
                           roll_event_key=roll_key,
                           fsm_state="ROLL_RESIDUAL_PROTECTED")
            else:
                from state_store import EMPTY_POS
                nxt = dict(EMPTY_POS)
            child_key = self._transition_key(
                "roll-exit", sym, fill, suffix=roll_key)
            inserted = self._commit_position_transition(
                child_key, sym, "ROLL_EXIT", nxt, fill=fill, side="SELL",
                trade=trade, entry_exec_ids=entry_ids,
                exit_exec_ids=getattr(fill, "exec_ids", []),
                payload={"roll_event_key": roll_key,
                         "closed_qty": rq, "broker_residual": held_after})
            if db:
                db.update_event(
                    roll_key, "RESIDUAL" if held_after else "RESOLVED",
                    payload={"last_fill_event": child_key,
                             "broker_residual": held_after})
            if held_after:
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = f"{sym} partial roll close"
                if inserted:
                    self.st.event(f"{sym}: ROLL CLOSE PARTIAL {rq}/"
                                  f"{rq + held_after}; {held_after} remains "
                                  "broker-confirmed and protected")
                    send_alert(f"🚨 {sym}: roll close PARTIAL {rq}/"
                               f"{rq + held_after}; {held_after} protected, "
                               "retrying roll next wake")
            elif inserted:
                send_alert(f"🔄 {sym} roll force-close @ {fill.px} "
                           f"pnl=${pnl:+,.2f}; new front "
                           f"{front.localSymbol}")
            p = self.st.pos(sym)

        # 4. trail update from the just-closed bar
        if p["position"]:
            if history_gaps:
                # A gap corrupts ATR/high-water inputs. Keep the last
                # broker-held stop unchanged; do not manufacture a tighter or
                # looser trail from an incomplete series.
                return
            a = strategy.atr(B, self.scfg["atr_n"])
            atr_last = float(a.iloc[-1])
            if p["con_id"] and front.conId != p["con_id"]:
                # ROLL PENDING (r6 #7): the stored series already carries
                # new-contract bars — trailing the OLD contract's stop from
                # them corrupts the price basis. Freeze until the roll runs.
                self.st.event(f"{sym}: trail FROZEN — roll pending "
                              f"({p['local_symbol']} -> "
                              f"{front.localSymbol})")
                self.dlog.log("stops", event="trail_frozen_roll", sym=sym)
                return
            # high-water = max high since entry over ALL stored bars — after
            # an outage the intermediate bars' highs must count, else the
            # trail sits looser than the reference engine (review M3)
            try:
                since = B[B.index >= pd.Timestamp(
                    p["entry_time"]).floor("h")]   # entry bar INCLUDED
                bar_hw = float(since["high"].max()) if len(since) \
                    else float(B["high"].iloc[-1])
            except Exception:
                bar_hw = float(B["high"].iloc[-1])
            hw = max(p["hw"], bar_hw)
            new_stop = strategy.trail_stop(hw, atr_last, self.scfg["k_trail"],
                                           p["stop_px"])
            new_stop = strategy.floor_to_tick(new_stop, icfg["tick"])
            nxt = dict(p)
            nxt["hw"] = hw
            if new_stop > p["stop_px"]:
                oid = self.ex.update_stop(sym, new_stop, reason="trail",
                                          hw=hw, atr=atr_last)
                nxt["stop_px"] = new_stop
                if oid is not None:
                    nxt["stop_order_id"] = oid
                transition = "STOP_TRAIL"
            else:
                transition = "POSITION_MARK"
            # key must carry the transition type: a restart within the same
            # bar can journal STOP_TRAIL then re-derive the identical
            # (bar, stop) as POSITION_MARK — same key, different type, and
            # the idempotency guard then errors EVERY wake until a new bar
            # changes the key (2026-08-03 incident: 5-min retries collided
            # for an hour after the YM adoption recovery).
            self._commit_position_transition(
                self._transition_key(
                    "position-mark", sym,
                    suffix=f"{last_bar_ts.isoformat()}:{new_stop}:{transition}"),
                sym, transition, nxt,
                payload={"bar": last_bar_ts.isoformat(), "hw": hw,
                         "stop_px": nxt["stop_px"], "atr": atr_last})
            p = self.st.pos(sym)

        # 6. entry signal — only on a FRESH bar, decided at most once.
        # A wake with no new completed bar is a trading failure for this
        # instrument (review #5): no signal on stale data, wake marked
        # degraded, 5-min catch-up retries until a fresh bar arrives.
        bar_key = str(last_bar_ts)
        if self.st.state["last_decision_bar"].get(sym) == bar_key:
            return                       # this bar is already decided
        if history_gaps:
            return                       # incomplete indicator history
        expected = (now_utc.floor("h") - timedelta(hours=1))
        if last_bar_ts < expected and market_long_open:
            self._degraded.add(sym)
            self.dlog.log("wakes", event="no_fresh_bar", sym=sym,
                          last_bar=bar_key, expected=str(expected))
            return                       # no decision on a stale bar
        self._degraded.discard(sym)
        # a FRESH bar decision supersedes any retry queued from the prior
        # bar (catch-up wakes that dedup-skip must NOT wipe the queue)
        self.st.state["pending_entries"].pop(sym, None)
        sig = strategy.signal_on_last_bar(B, self.scfg)
        # volume-surge conviction stamp (lead #3, observation only)
        try:
            volb = self.store.load(sym)["volume"]
            volb = volb[volb.index < pd.Timestamp.now(tz="UTC").floor("h")]
            vol_ratio = float(volb.iloc[-1] / volb.tail(21).iloc[:-1].mean())
        except Exception:
            vol_ratio = None
        w = self.weight(sym)
        acted = False
        note = ""
        if sig.get("signal") and not p["position"]:
            if not is_market_open(now_utc.to_pydatetime()):
                note = "market_closed"
            elif self.st.state["halt_entries"]:
                note = f"halted:{self.st.state['halt_reason']}"
            elif not self._reconcile_ok:
                note = "reconcile_unverified"   # fail closed: no broker
                                                # truth, no new risk
            elif stale:
                note = "stale_feed"
            elif w <= 0:
                note = "weight_0"
            else:
                try:
                    self._enter(sym, icfg, front, w, sig["close"], sig["atr"])
                    acted = True
                except Exception as e:
                    note = f"entry_failed:{e}"
                    self._entry_failure(sym, e)
                    # a decided trade must not die to plumbing: queue it and
                    # keep retrying on the 5-min subpoll while price still
                    # holds above the breakout level (2026-07-14: 3 signals
                    # lost to a half-dead gateway + hourly-only cadence)
                    self.st.state["pending_entries"][sym] = {
                        "level": sig["donchian"], "close": sig["close"],
                        "atr": sig["atr"], "weight": w,
                        "bar": str(sig.get("bar")), "created": utcnow()}
                    self.st.event(f"{sym}: entry queued — retrying every "
                                  f"5min while price > {sig['donchian']}")

        self.st.record_decision(
            ts=utcnow(), sym=sym, bar=sig.get("bar"), signal=sig.get("signal"),
            acted=acted, breakout=sig.get("breakout"), vol_ok=sig.get("vol_ok"),
            session_ok=sig.get("session_ok"), close=sig.get("close"),
            donchian=sig.get("donchian"), atr=sig.get("atr"),
            atr_pct=sig.get("atr_pct"), vol_thresh=sig.get("vol_thresh"),
            weight=w, note=note, vix=getattr(self, "_vix", None),
            vix_regime=vix_regime(getattr(self, "_vix", None)),
            oi_chg5=getattr(self, "_oi", {}).get(sym, {}).get("oi_chg5"),
            oi_state=getattr(self, "_oi", {}).get(sym, {}).get("oi_state"),
            vol_ratio=vol_ratio)
        self.st.state["last_decision_bar"][sym] = bar_key
        self.dlog.log("decisions", sym=sym, weight=w, acted=acted, note=note,
                      stale=stale, position=p["position"],
                      vix=getattr(self, "_vix", None),
                      vix_regime=vix_regime(getattr(self, "_vix", None)),
                      oi=getattr(self, "_oi", {}).get(sym), **sig)

    # ---------------- equity mark --------------------------------------
    def mark_equity(self):
        closed = {"core": 0.0, "sleeve": 0.0}
        db = getattr(self, "db", None)
        if db is not None:
            for book, value in db.con.execute(
                    "SELECT book,COALESCE(SUM(calculated_net),0) "
                    "FROM v_trade_net GROUP BY book"):
                if book in closed:
                    closed[book] = float(value)
        else:
            tpath = self.st.dir / "trades.csv"
            if tpath.exists():
                t = pd.read_csv(tpath)
                for book, g in t.groupby("book"):
                    closed[book] = float(g["pnl_usd"].sum())
        for book in ("core", "sleeve"):
            open_pnl = 0.0
            for sym, icfg in self.all_instruments.items():
                if self._book(sym) != book:
                    continue
                p = self.st.pos(sym)
                if p["position"]:
                    B = self.store.bars(sym, tail=1)
                    if len(B):
                        open_pnl += (float(B["close"].iloc[-1]) - p["entry_px"]) \
                            * icfg["mult"] * p["qty"]
                    if not p.get("entry_fees_allocated", False):
                        open_pnl -= float(p.get("entry_commission") or 0)
            self.st.record_equity(ts=utcnow(), book=book,
                                  open_pnl_usd=round(open_pnl, 2),
                                  closed_pnl_usd=round(closed[book], 2),
                                  total_usd=round(open_pnl + closed[book], 2))

    # ---------------- wake ---------------------------------------------
    def wake(self, scheduled: datetime | None = None):
        t0 = time.monotonic()
        # Readiness is a per-wake verdict. Persistent safety conditions live
        # in halt_entries/FSM/readiness rows; a recovered transient must not
        # force five-minute full wakes forever merely because this set was
        # never cleared.
        self._degraded = set()
        now = pd.Timestamp.now(tz="UTC")
        drift_s = ((now.to_pydatetime() - scheduled).total_seconds()
                   if scheduled else None)
        self.st.state["last_wake"] = utcnow()
        self.st.heartbeat()
        self._check_code_drift()     # BEFORE connectivity — drift must be
                                     # visible during outages too (r5 #7)
        def _leg_fail(leg: str, e: Exception) -> int:
            self._consec_errors[leg] += 1
            n = self._consec_errors[leg]
            port = "4001" if leg == "data" else "4002"
            self.st.event(f"CONNECT FAILED (#{n}) [{leg}({port})]: {e}")
            self.dlog.log("connections", event="connect_failed", leg=leg,
                          error=str(e)[:200], consec=n)
            if n in (1, 6) or n % 12 == 0:
                send_alert(f"🚨 cannot reach {leg}({port}) (#{n}): {e} — "
                           "retrying every 5min; resting stops still "
                           "guard all positions")
            if n == 3:
                if leg == "executor" and self.ex.venue == "ibkr_paper":
                    self._heal_gateway("connect")
                elif leg == "data":
                    send_alert("🔑 live DATA gateway (4001) is down and "
                               "needs YOUR login (auto-heal covers only "
                               "the paper leg) — bot fails safe meanwhile")
            return n
        # EXECUTOR (broker-safety leg) connects FIRST and INDEPENDENTLY —
        # a market-data outage must never disable stop reconciliation,
        # protection audits, or the kill switch (review r6 #6)
        ex_ok = data_ok = True
        reconnected = False
        try:
            reconnected = self.ex.connect()
            self._consec_errors["executor"] = 0
        except Exception as e:
            ex_ok = False
            _leg_fail("executor", e)
        if ex_ok:
            try:
                self._bind_runtime_identity()
                self._consec_errors["state"] = 0
            except Exception as e:
                # A journal/identity failure is not an executor outage. Do not
                # kill a healthy gateway in response to a local state fault.
                n = self._consec_errors.get("state", 0) + 1
                self._consec_errors["state"] = n
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    "durable runtime identity unavailable"
                self.st.state["last_wake_status"] = \
                    f"FAILED: durable identity/FSM bind failed (#{n}): {e}"
                self._set_readiness("accounting", "FAILED", str(e)[:200])
                self._set_readiness("broker", "OK",
                                    "executor connected; state bind failed")
                self.st.event(f"DURABLE STATE BIND FAILED (#{n}): {e}")
                if n in (1, 6) or n % 12 == 0:
                    send_alert(f"🆘 durable state/identity bind failed (#{n}): "
                               f"{e} — gateway left untouched; no trading")
                self.st.save()
                self._wake_failed = True
                return
        try:
            self.data.connect()
            self._consec_errors["data"] = 0
        except Exception as e:
            data_ok = False
            _leg_fail("data", e)
        if not ex_ok:
            self._set_readiness("broker", "FAILED", "executor unreachable")
            self.st.state["last_wake_status"] = \
                f"FAILED: executor(4002) unreachable " \
                f"(#{self._consec_errors['executor']})"
            self.dlog.log("wakes", event="wake_failed", leg="executor")
            self.st.save()
            self._wake_failed = True
            return
        self._set_readiness("broker", "OK", "executor connected and scoped")
        self._set_readiness(
            "data", "OK" if data_ok else "FAILED",
            "connected" if data_ok else "data(4001) unreachable")
        if reconnected and self._startup_done:
            # A rebuilt trade session invalidates every in-memory order state,
            # not only stop handles.  Force startup's entry/execution/position
            # recovery before any strategy work.
            self._startup_done = False
            self.st.event("executor reconnected — full broker-event reconcile "
                          "required before strategy work")
        # kill switch FIRST — before any instrument can act (review #1:
        # it used to be checked only after entries had already happened)
        self.check_kill_switch()
        if not self._startup_done:
            self.startup_reconcile()
        if not data_ok:
            # SAFETY-ONLY wake: broker leg is up, data leg is not. Run
            # every executor-side safety operation, skip signals/trails.
            for sym, icfg in self.all_instruments.items():
                p = self.st.pos(sym)
                if p["position"]:
                    fill = self.ex.check_stop_filled(sym)
                    if fill is not None:
                        self._book_stop_exit(sym, icfg, p, fill)
            self.reconcile_positions()
            self._risk_alerts()
            db0 = getattr(self, "db", None)
            accounting_ok = True
            if db0:
                try:
                    db0.snapshot_positions(self.st.state["positions"])
                    self._set_readiness("accounting", "OK",
                                        "position snapshot committed")
                except Exception as e:
                    accounting_ok = False
                    self._degraded.add("journal")
                    self._set_readiness("accounting", "FAILED", str(e)[:200])
                    self.st.event(f"DB snapshot write FAILED in safety wake: {e}")
            prot_status, prot_detail = self._protection_readiness()
            self._set_readiness("protection", prot_status, prot_detail)
            rec_status = ("FAILED" if "reconciliation" in self._degraded
                          else "OK")
            self._set_readiness("reconciliation", rec_status,
                                "broker/state audit completed" if rec_status == "OK"
                                else "broker/state audit failed")
            self.st.state["last_wake_status"] = \
                f"DEGRADED: data(4001) down (#{self._consec_errors['data']})" \
                f"; broker=OK protection={prot_status} " \
                f"reconciliation={rec_status} accounting=" \
                f"{'OK' if accounting_ok else 'FAILED'}"
            self.st.save()
            self._wake_failed = True     # keep retrying the data leg
            return
        # PRE-TRADE reconcile (fail closed): entries this wake require broker
        # truth to match state BEFORE any signal is acted on. Errors count as
        # mismatch — no truth, no new risk. Stops/trails always run.
        try:
            self._reconcile_ok = not self._position_diff()
        except Exception as e:
            self._reconcile_ok = False
            self.dlog.log("account", event="pretrade_reconcile_error",
                          error=str(e)[:200])
        if not self._reconcile_ok:
            self.st.event("pre-trade reconcile NOT CLEAN — entries disabled "
                          "this wake (stops/trails unaffected)")
            self._set_readiness("reconciliation", "FAILED",
                                "pre-trade broker/state diff not clean")
        else:
            self._set_readiness("reconciliation", "OK",
                                "pre-trade broker/state diff clean")
        # observation-only regime logging (PREREG_REGIME_FORWARD.md +
        # PREREG_OI_FORWARD.md — forward trials 1 & 2)
        v = self.data.vix_snapshot()
        if v is None:                       # CBOE index feed closed (overnight)
            v = self.st.state.get("vix")    # prior-day/last-known — matches
        self._vix = v                       # the prereg's prior-close basis
        from oi_state import get_states
        self._oi = get_states()
        self.st.state["vix"] = self._vix
        self.st.state["vix_regime"] = vix_regime(self._vix)
        self.maybe_rebalance(now)
        timings = {}
        for sym, icfg in self.all_instruments.items():
            t1 = time.monotonic()
            try:
                self.process_instrument(sym, icfg, now)
            except Exception as e:
                self.st.event(f"{sym} WAKE ERROR: {e}")
                log.exception("%s wake error", sym)
                self.dlog.log("wakes", event="instrument_error", sym=sym,
                              error=str(e)[:300])
                self._degraded.add(sym)   # an errored instrument is NOT a
                                          # green check (review finding)
            timings[sym] = round((time.monotonic() - t1) * 1000)
        try:
            self.process_satellite(now)
        except Exception as e:
            self.st.event(f"SATELLITE WAKE ERROR: {e}")
            log.exception("satellite wake error")
            self.dlog.log("wakes", event="instrument_error", sym="satellite",
                          error=str(e)[:300])
            self._degraded.add("satellite")
        self.reconcile_positions()
        self._risk_alerts()
        self.mark_equity()
        db = getattr(self, "db", None)
        if db:
            try:
                db.snapshot_positions(self.st.state["positions"])
                self._set_readiness("accounting", "OK",
                                    "position snapshot committed")
            except Exception as e:
                # never silent (23:00Z 07-17: swallowed error hid a failed
                # write) — the journal is authoritative, failures are events
                self.st.event(f"DB snapshot write FAILED: {e!r}")
                self.dlog.log("account", event="db_snapshot_error",
                              error=repr(e)[:200])
                self._degraded.add("journal")   # failed journal ≠ green wake
                self._set_readiness("accounting", "FAILED", repr(e)[:200])
        prot_status, prot_detail = self._protection_readiness()
        self._set_readiness("protection", prot_status, prot_detail)
        if prot_status != "OK":
            self._degraded.add("protection")
        self._recover_protection_halt()
        if self._degraded:
            # missing fresh bars = trading failure, not a green check:
            # degraded status + 5-min catch-up retries until bars arrive
            self.st.state["last_wake_status"] = \
                f"DEGRADED: no fresh bar for {sorted(self._degraded)}"
            self._wake_failed = True
        else:
            self.st.state["last_wake_status"] = "ok"
            if self._wake_failed:
                self.st.event("catch-up wake succeeded — recovered "
                              "within the hour")
                self._wake_failed = False
        self.st.save()
        self.dlog.log("wakes", event="wake", drift_s=drift_s,
                      total_ms=round((time.monotonic() - t0) * 1000),
                      instrument_ms=timings,
                      market_open=is_market_open(now.to_pydatetime()))

    def _enter(self, sym: str, icfg: dict, front, w: int,
               sig_close: float, sig_atr: float, tag: str = ""):
        """Place entry + arm stop + persist state (shared by the hourly wake
        and the pending-entry retry path). Stop basis is always the ORIGINAL
        signal bar's close/ATR, re-based only UP on the actual fill."""
        # operator kill switch beats everything — including retries
        if (self.st.dir / "KILL_SWITCH").exists():
            raise RuntimeError("KILL_SWITCH engaged — entry refused")
        # code-enforced live canary: any non-paper venue trades 1 contract,
        # period — the staged scale-up is a config change AFTER canary data
        if self.ex.venue != "ibkr_paper" and w > 1:
            self.st.event(f"{sym}: CANARY CLAMP {w} -> 1 (venue "
                          f"{self.ex.venue})")
            w = 1
        # persist entry INTENT (with a UNIQUE id, embedded in the broker
        # orderRef) before submission — crash recovery correlates exactly,
        # never adopts an older same-symbol execution
        import uuid
        # 48 random bits keeps the broker orderRef at 30 characters while
        # making same-symbol crash-correlation collisions negligible over the
        # lifetime of the strategy (the prior 32-bit truncation was too small
        # for a durable identity).
        intent_id = uuid.uuid4().hex[:12]
        account = getattr(self.ex, "account", None) or "test"
        event_key = f"entry-intent:{account}:{sym}:{intent_id}"
        intent = {
            "intent_id": intent_id, "qty": w,
            "local_symbol": front.localSymbol,
            "con_id": front.conId, "sig_close": sig_close,
            "sig_atr": sig_atr, "created": utcnow(),
            "event_key": event_key}
        db = getattr(self, "db", None)
        if db:
            try:
                db.begin_event(event_key, sym, "ENTRY_INTENT", payload=intent,
                               order_ref=f"trendbot:{sym}:entry:{intent_id}")
                db.order_event(sym, "entry", "intent", event_key=event_key,
                               qty=w, note=f"{front.localSymbol}:{intent_id}")
            except Exception as e:
                # No broker submission has happened. Leave a terminal marker
                # when begin_event succeeded, halt, and never trade around a
                # failed commit barrier.
                try:
                    if db.event_status(event_key):
                        db.update_event(
                            event_key, "CANCELLED",
                            payload={"resolution":
                                     "journal_failure_before_submission"})
                except Exception:
                    pass
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = "entry intent journal failed"
                self.st.save()
                raise RuntimeError(
                    f"{sym}: durable entry intent failed before submission") \
                    from e
        self.st.state["order_intents"][sym] = intent
        try:
            self.st.save()
        except Exception as e:
            self.st.state["order_intents"].pop(sym, None)
            if db:
                try:
                    db.update_event(
                        event_key, "CANCELLED",
                        payload={"resolution":
                                 "projection_failure_before_submission"})
                except Exception:
                    pass
            raise RuntimeError(
                f"{sym}: intent projection failed before submission") from e
        try:
            prov_stop = strategy.floor_to_tick(
                strategy.initial_stop(sig_close, sig_atr, self.scfg["k_trail"]),
                icfg["tick"])
            fill, oid = self.ex.enter_long_with_stop(
                sym, front, w, prov_stop, intent_id=intent_id)
        except TimeoutError:
            # TimeoutError is reserved for a verified terminal cancel with
            # no position. Generic RuntimeError is not broker proof.
            self.st.state["order_intents"].pop(sym, None)
            if db:
                db.update_event(event_key, "CANCELLED",
                                payload={"resolution": "verified_terminal"})
            self.st.save()
            raise
        except Exception as e:
            # UNVERIFIED failure (connection dropped mid-flight, etc.) —
            # keep the intent journaled: an order may be live at the broker
            # and startup recovery must find it (review 2026-07-18 #4)
            self.st.event(f"{sym}: entry failed UNVERIFIED — intent "
                          f"{intent_id} kept for crash recovery")
            from executor import UnresolvedEntry
            if isinstance(e, UnresolvedEntry):
                raise
            raise UnresolvedEntry(
                f"{sym}: entry result unverified; intent {intent_id} "
                "preserved for startup reconciliation") from e
        # A protection stop may have round-tripped part (or all) of the entry.
        # Entry executions, protection executions, closed-trade accounting and
        # NET position are one durable transition.
        trade = None
        exit_details = list(getattr(fill, "partial_exit_details", []) or [])
        exit_ids = [d[0] for d in exit_details if d[0]]
        if getattr(fill, "partial_exit", None):
            x_qty, x_px = fill.partial_exit
            known_comm = sum(float(d[3] or 0) for d in exit_details) + \
                float(fill.commission or 0)
            trade = dict(
                exit_time=utcnow(), book=self._book(sym), sym=sym,
                trade_symbol=icfg["trade_symbol"], qty=int(x_qty),
                entry_time=utcnow(), entry_px=fill.px, exit_px=x_px,
                reason="stop_during_entry",
                pnl_points=x_px - fill.px,
                pnl_usd=(x_px - fill.px) * icfg["mult"] * x_qty,
                commission=known_comm or None, manual_commission=0,
                entry_weight=w,
                local_symbol=front.localSymbol)
        qty = int(fill.qty)
        if qty:
            if oid is None:
                from executor import UnresolvedEntry
                self._startup_done = False
                raise UnresolvedEntry(
                    f"{sym}: broker position exists without a confirmed stop "
                    "handle; intent preserved for immediate recovery")
            true_stop = strategy.floor_to_tick(
                strategy.initial_stop(fill.px, sig_atr, self.scfg["k_trail"]),
                icfg["tick"])
            if true_stop > prov_stop:
                try:
                    self.ex.update_stop(sym, true_stop, reason="rebase",
                                        atr=sig_atr)
                except Exception as e:
                    # A broker position and some stop may both exist, but the
                    # exact trigger identity is unresolved. Do not project a
                    # confident HELD_PROTECTED state; startup will re-read and
                    # canonicalize the broker order before any new entry.
                    from executor import UnresolvedEntry
                    self._startup_done = False
                    send_alert(f"🆘 {sym}: entry filled but stop rebase was "
                               f"unresolved ({e}); forcing broker recovery")
                    raise UnresolvedEntry(
                        f"{sym}: filled entry stop rebase unresolved") from e
            stop_px = max(true_stop, prov_stop)
            nxt = dict(self.st.pos(sym))
            nxt.update(position=1, qty=qty, entry_px=fill.px,
                       entry_time=utcnow(), hw=fill.px, stop_px=stop_px,
                       sig_atr=sig_atr, con_id=front.conId,
                       local_symbol=front.localSymbol, stop_order_id=oid,
                       entry_weight=w, entry_commission=fill.commission,
                       entry_exec_ids=list(fill.exec_ids),
                       entry_fees_allocated=bool(trade),
                       basis_quality="broker_execution", basis_qty=qty)
        else:
            stop_px = None
            nxt = dict(self.st.pos(sym))
            nxt.update({k: v for k, v in {
                "position": 0, "qty": 0, "entry_px": None,
                "entry_time": None, "hw": None, "stop_px": None,
                "sig_atr": None, "con_id": None, "local_symbol": None,
                "stop_order_id": None, "entry_weight": None,
                "entry_commission": None, "entry_exec_ids": [],
                "entry_fees_allocated": False}.items()})
        executions = self._execution_rows(
            fill, "BUY", local_symbol=front.localSymbol, con_id=front.conId)
        executions.extend([
            dict(side="SELL", qty=float(q), px=float(px), exec_id=xid,
                 commission=comm, local_symbol=front.localSymbol,
                 con_id=front.conId)
            for xid, q, px, comm in exit_details])
        try:
            self._commit_position_transition(
                event_key, sym, "ENTRY_FILLED", nxt, trade=trade,
                entry_exec_ids=fill.exec_ids, exit_exec_ids=exit_ids,
                payload={"intent_id": intent_id, "net_qty": qty,
                         "stop_order_id": oid}, executions=executions)
        except Exception as e:
            # Broker fill + protection happened, but local authority did not
            # advance. Force full intent/execution adoption on the next
            # catch-up wake; a plain position diff cannot recover entry basis.
            from executor import UnresolvedEntry
            self._startup_done = False
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = f"{sym} entry commit failed"
            raise UnresolvedEntry(
                f"{sym}: broker entry protected but FSM commit failed; "
                "intent preserved for recovery") from e
        self.st.state["pending_entries"].pop(sym, None)
        self.st.state["order_intents"].pop(sym, None)
        self.st.save()
        if qty == 0:
            send_alert(f"ℹ️ {sym}: entry round-tripped via its protection "
                       "stop during the fill — flat")
            return
        if db:
            try:
                db.order_event(sym, "entry",
                               "filled" if qty == w else "partial",
                               event_key=event_key,
                               order_id=fill.order_id, qty=qty, px=fill.px,
                               note=f"stop_order_id={oid}")
            except Exception as e:
                # The authoritative entry/FSM transaction is already
                # committed. An auxiliary audit-row failure must degrade the
                # wake, not tell the caller the broker entry failed and queue
                # a second attempt.
                self._degraded.add("order_audit")
                self.st.event(f"{sym}: entry committed but auxiliary order "
                              f"event write failed: {e}")
                self._set_readiness("accounting", "FAILED", str(e)[:200])
        send_alert(f"✅ {sym} LONG {qty}x {front.localSymbol} @ {fill.px} "
                   f"stop {stop_px} (ATR {sig_atr:.2f})"
                   f"{' PARTIAL of ' + str(w) if qty != w else ''}{tag}")

    def _entry_failure(self, sym: str, e: Exception):
        self.st.event(f"{sym} ENTRY FAILED: {e}")
        send_alert(f"🚨 {sym} entry failed: {e}")
        from executor import UnresolvedEntry
        if isinstance(e, UnresolvedEntry):
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = f"{sym} entry state unresolved"
            self.st.event(f"{sym}: UNRESOLVED entry — entries halted until "
                          "recovery verifies broker state")
            self._startup_done = False
        if "UNROUTED" in str(e):
            self._heal_gateway(sym)

    def _retry_pending_entries(self):
        """Subpoll hook: retry queued entries while the breakout condition
        still holds. A pending entry dies when (a) it fills, (b) price falls
        back to/below the breakout level, or (c) the next successful hourly
        wake clears the queue and re-decides from the fresh closed bar."""
        pend = self.st.state.get("pending_entries") or {}
        for sym, pe in list(pend.items()):
            icfg = self.all_instruments.get(sym)
            p = self.st.pos(sym)
            if icfg is None or p["position"]:
                pend.pop(sym, None)
                continue
            # time bound: retries exist to survive plumbing failures (self-
            # heal ≈2-3min), not to chase price. 15min ≈ 2-3 subpoll attempts
            # covering a full gateway self-heal + one spare, while keeping
            # any retry fill close to the signal price the backtest assumes.
            try:
                age_s = (datetime.now(timezone.utc)
                         - datetime.fromisoformat(pe["created"])).total_seconds()
            except Exception:
                age_s = 1e9
            if age_s > 900:
                pend.pop(sym, None)
                self.st.event(f"{sym}: entry retry window expired (15min) — "
                              "next hourly bar re-decides")
                self.st.save()
                continue
            if self.st.state["halt_entries"]:
                return
            # fresh fail-closed reconcile before EVERY retry (review: the
            # retry path skipped the broker-truth gate the wake enforces)
            try:
                if self._position_diff():
                    return
            except Exception:
                return               # no broker truth, no new risk
            try:
                front = self.data.front_contract(
                    icfg["trade_symbol"], icfg["exchange"],
                    icfg.get("min_days_to_expiry", 0))
                raw = self.data.hourly_bars(front, duration="1 D")
                cur_px = float(raw["close"].iloc[-1])
            except Exception as e:
                self.dlog.log("orders", event="retry_price_check_failed",
                              sym=sym, error=str(e)[:150])
                continue
            if cur_px <= pe["level"]:
                pend.pop(sym, None)
                self.st.event(f"{sym}: entry retry abandoned — price {cur_px} "
                              f"back inside channel (level {pe['level']})")
                self.st.save()
                continue
            self.dlog.log("orders", event="entry_retry", sym=sym, px=cur_px,
                          level=pe["level"],
                          drift_pts=round(cur_px - pe["close"], 2))
            try:
                self._enter(sym, icfg, front, pe["weight"], pe["close"],
                            pe["atr"], tag=" (retry)")
            except Exception as e:
                self._entry_failure(sym, e)   # stays queued for next subpoll

    # ---- risk rails (replay-calibrated, see research/governor_replay.py) --
    # BLOCKING governors were replayed vs the 16-yr deployed config and
    # REJECTED under the pre-registered criterion: every tested daily-loss
    # block or drawdown-disable damages total P&L (worst -90%) because this
    # system's wins follow its loss streaks. Rails are therefore ALERT-ONLY;
    # the hard brake is the human KILL_SWITCH file.
    RISK_DAILY_ALERT = -600.0        # least-harmful tested tier
    RISK_DD_ALERT = -2500.0
    RISK_OPEN_CEILING = 1750.0       # structural worst-case: 3xNQ + GC 2xATR

    def _market_open_now(self) -> bool:
        return is_market_open(datetime.now(timezone.utc))

    def check_kill_switch(self) -> bool:
        """Operator kill switch: create state/KILL_SWITCH to stop entries;
        write the word FLATTEN in it to also close all positions (once)."""
        ks = self.st.dir / "KILL_SWITCH"
        if not ks.exists():
            if str(self.st.state.get("halt_reason", "")).startswith("KILL"):
                self.st.state["halt_entries"] = False
                self.st.state["halt_reason"] = None
                self.st.event("kill switch removed — entries re-enabled")
                send_alert("✅ kill switch removed — entries re-enabled")
            return False
        if not str(self.st.state.get("halt_reason", "")).startswith("KILL"):
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = "KILL_SWITCH engaged by operator"
            self.st.event("KILL SWITCH engaged — entries halted")
            send_alert("🛑 KILL SWITCH engaged — entries halted")
        txt = ks.read_text().upper()
        if "FLATTEN" in txt and "DONE" not in txt:
            # Never cancel good broker-held stops into a closed session and
            # then wait on a market order that cannot fill. Keep protection
            # intact and retry the flatten at the first open-market subpoll.
            if not self._market_open_now():
                if self.st.state.get("kill_closed_alerted") != utcnow()[:10]:
                    self.st.state["kill_closed_alerted"] = utcnow()[:10]
                    self.st.event("kill-switch FLATTEN deferred — market "
                                  "closed; existing stops left untouched")
                    send_alert("🛑 FLATTEN requested while market closed — "
                               "broker stops left intact; bot retries when "
                               "the session opens")
                self.st.save()
                return True
            all_flat = True
            # SEQUENCING: cancel-and-verify EVERY scoped account order FIRST
            # — including manual/foreign-ref orders that could reopen
            # exposure after DONE — THEN read positions, flatten, and verify
            # both again at the end.
            try:
                sweep_unresolved = []
                for s2 in list(getattr(self.ex, "_stop_trades", {})):
                    if self.ex.cancel_stop(s2) == "unknown":
                        sweep_unresolved.append(s2)
                orders = (self.ex.open_account_orders()
                          if hasattr(self.ex, "open_account_orders") else
                          self.ex.open_bot_orders()
                          if hasattr(self.ex, "open_bot_orders") else [])
                for od in orders:
                    t2 = od.get("trade")
                    if t2 is not None and self.ex.cancel_and_verify(
                            t2, timeout=5) == "unknown":
                        sweep_unresolved.append(od.get("ref"))
                if sweep_unresolved:
                    # r6 #5: an unresolved cancel + a market close can
                    # both fill => short. NO flatten this cycle.
                    send_alert(f"🆘 kill-switch: cancels UNRESOLVED for "
                               f"{sweep_unresolved} — NOT flattening this "
                               "cycle; retrying")
                    return True
            except Exception as e:
                send_alert(f"🆘 kill-switch: order sweep failed ({e}) — "
                           "retrying next cycle")
                return True
            # Stops may partially or fully fill during that cancellation
            # sweep. Apply the broker quantity delta BEFORE sizing any market
            # close; otherwise the close can flatten the residual while the
            # stop execution remains unapplied and DONE lies about accounting.
            try:
                self._reconcile_execution_deltas()
            except Exception as e:
                send_alert(f"🆘 kill-switch: pre-flatten execution "
                           f"reconcile failed ({e}) — NOT flattening until "
                           "the stop/position delta is durable")
                return True
            try:
                acct = self.ex.positions()
            except Exception as e:
                send_alert(f"🆘 kill-switch flatten: cannot read broker "
                           f"positions ({e}) — retrying next cycle")
                return True
            con_to_sym = {self.st.pos(s)["con_id"]: s
                          for s in self.all_instruments
                          if self.st.pos(s)["position"]}
            for con_id, held in acct.items():
                if not held:
                    continue
                if held < 0:
                    # FLATTEN flattens (review r3 #7): a short is the most
                    # dangerous error state — cover it, loudly
                    try:
                        c = self.ex.contract_by_conid(con_id)
                        fill = self.ex.flatten_position(
                            f"COVER-{con_id}", c, held)
                        label = f"COVER-{con_id}"
                        try:
                            self._commit_emergency_fill(
                                label, fill, "BUY", con_id=con_id,
                                local_symbol=getattr(c, "localSymbol", None))
                        except Exception as journal_error:
                            self.st.state.setdefault("kill_unresolved", {})[
                                self._transition_key(
                                    "emergency", label, fill)] = {
                                        "label": label,
                                        "error": str(journal_error)[:200],
                                        "created": utcnow()}
                            raise RuntimeError(
                                "short covered but emergency execution "
                                "journal failed") from journal_error
                        self.st.event(f"kill switch COVERED short {held} "
                                      f"of {con_id} @ {fill.px}")
                        send_alert(f"🛑 kill switch covered SHORT {held}x "
                                   f"{con_id} @ {fill.px} (bug artifact — "
                                   "investigate)")
                    except Exception as e:
                        all_flat = False
                        send_alert(f"🆘 kill-switch: covering short "
                                   f"{con_id} FAILED ({e}) — MANUAL ACTION")
                    continue
                sym = con_to_sym.get(con_id)
                icfg = (self.all_instruments.get(sym)
                        if sym else None)
                p = self.st.pos(sym) if sym else None
                close_qty = int(held)
                try:
                    if sym:
                        self.ex.cancel_stop(sym)
                    c = self.ex.contract_by_conid(con_id)
                    if sym is None or icfg is None:
                        # unmanaged long: close it, book nothing (no entry
                        # basis), event it
                        fill = self.ex.close_market(f"UNMANAGED-{con_id}",
                                                    c, close_qty)
                        label = f"UNMANAGED-{con_id}"
                        try:
                            self._commit_emergency_fill(
                                label, fill, "SELL", con_id=con_id,
                                local_symbol=getattr(c, "localSymbol", None))
                        except Exception as journal_error:
                            self.st.state.setdefault("kill_unresolved", {})[
                                self._transition_key(
                                    "emergency", label, fill)] = {
                                        "label": label,
                                        "error": str(journal_error)[:200],
                                        "created": utcnow()}
                            raise RuntimeError(
                                "unmanaged position closed but emergency "
                                "execution journal failed") from journal_error
                        self.st.event(f"kill switch closed UNMANAGED "
                                      f"{close_qty} of {con_id} @ {fill.px}")
                        send_alert(f"🛑 kill switch closed unmanaged "
                                   f"{close_qty}x {con_id} @ {fill.px}")
                        continue
                    fill = self.ex.close_market(sym, c, close_qty)
                    closed = int(fill.qty or 0)
                    try:
                        broker_remaining = int(
                            self.ex.positions().get(con_id, 0))
                    except Exception as e2:
                        raise RuntimeError(
                            "kill-switch close filled but residual broker "
                            "quantity is unverifiable") from e2
                    basis_before = int(
                        p.get("basis_qty") if p.get("basis_qty") is not None
                        else p["qty"])
                    book_qty = min(closed, basis_before)
                    if closed > basis_before:
                        self.st.event(f"{sym}: kill switch closed {closed} but "
                                      f"state basis covers {basis_before}; "
                                      f"{closed - basis_before} has no P&L "
                                      "basis (broker quantity was authoritative)")
                    pnl = ((fill.px - p["entry_px"]) * icfg["mult"] * book_qty
                           if book_qty else 0.0)
                    allocate_entry = (book_qty > 0 and
                                      not p.get("entry_fees_allocated", False))
                    entry_ids = (list(p.get("entry_exec_ids") or [])
                                 if allocate_entry else [])
                    manual_entry_fee = ((p.get("entry_commission") or 0)
                                        if allocate_entry and not entry_ids else 0)
                    trade = (dict(
                        exit_time=utcnow(), book=self._book(sym), sym=sym,
                        trade_symbol=icfg["trade_symbol"], qty=book_qty,
                        entry_time=p["entry_time"], entry_px=p["entry_px"],
                        exit_px=fill.px, reason="kill_switch_flatten",
                        pnl_points=fill.px - p["entry_px"], pnl_usd=pnl,
                        commission=((fill.commission or 0) +
                                    manual_entry_fee) or None,
                        manual_commission=manual_entry_fee,
                        entry_weight=p["entry_weight"],
                        local_symbol=p["local_symbol"])
                        if book_qty else None)
                    if broker_remaining == 0:
                        from state_store import EMPTY_POS
                        nxt = dict(EMPTY_POS)
                    else:
                        all_flat = False
                        oid = self.ex.place_stop(
                            sym, c, broker_remaining, p["stop_px"])
                        nxt = dict(p)
                        nxt.update(
                            qty=broker_remaining,
                            basis_qty=max(0, basis_before - book_qty),
                            stop_order_id=oid,
                            entry_fees_allocated=(
                                p.get("entry_fees_allocated", False)
                                or allocate_entry),
                            fsm_state="KILL_RESIDUAL_PROTECTED")
                    event_key = self._transition_key(
                        "kill-switch-exit", sym, fill)
                    inserted = self._commit_position_transition(
                        event_key, sym, "KILL_SWITCH_EXIT", nxt,
                        fill=fill, side="SELL", trade=trade,
                        entry_exec_ids=entry_ids,
                        exit_exec_ids=getattr(fill, "exec_ids", []),
                        payload={"broker_closed": closed,
                                 "booked_qty": book_qty,
                                 "broker_remaining": broker_remaining})
                    if inserted and broker_remaining == 0:
                        send_alert(f"🛑 {sym} flattened by kill switch @ "
                                   f"{fill.px} pnl=${pnl:+,.2f}")
                    elif inserted:
                        send_alert(f"⚠️ {sym}: kill-switch close was PARTIAL "
                                   f"({closed}); {broker_remaining} still "
                                   f"held, stop re-armed @ {p['stop_px']}")
                except Exception as e:
                    all_flat = False
                    if p is None:
                        send_alert(f"🆘 kill-switch: failed closing "
                                   f"unmanaged {con_id} ({e}) — MANUAL")
                        continue
                    # the stop was cancelled before the close failed — the
                    # position may be naked: re-arm ONLY if the broker still
                    # holds it (re-arming a flat account creates a naked
                    # short generator — review r3 #6b)
                    try:
                        still = self.ex.positions().get(con_id, 0)
                        if still and still > 0:
                            c = self.ex.contract_by_conid(p["con_id"])
                            outcome = self.ex.cancel_stop(sym)
                            if outcome not in ("none", "cancelled"):
                                raise RuntimeError(
                                    f"existing stop cancel {outcome}; not "
                                    "stacking kill-switch recovery stop")
                            oid = self.ex.place_stop(sym, c, int(still),
                                                     p["stop_px"])
                            if int(still) == int(p["qty"]):
                                nxt = dict(p)
                                nxt["stop_order_id"] = oid
                                self._commit_position_transition(
                                    self._transition_key(
                                        "stop-rearm", sym,
                                        suffix=f"kill-error:{con_id}:{oid}"),
                                    sym, "STOP_REARM", nxt,
                                    payload={"reason": "kill_close_error"})
                            send_alert(f"🆘 {sym}: kill-switch flatten "
                                       f"FAILED ({e}) — stop RE-ARMED for "
                                       f"{still:.0f} @ {p['stop_px']}")
                        else:
                            send_alert(f"🆘 {sym}: flatten errored ({e}) "
                                       "but broker shows flat — no re-arm; "
                                       "verifying next cycle")
                    except Exception as e2:
                        send_alert(f"🆘 {sym}: flatten failed AND re-arm "
                                   f"failed ({e2}) — MANUAL ACTION NOW")
            # A stop can fill during the initial cancellation sweep, leaving
            # the broker flat without entering the position loop above. Apply
            # that quantity/accounting delta before considering DONE. Also
            # recover any interrupted unmanaged close/cover journal writes.
            try:
                self._reconcile_execution_deltas()
                recovered_emergency = self._recover_emergency_fills()
                if recovered_emergency:
                    self.st.event(
                        f"kill switch recovered {recovered_emergency} "
                        "emergency execution row(s)")
            except Exception as e:
                all_flat = False
                send_alert(f"🆘 kill-switch post-flatten accounting "
                           f"reconcile failed ({e}) — DONE refused")
            self.st.save()
            # completion = the FULL broker-truth invariant (review r3 #6):
            # every scoped position zero AND every account order terminal —
            # a flat book with a live naked stop is NOT done
            try:
                acct_flat = not any(q for q in
                                    self.ex.positions().values())
                open_orders = (self.ex.open_account_orders()
                               if hasattr(self.ex, "open_account_orders") else
                               self.ex.open_bot_orders()
                               if hasattr(self.ex, "open_bot_orders") else [])
            except Exception:
                acct_flat, open_orders = False, [{"ref": "unknown"}]
            state_flat = not any(self.st.pos(s).get("position")
                                 for s in self.all_instruments)
            unresolved_journal = bool(
                self.st.state.get("kill_unresolved") or {})
            db = getattr(self, "db", None)
            if db is not None:
                try:
                    unresolved_journal = (unresolved_journal or
                                          db.unapplied_execution_count() > 0)
                except Exception as e:
                    unresolved_journal = True
                    self.st.event("kill-switch completion journal check "
                                  f"failed: {e}")
            if (all_flat and acct_flat and state_flat and not open_orders
                    and not unresolved_journal):
                ks.write_text("ENGAGED (flatten DONE)")
                send_alert("✅ kill-switch flatten complete — broker "
                           "verified flat AND all bot orders terminal")
            else:
                send_alert(f"⚠️ kill-switch flatten INCOMPLETE (flat="
                           f"{acct_flat}, state_flat={state_flat}, "
                           f"live orders={len(open_orders)}, journal_pending="
                           f"{unresolved_journal}) "
                           "— will retry next cycle")
        return True

    def _check_code_drift(self):
        """Fail closed on commit, config, or worktree provenance drift."""
        db = getattr(self, "db", None)
        if db is None:
            return
        try:
            import hashlib
            import subprocess
            head_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=BASE,
                capture_output=True, text=True, timeout=5, check=True)
            head = head_result.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=BASE, capture_output=True, text=True, timeout=5,
                check=True).stdout.strip()
            cfg_sha = hashlib.sha256(
                (BASE / "config.yaml").read_bytes()).hexdigest()[:16]
            provenance = db.run_provenance()
            reasons = []
            if not head or head != provenance["code_commit"]:
                reasons.append(
                    f"HEAD={head or 'unknown'} run={provenance['code_commit']}")
            if cfg_sha != provenance["config_sha256"]:
                reasons.append(
                    f"config={cfg_sha} run={provenance['config_sha256']}")
            if status:
                reasons.append("worktree dirty")
            if reasons:
                detail = "; ".join(reasons)
                self._degraded.add("provenance")
                if self.st.state.get("soak_start"):
                    db.invalidate_current_soak(
                        f"runtime provenance drift: {detail}")
                    self.st.state["soak_release_valid"] = False
                    self._set_readiness(
                        "soak", "INVALID",
                        "runtime provenance drift permanently invalidated "
                        "the qualification epoch")
                drift_key = f"{head}:{cfg_sha}:{bool(status)}"
                self.st.state["code_drift"] = detail
                self.st.state["halt_entries"] = True
                if (not self.st.state.get("halt_reason") or
                        str(self.st.state.get("halt_reason", "")).startswith(
                            "CODE_DRIFT")):
                    self.st.state["halt_reason"] = f"CODE_DRIFT: {detail}"
                self._set_readiness("provenance", "FAILED", detail)
                if self.st.state.get("drift_alerted") != drift_key:
                    self.st.state["drift_alerted"] = drift_key
                    self.st.event(f"CODE DRIFT: {detail} — restart a clean "
                                  "committed tree before entries")
                    send_alert(f"⚠️ CODE DRIFT: {detail} — entries halted; "
                               "restart only from a clean committed tree")
            else:
                self._degraded.discard("provenance")
                self.st.state.pop("code_drift", None)
                self._set_readiness(
                    "provenance", "OK", "commit/config/clean-tree match")
                if str(self.st.state.get("halt_reason", "")).startswith(
                        "CODE_DRIFT"):
                    self.st.state["halt_entries"] = False
                    self.st.state["halt_reason"] = None
                    self.st.event("code/config drift cleared — entries resumed")
        except Exception as e:
            self._degraded.add("provenance")
            if self.st.state.get("soak_start"):
                try:
                    db.invalidate_current_soak(
                        f"runtime provenance became unverifiable: {e}")
                    self.st.state["soak_release_valid"] = False
                    self._set_readiness(
                        "soak", "INVALID",
                        "unverifiable runtime provenance permanently "
                        "invalidated the qualification epoch")
                except Exception as invalidation_error:
                    self.dlog.log(
                        "wakes", event="soak_invalidation_error",
                        error=str(invalidation_error)[:200])
            self.st.state["code_drift"] = f"provenance check failed: {e}"
            self.st.state["halt_entries"] = True
            if not self.st.state.get("halt_reason"):
                self.st.state["halt_reason"] = \
                    "CODE_DRIFT provenance check failed"
            self._set_readiness("provenance", "FAILED", str(e)[:200])
            self.dlog.log("wakes", event="drift_check_error",
                          error=str(e)[:150])

    def _risk_alerts(self):
        """Alert-only rails: daily realized loss + drawdown, deduped daily."""
        db = getattr(self, "db", None)
        if db is None:
            return
        try:
            today = utcnow()[:10]
            # corrections COUNT (review: rails ignored voids/fee adjusts)
            ledger = ("SELECT exit_time AS t, calculated_net AS v "
                      "FROM v_trade_net "
                      "UNION ALL SELECT ts, delta_usd FROM corrections")
            day = db.con.execute(
                f"SELECT COALESCE(SUM(v),0) FROM ({ledger}) WHERE "
                "t LIKE ?", (today + "%",)).fetchone()[0]
            eq = db.con.execute(
                f"SELECT COALESCE(SUM(v),0) FROM ({ledger})").fetchone()[0]
            peak = db.con.execute(
                f"SELECT COALESCE(MAX(cum),0) FROM (SELECT SUM(v) OVER "
                f"(ORDER BY t) AS cum FROM ({ledger}))").fetchone()[0]
            peak = max(0.0, float(peak))   # zero-equity start IS a peak
            dd = eq - peak
            # portfolio stop-risk ceiling (static structural cap, review):
            # sum of (last close - stop) exposure across open positions
            open_risk = 0.0
            for s2, ic2 in self.all_instruments.items():
                p2 = self.st.pos(s2)
                if p2["position"] and p2.get("stop_px"):
                    try:
                        last = float(self.store.bars(s2)["close"].iloc[-1])
                        open_risk += max(0.0, (last - p2["stop_px"])
                                         * ic2["mult"] * p2["qty"])
                    except Exception:
                        pass
            alerted = self.st.state.get("risk_alerted_on")
            if (day <= self.RISK_DAILY_ALERT or dd <= self.RISK_DD_ALERT
                    or open_risk > self.RISK_OPEN_CEILING) \
                    and alerted != today:
                self.st.state["risk_alerted_on"] = today
                send_alert(f"⚠️ RISK RAIL: day ${day:+,.0f} / drawdown "
                           f"${dd:+,.0f} — alert-only by design (replay "
                           "showed blocking harms; kill switch = "
                           "state/KILL_SWITCH)")
                self.st.event(f"risk rail alert: day {day:+.0f} dd {dd:+.0f}")
        except Exception as e:
            self.dlog.log("account", event="risk_alert_error",
                          error=str(e)[:150])

    def _position_diff(self) -> list[str]:
        """Account-vs-state diff (raises on broker unavailability — callers
        decide fail-open vs fail-closed)."""
        acct = self.ex.positions()
        managed, mm = {}, []
        for sym in self.all_instruments:
            p = self.st.pos(sym)
            if p["position"]:
                if p["con_id"] in managed:
                    mm.append(
                        f"duplicate managed conId {p['con_id']}: "
                        f"{managed[p['con_id']]} and {sym}")
                managed[p["con_id"]] = sym
                held = acct.get(p["con_id"], 0)
                if held != p["qty"]:
                    mm.append(f"{sym}: state {p['qty']} vs account {held}")
        for key, q in acct.items():
            if q and key not in managed:
                mm.append(f"unmanaged {key}: account {q}, state 0")
        return mm

    def _reconcile_execution_deltas(self):
        """Apply broker-quantity decreases missed by an interrupted caller.

        This is the retry path for a crash/DB failure after a broker fill but
        before the atomic FSM transition.  Exact unapplied execIds are used
        whenever possible; an execution-less broker delta is applied with an
        estimated price, loudly halts entries, and remains auditable.
        """
        db = getattr(self, "db", None)
        acct = self.ex.positions()
        for sym, icfg in self.all_instruments.items():
            p = self.st.pos(sym)
            if not p.get("position") or not p.get("con_id"):
                continue
            held = int(acct.get(p["con_id"], 0))
            if held >= int(p["qty"]):
                continue
            delta = int(p["qty"]) - held
            rows = (db.unapplied_executions(sym, side="SELL")
                    if db is not None else [])
            rows = [r for r in rows if r.get("con_id") == p["con_id"]]
            q = sum(r["qty"] for r in rows)
            from executor import Fill
            if rows and abs(q - delta) < 1e-9:
                px = sum(r["qty"] * r["px"] for r in rows) / q
                fill = Fill(px,
                            sum(float(r.get("commission") or 0)
                                for r in rows) or None,
                            qty=delta,
                            exec_time=min((r.get("exec_time") for r in rows
                                           if r.get("exec_time")), default=None),
                            exec_ids=[r["exec_id"] for r in rows
                                      if r.get("exec_id")])
                fill.exec_details = [
                    (r.get("exec_id"), r["qty"], r["px"],
                     r.get("commission")) for r in rows]
                reason = "reconcile_broker_execution"
            else:
                est_px2 = (p["stop_px"] if p.get("stop_px") is not None
                           else p["entry_px"])
                fill = Fill(float(est_px2), None, qty=delta)
                reason = "reconcile_exit_estimated"
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{sym} broker delta lacks exact executions"
                self.st.event(f"{sym}: broker quantity fell {p['qty']}->{held} "
                              "without an exact unapplied execution set; "
                              "accounting price estimated")
            fill.order_key = f"reconcile:{sym}:{p['qty']}->{held}"
            fill.filled_total = delta
            self._book_stop_exit(sym, icfg, p, fill, reason=reason)

    def _ensure_protection(self):
        """Bounded recovery for an unprotected position: halt entries ->
        re-arm at the state stop -> if unconfirmable, FLATTEN, verify, and
        record. 'Alert and reconcile' is the first response, never the last."""
        for sym in self.all_instruments:
            p = self.st.pos(sym)
            if not p["position"]:
                continue
            icfg_a = self.all_instruments[sym]
            # BROKER-QUANTITY-FIRST (r6 #2): never arm against a holding
            # the broker doesn't confirm — a flat account + fresh stop is
            # a naked-short generator, not protection
            try:
                held_b = self.ex.positions().get(p.get("con_id"), 0)
            except Exception:
                held_b = None
            if held_b is None or held_b <= 0:
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{sym} broker holding {held_b} != state {p['qty']}"
                self.st.event(f"{sym}: NOT arming — broker holds "
                              f"{held_b}, state says {p['qty']}; halted "
                              "for the position-diff handler")
                send_alert(f"🚨 {sym}: protection audit found broker "
                           f"{held_b} vs state {p['qty']} — NO stop "
                              "armed, entries halted")
                continue
            target_qty = int(held_b)
            if self.ex.has_working_stop(
                    sym, target_qty, con_id=p.get("con_id"),
                    stop_px=p.get("stop_px"),
                    px_tol=icfg_a["tick"] * 1e-6):
                continue
            if target_qty != int(p["qty"]):
                # Quantity provenance remains unresolved, but all positive
                # broker exposure still receives one exact protective stop.
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = \
                    f"{sym} broker holding {target_qty} != state {p['qty']}"
                self.st.event(f"{sym}: protecting broker quantity "
                              f"{target_qty} while state remains {p['qty']}; "
                              "entries halted for provenance reconciliation")
            # unresolved duplicate stops block re-arm for this sym (r6 #3)
            if sym in (getattr(self.ex, "unresolved_duplicate_stops", None)
                       or {}):
                self.st.event(f"{sym}: NOT arming — unresolved duplicate "
                              "stop still live at broker")
                continue
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = f"{sym} position unprotected"
            self.st.event(f"{sym}: NO WORKING STOP for broker qty "
                          f"{target_qty} — "
                          "re-arming now")
            send_alert(f"🚨 {sym}: position unprotected — re-arming stop")
            try:
                # cancel any residual/partial stop FIRST with a VERIFIED
                # outcome — re-arming beside a live remainder stacks sell
                # quantity (review r3 #5)
                outcome = self.ex.cancel_stop(sym)
                if outcome == "unknown":
                    raise RuntimeError(
                        "residual stop cancel unresolved — not stacking")
                if outcome == "filled":
                    self.st.event(f"{sym}: residual stop FILLED during "
                                  "protection audit — exit books next cycle")
                    continue
                c = self.ex.contract_by_conid(p["con_id"])
                oid = self.ex.place_stop(
                    sym, c, target_qty, p["stop_px"])
                nxt = dict(p)
                nxt["stop_order_id"] = oid
                nxt["fsm_state"] = "HELD_PROTECTED"
                self._commit_position_transition(
                    self._transition_key(
                        "stop-rearm", sym,
                        suffix=f"{p['con_id']}:{oid}:{p['qty']}:{p['stop_px']}"),
                    sym, "STOP_REARM", nxt,
                    payload={"reason": "protection_audit",
                             "broker_qty": target_qty,
                             "state_qty": p["qty"]})
                p = self.st.pos(sym)
                self.st.event(f"{sym}: stop RE-ARMED @ {p['stop_px']}")
                send_alert(f"✅ {sym}: stop re-armed @ {p['stop_px']}")
            except Exception as e:
                self.st.event(f"{sym}: re-arm FAILED ({e}) — flattening")
                send_alert(f"🚨 {sym}: cannot confirm protection — "
                           "FLATTENING per bounded-recovery policy")
                try:
                    c = self.ex.contract_by_conid(p["con_id"])
                    fill = self.ex.close_market(sym, c, target_qty)
                    icfg = self.all_instruments[sym]
                    broker_remaining = int(
                        self.ex.positions().get(p["con_id"], 0))
                    residual_protected = False
                    residual_stop_id = None
                    recovery_errors = []
                    # A market close can itself fill partially. Re-protect its
                    # broker-confirmed residual; if protection remains
                    # unattainable, make two bounded additional close
                    # attempts instead of knowingly leaving it naked until
                    # the next five-minute poll.
                    for attempt in range(2):
                        if broker_remaining <= 0:
                            break
                        try:
                            residual_stop_id = self.ex.place_stop(
                                sym, c, broker_remaining, p["stop_px"])
                            residual_protected = True
                            break
                        except Exception as protect_error:
                            recovery_errors.append(
                                f"protect#{attempt + 1}:{protect_error}")
                        try:
                            extra = self.ex.close_market(
                                sym, c, broker_remaining)
                            fill = self._merge_fills(fill, extra)
                            broker_remaining = int(
                                self.ex.positions().get(p["con_id"], 0))
                        except Exception as close_error:
                            recovery_errors.append(
                                f"close#{attempt + 2}:{close_error}")
                            break
                    closed = int(fill.qty or 0)
                    basis_before = int(
                        p.get("basis_qty") if p.get("basis_qty") is not None
                        else p["qty"])
                    qty = min(closed, basis_before)
                    pnl = (fill.px - p["entry_px"]) * icfg["mult"] * qty
                    allocate_entry = (qty > 0 and
                                      not p.get("entry_fees_allocated", False))
                    entry_ids = (list(p.get("entry_exec_ids") or [])
                                 if allocate_entry else [])
                    manual_entry_fee = ((p.get("entry_commission") or 0)
                                        if allocate_entry and not entry_ids else 0)
                    trade = (dict(
                        exit_time=utcnow(), book=self._book(sym), sym=sym,
                        trade_symbol=icfg["trade_symbol"], qty=qty,
                        entry_time=p["entry_time"], entry_px=p["entry_px"],
                        exit_px=fill.px, reason="protection_lost_flatten",
                        pnl_points=fill.px - p["entry_px"], pnl_usd=pnl,
                        commission=((fill.commission or 0) +
                                    manual_entry_fee) or None,
                        manual_commission=manual_entry_fee,
                        entry_weight=p["entry_weight"],
                        local_symbol=p["local_symbol"])
                        if qty else None)
                    if broker_remaining == 0:
                        from state_store import EMPTY_POS
                        nxt = dict(EMPTY_POS)
                    else:
                        nxt = dict(p)
                        nxt.update(qty=broker_remaining,
                                   basis_qty=max(0, basis_before - qty),
                                   entry_fees_allocated=(
                                       p.get("entry_fees_allocated", False)
                                       or allocate_entry))
                        if residual_protected:
                            nxt.update(stop_order_id=residual_stop_id,
                                       fsm_state="HELD_PROTECTED")
                        else:
                            nxt.update(stop_order_id=None,
                                       fsm_state="HELD_UNPROTECTED")
                    event_key = self._transition_key(
                        "protection-lost-exit", sym, fill)
                    inserted = self._commit_position_transition(
                        event_key, sym, "PROTECTION_LOST_EXIT", nxt,
                        fill=fill, side="SELL", trade=trade,
                        entry_exec_ids=entry_ids,
                        exit_exec_ids=getattr(fill, "exec_ids", []),
                        payload={"broker_closed": closed,
                                 "booked_qty": qty,
                                 "broker_remaining": broker_remaining,
                                 "residual_protected": residual_protected,
                                 "recovery_errors": recovery_errors})
                    if inserted and broker_remaining == 0:
                        send_alert(f"🛑 {sym}: flattened @ {fill.px} "
                                   f"(protection unconfirmable) "
                                   f"pnl=${pnl:+,.2f}")
                    elif inserted:
                        self.st.event(f"{sym}: flatten was PARTIAL ({qty}); "
                                      f"{broker_remaining} still held; "
                                      f"protected={residual_protected}")
                        send_alert(f"🆘 {sym}: partial flatten — "
                                   f"{broker_remaining} held; residual "
                                   f"protected={residual_protected}")
                        if not residual_protected:
                            send_alert(f"🆘 {sym}: bounded protection/close "
                                       "attempts exhausted with an "
                                       "UNPROTECTED residual — MANUAL ACTION "
                                       f"NOW ({recovery_errors})")
                except Exception as e2:
                    self.st.event(f"{sym}: FLATTEN ALSO FAILED: {e2} — "
                                  "manual intervention required")
                    send_alert(f"🆘 {sym}: flatten failed after re-arm "
                               f"failure: {e2} — MANUAL ACTION NEEDED")
            self.st.save()

    def _heal_gateway(self, sym: str):
        """Self-heal a half-dead paper gateway (Error 1104: process alive,
        API answering, order routing severed — undetectable by IBC KeepAlive
        or connect checks). Kill IBC's java; launchd relaunches a fresh
        session in ~60s and the next wake retries. ibkr_paper only."""
        if self.ex.venue != "ibkr_paper":
            return
        import subprocess
        try:
            self.ex.disconnect()
        except Exception:
            pass
        r = subprocess.run(["pkill", "-f", "IBC.jar"], capture_output=True)
        self.st.event(f"GATEWAY SELF-HEAL: {sym} entry was UNROUTED — killed "
                      f"half-dead gateway (pkill rc={r.returncode}); launchd "
                      "relaunches, next wake retries")
        send_alert("🔧 gateway order-routing was dead (orders not reaching "
                   "IBKR) — restarted the paper gateway automatically; "
                   "next hourly wake retries")
        self.dlog.log("connections", event="gateway_self_heal", sym=sym,
                      pkill_rc=r.returncode)

    def reconcile_positions(self):
        """Per-wake account-vs-state audit (added 2026-07-10 after IBKR's
        paper nightly reset silently wiped positions while the GTC stops
        survived — the stops then fired against a flat book and created
        naked shorts). Runs AFTER per-instrument processing, so normal
        stop-fill reconciliation has already been applied; any residual
        mismatch is real. Mismatch => halt entries + alert. Stops/trailing
        keep working; the halt self-clears when account and state agree."""
        try:
            self._reconcile_execution_deltas()
        except Exception as e:
            self.dlog.log("account", event="execution_delta_reconcile_error",
                          error=str(e)[:200])
            self._degraded.add("reconciliation")
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = "execution delta reconcile failed"
            return
        self._ensure_protection()
        try:
            mismatches = self._position_diff()
            if mismatches:
                # debounce: a freshly-(re)connected session can serve a stale
                # position snapshot mid-replay (false halt 2026-07-13 01:00Z,
                # phantom -1 MGC on a clean account). Re-read before halting.
                ib = getattr(self.ex, "ib", None)
                if ib is not None:
                    ib.sleep(5)          # pump the event loop so the position
                else:                    # replay completes
                    time.sleep(5)
                mismatches = self._position_diff()
        except Exception as e:
            self.dlog.log("account", event="reconcile_error", error=str(e)[:200])
            self._degraded.add("reconciliation")
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = "broker/state reconciliation failed"
            self._set_readiness("reconciliation", "FAILED", str(e)[:200])
            return
        if mismatches:
            detail = "; ".join(mismatches)
            self.dlog.log("account", event="position_mismatch", detail=detail)
            if not self.st.state["halt_entries"]:
                self.st.state["halt_entries"] = True
                self.st.state["halt_reason"] = f"position mismatch: {detail}"
                self.st.event(f"POSITION MISMATCH — entries HALTED: {detail}")
                send_alert(f"🚨 POSITION MISMATCH (account vs bot) — entries "
                           f"halted until it clears: {detail}")
        elif (self.st.state["halt_entries"] and
              (str(self.st.state.get("halt_reason", "")).startswith(
                  "position mismatch") or
               self.st.state.get("halt_reason") ==
               "broker/state reconciliation failed")):
            self.st.state["halt_entries"] = False
            self.st.state["halt_reason"] = None
            self.st.event("position mismatch cleared — entries resumed")
            send_alert("✅ position mismatch cleared — entries resumed")

    def subpoll(self):
        """5-min poll: heartbeat, failed-wake catch-up, stop fills,
        pending-entry retries. A failure never waits for the next hour."""
        self.st.heartbeat()
        self.check_kill_switch()     # operator brake first — even while
                                     # disconnected the halt must engage
        if self._wake_failed:
            self.wake()              # catch-up: reconnect + full cycle
            return                   # (wake did everything if it succeeded)
        if not (self.ex.is_connected() and self._startup_done):
            return
        for sym, icfg in self.all_instruments.items():
            p = self.st.pos(sym)
            if not p["position"]:
                continue
            fill = self.ex.check_stop_filled(sym)
            if fill is not None:
                self._book_stop_exit(sym, icfg, p, fill)
        self._retry_pending_entries()
        try:
            self.process_satellite(pd.Timestamp.now(tz="UTC"))
        except Exception as e:
            self.st.event(f"SATELLITE SUBPOLL ERROR: {e}")
            self.dlog.log("wakes", event="instrument_error", sym="satellite",
                          error=str(e)[:300])
        self.st.save()

    def _book_stop_exit(self, sym: str, icfg: dict, p: dict, fill,
                        *, reason: str = "stop"):
        """Book a stop exit for the quantity that ACTUALLY filled. A stop
        that filled less than the position leaves the remainder held AND
        protected (IB keeps the stop working for the remaining quantity)."""
        broker_fill_qty = int(fill.qty or 0)
        qty = min(broker_fill_qty, p["qty"])
        if qty <= 0:
            return
        unbased_qty = max(0, broker_fill_qty - qty)
        if unbased_qty:
            self.st.state["halt_entries"] = True
            self.st.state["halt_reason"] = \
                f"{sym} exit exceeded tracked long basis"
            self.st.event(f"{sym}: broker SELL fill {broker_fill_qty} exceeded "
                          f"tracked long {p['qty']} by {unbased_qty}; likely "
                          "short exposure — accounting correction required")
            send_alert(f"🆘 {sym}: exit sold {broker_fill_qty} against tracked "
                       f"long {p['qty']} — possible SHORT {unbased_qty}; "
                       "manual broker check now")
        pnl = (fill.px - p["entry_px"]) * icfg["mult"] * qty
        allocate_entry = not p.get("entry_fees_allocated", False)
        entry_ids = list(p.get("entry_exec_ids") or []) if allocate_entry else []
        manual_entry_fee = ((p.get("entry_commission") or 0)
                            if allocate_entry and not entry_ids else 0)
        comm = (fill.commission or 0) + manual_entry_fee
        trade = dict(
            exit_time=utcnow(), book=self._book(sym), sym=sym,
            trade_symbol=icfg["trade_symbol"], qty=qty,
            entry_time=p["entry_time"], entry_px=p["entry_px"],
            exit_px=fill.px, reason=reason,
            pnl_points=fill.px - p["entry_px"], pnl_usd=pnl,
            commission=comm or None, manual_commission=manual_entry_fee,
            entry_weight=p["entry_weight"],
            local_symbol=p["local_symbol"])
        nxt = dict(p)
        if qty >= p["qty"]:
            from state_store import EMPTY_POS
            nxt = dict(EMPTY_POS)
        else:
            nxt["qty"] = p["qty"] - qty
            nxt["basis_qty"] = max(
                0, int(p.get("basis_qty")
                       if p.get("basis_qty") is not None else p["qty"]) - qty)
            nxt["entry_fees_allocated"] = \
                p.get("entry_fees_allocated", False) or allocate_entry
        event_key = self._transition_key(reason, sym, fill)
        inserted = self._commit_position_transition(
            event_key, sym, reason.upper(), nxt, fill=fill, side="SELL",
            trade=trade, entry_exec_ids=entry_ids,
            exit_exec_ids=getattr(fill, "exec_ids", []),
            payload={"closed_qty": qty,
                     "broker_fill_qty": broker_fill_qty,
                     "unbased_qty": unbased_qty,
                     "remaining_qty": int(nxt.get("qty") or 0)})
        if inserted and qty >= p["qty"]:
            send_alert(f"🛑 {sym} stop filled @ {fill.px} "
                       f"pnl=${pnl:+,.2f} ({qty}x)")
        elif inserted:
            self.st.event(f"{sym}: PARTIAL stop exit {qty}, still holding "
                          f"{nxt['qty']} (stop continues for remainder)")
            send_alert(f"🛑 {sym} PARTIAL stop exit {qty} @ {fill.px} "
                       f"pnl=${pnl:+,.2f} — {nxt['qty']} still held+protected")
        # SQLite transition committed — NOW remove a fully-filled live handle.
        if hasattr(self.ex, "mark_stop_booked"):
            self.ex.mark_stop_booked(sym, fill)

    # ---------------- scheduler ----------------------------------------
    def run_forever(self):
        wake_s = self.cfg["ops"]["wake_second"]
        self.st.event(f"bot started (venue={self.ex.venue}, "
                      f"mode={self.st.state['mode']})")
        self.st.heartbeat()   # stamp at boot — else the watchdog sees the
                              # previous process's stale heartbeat and kills
                              # a healthy fresh start (race seen 07:28Z)
        # startup catch-up: if the previous process's last wake failed or is
        # stale, don't wait for the next hourly slot — decide from the last
        # closed bar NOW (same never-wait-an-hour rule as the subpoll)
        # ALWAYS wake at boot (review r3: a restart after a green wake left
        # the daemon disconnected + unreconciled for up to an hour). The
        # wake is idempotent: per-bar decision dedup, reconcile, no re-entry.
        self.st.event("startup wake (connect + reconcile immediately)")
        self.wake()
        loop_errors = 0
        last_wake_mono = time.monotonic()
        while True:
            now = datetime.now(timezone.utc)
            # this hour's :00:wake_s if still ahead, else next hour's — the
            # old unconditional +1h skipped the hour whenever an iteration
            # landed inside [:00:00, :00:wake_s), starving wakes entirely
            # once the 5-min poll chain phase-locked into that window
            cand = now.replace(minute=0, second=wake_s, microsecond=0)
            nxt_hour = cand if cand > now else cand + timedelta(hours=1)
            nxt_poll = now + timedelta(minutes=5)
            target = min(nxt_hour, nxt_poll)
            dt = max(1.0, (target - now).total_seconds())
            try:
                self.ex.idle_sleep(dt)        # keeps venue sockets serviced
                overdue = time.monotonic() - last_wake_mono > 3900
                if target == nxt_hour or overdue:
                    if overdue and target != nxt_hour:
                        self.dlog.log("wakes", event="forced_wake",
                                      reason="no wake >65min")
                    self.wake(scheduled=target if target == nxt_hour else None)
                    last_wake_mono = time.monotonic()
                else:
                    self.subpoll()
                loop_errors = 0
            except KeyboardInterrupt:
                self.st.event("bot stopped (keyboard)")
                return
            except Exception as e:
                loop_errors += 1
                self.st.event(f"LOOP ERROR (#{loop_errors}): {e}")
                log.exception("loop error")
                self.dlog.log("wakes", event="loop_error", n=loop_errors,
                              error=str(e)[:300])
                if loop_errors in (1, 6) or loop_errors % 12 == 0:
                    send_alert(f"🚨 loop error (#{loop_errors}): {e}")
                time.sleep(30)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single wake and exit")
    ap.add_argument("--force", action="store_true",
                    help="override the daemon-live safety guard for --once")
    ap.add_argument("--mode", choices=["defensive", "default", "aggressive"],
                    help="toggle rotation mode and exit")
    args = ap.parse_args()

    # This must precede the kernel lock: ProcessLock creates/writes its path.
    # A refused checkout must not touch the operational generation at all,
    # including by leaving behind a misleading lock file.
    assert_clean_release()

    # Heartbeat is a friendly diagnostic; the fcntl lock below is the actual
    # split-brain invariant and cannot be bypassed by --force.
    if args.once and not args.force:
        hb = BASE / "state" / "heartbeat"
        if hb.exists():
            from datetime import datetime as _dt, timezone as _tz
            age = (_dt.now(_tz.utc) - _dt.fromisoformat(hb.read_text().strip())).total_seconds()
            if age < 360:
                print(f"REFUSING --once: daemon appears LIVE (heartbeat {age:.0f}s "
                      "old). Running --once now would corrupt shared state. "
                      "Stop the daemon first (launchctl unload …trendbot) or pass "
                      "--force if you are certain the daemon is down.")
                raise SystemExit(73)

    mode = "mode-change" if args.mode else ("once" if args.once else "daemon")
    try:
        with ProcessLock(BASE / "state" / "trading.lock", mode):
            bot = TrendBot(client_id_offset=10 if args.once else 0)
            if args.mode:
                prev = bot.st.state["mode"]
                bot.st.state["mode"] = args.mode
                bot.st.state["mode_history"].append(
                    {"ts": utcnow(), "from": prev, "to": args.mode})
                bot.st.event(f"MODE TOGGLE {prev} -> {args.mode} (takes effect "
                              "at next Monday rebalance)")
                bot.st.save()
                print(f"mode: {prev} -> {args.mode}")
                return
            if args.once:
                bot.wake()
                return
            bot.run_forever()
    except ProcessLockError as e:
        print(f"REFUSING TO START: {e}")
        raise SystemExit(73)


if __name__ == "__main__":
    main()
