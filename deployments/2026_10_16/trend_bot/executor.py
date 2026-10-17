"""Order execution — IBKR paper/live behind one interface:

  venue: ibkr_paper   IBKR paper session (TWS paper 7497 / gateway paper 4002)
  venue: ibkr_live    IBKR live gateway (4001) — DORMANT until interlocks pass
  venue: webull_live  REFUSED by the factory; no validated adapter exists

LIVE INTERLOCKS (IBKR live): refuse to start unless ALL of
  1. config executor.live_confirmed == true          (a deliberate edit)
  2. env TRENDBOT_LIVE == "YES"                      (a deliberate export)
  3. the venue account matches env TRENDBOT_LIVE_ACCOUNT (right account)
A live flip is a decision, never an accident.

Safety invariants (all venues):
  * SL-ARMED-WITHIN-2s: entry fill without a resting stop within
    stop_arm_timeout_s => market-flatten immediately + alert.
  * Entry-timeout adoption: if a market order times out, cancel and re-check —
    a late fill is ADOPTED (stop armed), never orphaned.
  * All orders tagged orderRef="trendbot:{SYM}:{role}" for restart recovery.
"""
from __future__ import annotations

import logging
import os
import time

from ib_insync import IB, Future, MarketOrder, StopOrder

from deep_log import get_logger

log = logging.getLogger("trend_bot.exec")


class ProtectionAbort(Exception):
    """Partial-entry protection failed repeatedly — abort the entry."""


class UnresolvedEntry(Exception):
    """Entry order state could not be verified at the broker. The intent
    journal MUST be preserved (callers do not clear it for this class)."""


class UnresolvedProtection(Exception):
    """A submitted protective order may still be live at the broker."""


class Fill:
    def __init__(self, px: float, commission: float | None,
                 qty: float = 0.0, exec_time=None, order_id=None,
                 exec_ids=None):
        self.px = px
        self.commission = commission
        self.qty = qty              # ACTUAL filled quantity — callers must
                                    # size state and stops from this, never
                                    # from the requested quantity
        self.exec_time = exec_time  # broker's first-execution timestamp
        self.order_id = order_id    # the ENTRY/CLOSE order's own id
        self.exec_ids = exec_ids or []   # broker execIds (idempotency keys)
        self.exec_details = []      # [(execId, qty, px)] one per execution
        self.partial_exit = None    # (qty, px) sold by the protection stop
                                    # DURING the entry — caller books it
        self.partial_exit_details = []
        self.order_key = None       # durable permId/ref-based order identity
        self.filled_total = qty     # broker cumulative quantity at observation


class ExecutorBase:
    """Interface every venue must implement."""
    venue = "base"

    def connect(self): ...
    def disconnect(self): ...
    def is_connected(self) -> bool: ...
    def idle_sleep(self, seconds: float): ...
    def positions(self) -> dict: ...
    def open_account_orders(self) -> list[dict]: ...
    def recover_stops(self) -> dict: ...
    def enter_long_with_stop(self, sym, contract, qty, stop_px): ...
    def place_stop(self, sym, contract, qty, stop_px) -> int | str: ...
    def update_stop(self, sym, new_px): ...
    def check_stop_filled(self, sym): ...
    def cancel_stop(self, sym): ...
    def close_market(self, sym, contract, qty) -> Fill: ...


def check_live_interlocks(cfg_executor: dict, venue: str, account: str):
    """Raise unless every deliberate-action interlock passes."""
    if not cfg_executor.get("live_confirmed"):
        raise RuntimeError(
            f"REFUSING {venue}: executor.live_confirmed is not true in config")
    if os.environ.get("TRENDBOT_LIVE") != "YES":
        raise RuntimeError(
            f"REFUSING {venue}: env TRENDBOT_LIVE != YES")
    want = os.environ.get("TRENDBOT_LIVE_ACCOUNT", "")
    if not want or account != want:
        raise RuntimeError(
            f"REFUSING {venue}: account {account!r} does not match "
            f"env TRENDBOT_LIVE_ACCOUNT")


class IBKRPaperExecutor(ExecutorBase):
    venue = "ibkr_paper"
    _account_check = staticmethod(lambda acct: acct.startswith("D"))
    _account_err = "not a paper account (paper ids start with 'D')"

    def __init__(self, host: str, port: int, client_id: int,
                 stop_arm_timeout_s: float = 2.0, alert=None, dlog=None,
                 cfg_executor: dict | None = None):
        self.host, self.port, self.client_id = host, port, client_id
        self.stop_arm_timeout_s = stop_arm_timeout_s
        self.alert = alert or (lambda msg: None)
        self.dlog = dlog or get_logger()
        self.cfg_executor = cfg_executor or {}
        self.ib = IB()
        self.account = None
        self.db = None              # attached by TrendBot after StateDB opens
        self._stop_trades: dict[str, object] = {}
        self._events_wired = False
        self._was_connected = False
        self._down_since = None

    # ---- lifecycle ----------------------------------------------------
    def _verify_account(self, acct: str):
        if not self._account_check(acct):
            raise RuntimeError(f"REFUSING to trade: account {acct} "
                               f"{self._account_err}")
        if self.venue == "ibkr_paper":
            want = os.environ.get("TRENDBOT_PAPER_ACCOUNT", "")
            if not want:
                raise RuntimeError(
                    "REFUSING paper executor: TRENDBOT_PAPER_ACCOUNT is "
                    "not configured")
            if acct != want:
                raise RuntimeError(
                    f"REFUSING paper executor: gateway account {acct!r} "
                    "does not match TRENDBOT_PAPER_ACCOUNT")

    def connect(self) -> bool:
        """Returns True if this call RE-established a connection (caller must
        re-sync order handles via recover_stops)."""
        if self.ib.isConnected():
            return False
        self.ib.connect(self.host, self.port, clientId=self.client_id,
                        timeout=20)
        self.ib.RequestTimeout = 90   # never hang forever on a flapping farm
        accts = self.ib.managedAccounts()
        acct = accts[0] if accts else ""
        try:
            self._verify_account(acct)
        except Exception:
            self.ib.disconnect()
            raise
        self.account = acct
        self._wire_events()
        reconnected = self._was_connected
        self._was_connected = True
        outage_s = (round(time.monotonic() - self._down_since, 1)
                    if self._down_since else None)
        self._down_since = None
        self.dlog.log("connections", client="executor", venue=self.venue,
                      event="reconnect" if reconnected else "connect",
                      account=acct, port=self.port, outage_s=outage_s,
                      note="TRADE SESSION BACK ALIVE" if outage_s else None)
        log.info("%s executor connected, account=%s", self.venue, acct)
        return reconnected

    def _wire_events(self):
        if self._events_wired:
            return
        self._events_wired = True

        def on_status(trade):
            self.dlog.log("orders", venue=self.venue, event="status",
                          order_id=trade.order.orderId,
                          perm_id=trade.order.permId,
                          ref=trade.order.orderRef,
                          type=trade.order.orderType,
                          action=trade.order.action,
                          qty=trade.order.totalQuantity,
                          aux=trade.order.auxPrice,
                          status=trade.orderStatus.status,
                          filled=trade.orderStatus.filled,
                          remaining=trade.orderStatus.remaining,
                          avg_fill=trade.orderStatus.avgFillPrice)
            ref = trade.order.orderRef or ""
            parts = ref.split(":")
            db = getattr(self, "db", None)
            if (self.account and
                    getattr(trade.order, "account", None) != self.account):
                return
            if (db is not None and len(parts) >= 4 and parts[0] == "trendbot"
                    and parts[2] == "entry"):
                sym, intent_id = parts[1], parts[3]
                # Current refs are trendbot:SYM:entry:INTENT.  Keep the parser
                # tolerant of future suffixes by taking the first intent part.
                event_key = (f"entry-intent:{self.account}:{sym}:"
                             f"{intent_id}")
                status = trade.orderStatus.status
                durable_status = {
                    # IBKR can emit a preliminary Cancelled/Inactive before
                    # a real fill. Only cancel_and_verify may declare the
                    # durable event terminal.
                    "Cancelled": "CANCEL_SEEN",
                    "ApiCancelled": "CANCEL_SEEN",
                    "Inactive": "CANCEL_SEEN",
                    "Filled": "FILLED_PENDING",
                }.get(status, "WORKING")
                try:
                    db.begin_event(event_key, sym, "ENTRY_INTENT",
                                   order_ref=ref, order_id=trade.order.orderId,
                                   perm_id=trade.order.permId,
                                   payload={"broker_status": status})
                    db.update_event(
                        event_key, durable_status, order_ref=ref,
                        order_id=trade.order.orderId,
                        perm_id=trade.order.permId,
                        payload={"broker_status": status,
                                 "filled": float(trade.orderStatus.filled or 0),
                                 "remaining": float(
                                     trade.orderStatus.remaining or 0)})
                except Exception as e:
                    self.dlog.log("orders", venue=self.venue,
                                  event="entry_state_journal_error",
                                  order_id=trade.order.orderId,
                                  error=str(e)[:200])

        def on_exec(trade, fill):
            self.dlog.log("orders", venue=self.venue, event="execution",
                          order_id=trade.order.orderId,
                          ref=trade.order.orderRef,
                          exec_id=fill.execution.execId,
                          shares=fill.execution.shares,
                          price=fill.execution.price,
                          exec_time=fill.execution.time,
                          local_symbol=fill.contract.localSymbol)
            db = getattr(self, "db", None)
            if db is not None:
                fill_account = (getattr(fill.execution, "acctNumber", None)
                                or self.account)
                if self.account and fill_account != self.account:
                    return
                ref = trade.order.orderRef or ""
                parts = ref.split(":")
                sym = parts[1] if len(parts) > 1 else "UNKNOWN"
                try:
                    db.execution(
                        sym, fill.execution.side,
                        float(fill.execution.shares),
                        float(fill.execution.price),
                        local_symbol=fill.contract.localSymbol,
                        con_id=fill.contract.conId,
                        commission=(fill.commissionReport.commission
                                    if fill.commissionReport else None),
                        exec_id=fill.execution.execId,
                        exec_time=fill.execution.time,
                        order_ref=ref, order_id=trade.order.orderId,
                        perm_id=trade.order.permId,
                        account=fill_account)
                except Exception as e:
                    self.dlog.log("orders", venue=self.venue,
                                  event="execution_journal_error",
                                  exec_id=fill.execution.execId,
                                  error=str(e)[:200])

        def on_comm(trade, fill, report):
            self.dlog.log("orders", venue=self.venue, event="commission",
                          exec_id=report.execId,
                          commission=report.commission,
                          realized_pnl=report.realizedPNL)
            db = getattr(self, "db", None)
            if db is not None:
                fill_account = (getattr(fill.execution, "acctNumber", None)
                                or self.account)
                if self.account and fill_account != self.account:
                    return
                try:
                    db.commission(
                        report.execId, float(report.commission),
                        account=fill_account,
                        currency=getattr(report, "currency", None),
                        realized_pnl=getattr(report, "realizedPNL", None))
                except Exception as e:
                    self.dlog.log("orders", venue=self.venue,
                                  event="commission_journal_error",
                                  exec_id=report.execId, error=str(e)[:200])

        COLD = {1100: "connectivity_lost", 2110: "connectivity_broken"}
        ALIVE = {1101: "connectivity_restored_lost_state",
                 1102: "connectivity_restored"}

        def on_err(reqId, code, msg, *a):
            if code in COLD:
                self._down_since = self._down_since or time.monotonic()
                self.dlog.log("connections", client="executor",
                              venue=self.venue, event="gateway_cold",
                              code=code, kind=COLD[code], msg=str(msg)[:160])
            elif code in ALIVE:
                out = (round(time.monotonic() - self._down_since, 1)
                       if self._down_since else None)
                self._down_since = None
                self.dlog.log("connections", client="executor",
                              venue=self.venue, event="gateway_alive",
                              code=code, kind=ALIVE[code], outage_s=out,
                              msg=str(msg)[:160])
            else:
                self.dlog.log("connections", client="executor",
                              venue=self.venue, event="ib_message",
                              req_id=reqId, code=code, msg=str(msg)[:200])

        def on_disconnect():
            self._down_since = time.monotonic()
            self.dlog.log("connections", client="executor", venue=self.venue,
                          event="socket_disconnected",
                          note="TRADE SESSION COLD — socket dropped")

        self.ib.orderStatusEvent += on_status
        self.ib.execDetailsEvent += on_exec
        self.ib.commissionReportEvent += on_comm
        self.ib.errorEvent += on_err
        self.ib.disconnectedEvent += on_disconnect

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            self.dlog.log("connections", client="executor", venue=self.venue,
                          event="disconnect")

    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def idle_sleep(self, seconds: float):
        if self.ib.isConnected():
            self.ib.sleep(seconds)
        else:
            time.sleep(seconds)

    # ---- contract helpers ---------------------------------------------
    def contract_by_conid(self, con_id: int):
        c = Future(conId=con_id)
        got = self.ib.qualifyContracts(c)
        if not got:
            raise RuntimeError(f"cannot qualify conId {con_id}")
        return got[0]

    # ---- account truth --------------------------------------------------
    def positions(self) -> dict[int, float]:
        # ACCOUNT-SCOPED (r6 #9): a linked account's position must never
        # bleed into this venue's view
        acct = getattr(self, "account", None)
        scoped: dict[int, float] = {}
        for position in self.ib.positions():
            if (acct is not None and
                    getattr(position, "account", acct) != acct):
                continue
            qty = float(position.position)
            if abs(qty - round(qty)) > 1e-9:
                raise RuntimeError(
                    "non-integral futures position returned by broker: "
                    f"account={acct} conId={position.contract.conId} qty={qty}")
            con_id = int(position.contract.conId)
            scoped[con_id] = scoped.get(con_id, 0.0) + qty
        return scoped

    def position_details(self) -> list[dict]:
        acct = getattr(self, "account", None)
        return [{"con_id": p.contract.conId,
                 "local_symbol": p.contract.localSymbol,
                 "qty": p.position, "avg_cost": p.avgCost}
                for p in self.ib.positions()
                if acct is None or getattr(p, "account", acct) == acct]

    def recover_stops(self) -> dict[str, object]:
        """Re-link resting stops. MULTIPLE live stops for one symbol are a
        defect state (review r4 #3) — cancel every candidate and let startup
        create one canonical replacement.  Anything unresolved is reported
        so the caller halts beside a possibly-live extra stop."""
        self.ib.reqAllOpenOrders()
        self.ib.sleep(1)
        by_sym: dict[str, list] = {}
        for t in self.ib.openTrades():
            ref = t.order.orderRef or ""
            if getattr(self, 'account', None) and getattr(
                    t.order, "account", None) != self.account:
                continue
            if ref.startswith("trendbot:") and ref.endswith(":stop"):
                by_sym.setdefault(ref.split(":")[1], []).append(t)
        self._stop_trades = {}
        self.unresolved_duplicate_stops: dict[str, list[int]] = {}
        for sym, trades in by_sym.items():
            if len(trades) == 1:
                self._stop_trades[sym] = trades[0]
                continue
            # duplicates: choosing a survivor by orderId is a GUESS (review
            # r5 #2 — the 'newest' can be the unsafe one). Cancel-verify
            # every candidate; the startup re-arm sweep then arms ONE
            # canonical stop from persisted state.
            for cand in trades:
                outcome = self.cancel_and_verify(
                    cand, timeout=getattr(self, "cancel_verify_timeout",
                                          10.0))
                self.dlog.log("stops", venue=self.venue,
                              event="duplicate_stop_cancelled", sym=sym,
                              order_id=cand.order.orderId, outcome=outcome)
                if outcome not in ("cancelled", "filled"):
                    self.unresolved_duplicate_stops.setdefault(
                        sym, []).append(cand.order.orderId)
                    self.alert(f"🚨 {sym}: DUPLICATE stop "
                               f"{cand.order.orderId} could not be "
                               "cancelled — unresolved")
        self.dlog.log("account", venue=self.venue, event="recover_stops",
                      recovered={s: t.order.orderId
                                 for s, t in self._stop_trades.items()},
                      unresolved_duplicates=self.unresolved_duplicate_stops
                      or None)
        return dict(self._stop_trades)

    # ---- orders ---------------------------------------------------------
    def _order_key(self, trade) -> str:
        perm = int(getattr(trade.order, "permId", 0) or 0)
        if perm:
            return f"perm:{perm}"
        ref = getattr(trade.order, "orderRef", "") or ""
        return f"ref:{ref}:order:{trade.order.orderId}"

    def _fill_from(self, trade) -> "Fill":
        """Build a Fill from ACTUAL executions — px, commission, filled
        quantity, and the broker's first-execution timestamp. A partially
        filled order returns its real quantity; callers must never assume
        the requested quantity filled (a timed-out 3-lot with 1 filled once
        risked a 3-lot stop against a 1-lot position)."""
        px = trade.orderStatus.avgFillPrice
        qty = float(trade.orderStatus.filled or 0)
        comm = sum(f.commissionReport.commission for f in trade.fills
                   if f.commissionReport) or None
        exec_t = None
        exec_ids, details = [], []
        db = getattr(self, "db", None)
        if trade.fills:
            try:
                exec_t = min(f.execution.time for f in trade.fills)
                new = [f for f in trade.fills
                       if not (db is not None and f.execution.execId and
                               db.is_execution_applied(
                                   f.execution.execId,
                                   account=(getattr(f.execution,
                                                    "acctNumber", None)
                                            or self.account)))]
                exec_ids = [f.execution.execId for f in new]
                # per-execution commission, one row per NEW execId — a
                # re-observed execution must never re-enter the ledger
                # (review r5 #6)
                details = [(f.execution.execId, f.execution.shares,
                            f.execution.price,
                            (f.commissionReport.commission
                             if f.commissionReport else None))
                           for f in new]
            except Exception as e:
                self.dlog.log("orders", venue=self.venue,
                              event="fill_provenance_error",
                              order_id=trade.order.orderId,
                              error=str(e)[:200])
                raise RuntimeError(
                    f"order {trade.order.orderId} execution provenance "
                    "unreadable") from e
        fl = Fill(px, comm, qty=qty, exec_time=exec_t,
                  order_id=trade.order.orderId, exec_ids=exec_ids)
        fl.exec_details = details
        fl.order_key = self._order_key(trade)
        fl.filled_total = qty
        return fl

    def _wait_fill(self, trade, timeout: float = 90.0, on_partial=None):
        t0 = time.monotonic()
        last_partial = 0.0
        while time.monotonic() - t0 < timeout:
            self.ib.waitOnUpdate(timeout=2)
            filled = float(trade.orderStatus.filled or 0)
            if (on_partial and filled > last_partial
                    and trade.orderStatus.status != "Filled"):
                # a PARTIAL position exists at the broker RIGHT NOW — let the
                # caller protect it immediately instead of waiting out the
                # timeout unprotected (review 2026-07-18 blocker #3).
                # FAIL CLOSED: a failed protection attempt is NOT "handled" —
                # last_partial only advances on success, so the next loop
                # tick retries, and the failure is logged loudly.
                try:
                    on_partial(filled)
                    last_partial = filled
                except ProtectionAbort:
                    raise               # caller aborts the entry (fail closed)
                except Exception as e:
                    self.dlog.log("stops", venue=self.venue,
                                  event="partial_protect_failed",
                                  order_id=trade.order.orderId,
                                  filled=filled, error=str(e)[:150])
            if trade.orderStatus.status == "Filled":
                return self._fill_from(trade)
            if trade.orderStatus.status in ("Cancelled", "Inactive"):
                # TWS can emit a SPURIOUS first "Cancelled" (permId=0) before
                # the real ack — order 15 on 2026-07-05 filled 140ms after
                # one. Never trust it immediately: wait, then re-check.
                self.ib.sleep(3)
                if trade.orderStatus.status == "Filled" or trade.orderStatus.filled:
                    self.dlog.log("orders", venue=self.venue,
                                  event="spurious_cancel_recovered",
                                  order_id=trade.order.orderId,
                                  px=trade.orderStatus.avgFillPrice,
                                  filled=float(trade.orderStatus.filled or 0))
                    return self._fill_from(trade)
                if trade.orderStatus.status in ("Cancelled", "Inactive"):
                    raise RuntimeError(f"order {trade.order.orderId} "
                                       f"{trade.orderStatus.status}")
        # A missing permId is NOT terminal proof.  TWS has emitted permId=0
        # statuses immediately before a real fill, so every timeout follows
        # the same cancel-and-verify path.  UNROUTED remains a diagnostic/heal
        # hint, never authorization to discard the durable intent.
        unrouted = not trade.order.permId
        outcome = self.cancel_and_verify(
            trade, timeout=getattr(self, "cancel_verify_timeout", 10.0))
        if outcome == "unknown":
            raise UnresolvedEntry(
                f"order {trade.order.orderId} "
                f"{'UNROUTED (permId=0), ' if unrouted else ''}cancel "
                "UNRESOLVED after timeout — intent preserved for recovery")
        if trade.orderStatus.status == "Filled" or trade.orderStatus.filled:
            self.dlog.log("orders", venue=self.venue, event="late_fill_adopted",
                          order_id=trade.order.orderId,
                          px=trade.orderStatus.avgFillPrice,
                          filled=float(trade.orderStatus.filled or 0))
            return self._fill_from(trade)
        raise TimeoutError(
            f"order {trade.order.orderId} "
            f"{'UNROUTED (permId=0), ' if unrouted else ''}not filled in "
            f"{timeout}s (cancel VERIFIED terminal)")

    def enter_long_with_stop(self, sym: str, contract, qty: int,
                             stop_px: float,
                             intent_id: str | None = None) -> tuple[Fill, int]:
        o = MarketOrder("BUY", qty)
        o.account = getattr(self, "account", None) or ""
        # unique ref per intent — crash recovery must never adopt an OLDER
        # same-symbol execution (review 2026-07-18 blocker #4)
        o.orderRef = (f"trendbot:{sym}:entry:{intent_id}" if intent_id
                      else f"trendbot:{sym}:entry")
        t_submit = time.monotonic()
        self.dlog.log("orders", venue=self.venue, event="submit_entry",
                      sym=sym, qty=qty, local_symbol=contract.localSymbol,
                      ref=o.orderRef)
        # incremental protection: any partial that appears during the wait
        # gets a stop for the filled quantity IMMEDIATELY (blocker #3 — a
        # partial used to sit unprotected for up to ~95s)
        partial = {"trade": None, "qty": 0, "fails": 0,
                   "sold": 0.0, "exit_details": [],
                   "fallback_by_order": {}}

        def _details(fl: Fill) -> list[tuple]:
            rows = list(getattr(fl, "exec_details", []) or [])
            if not rows and float(fl.qty or 0) > 0:
                rows = [(None, float(fl.qty), float(fl.px), fl.commission)]
            return rows

        def _round_trip(entry_fill: Fill,
                        exit_details: list[tuple]) -> Fill:
            """Return an auditable entry result with its emergency exits."""
            existing = list(getattr(entry_fill,
                                    "partial_exit_details", []) or [])
            gross = float(entry_fill.qty or 0) + sum(
                float(row[1]) for row in existing)
            rows = existing + list(exit_details)
            sold = sum(float(row[1]) for row in rows)
            if sold > gross + 1e-9:
                raise UnresolvedEntry(
                    f"{sym}: emergency exits {sold} exceed entry fills "
                    f"{gross}; broker state needs reconciliation")
            if sold:
                notional = sum(float(row[1]) * float(row[2]) for row in rows)
                entry_fill.partial_exit = (sold, notional / sold)
                entry_fill.partial_exit_details = rows
            entry_fill.qty = max(0.0, gross - sold)
            return entry_fill

        def _capture_interim_exit(t):
            already = {d[0] for d in partial["exit_details"] if d[0]}
            new = []
            for f in getattr(t, "fills", []):
                xid = f.execution.execId
                if xid in already:
                    continue
                new.append((xid, float(f.execution.shares),
                            float(f.execution.price),
                            (f.commissionReport.commission
                             if f.commissionReport else None)))
            total = float(t.orderStatus.filled or 0)
            if new:
                partial["exit_details"].extend(new)
            elif total:
                key = self._order_key(t)
                prior = float(partial["fallback_by_order"].get(key, 0))
                if total > prior:
                    partial["exit_details"].append(
                        (None, total - prior, t.orderStatus.avgFillPrice, None))
                    partial["fallback_by_order"][key] = total
            partial["sold"] = sum(float(d[1])
                                  for d in partial["exit_details"])

        def _protect(filled: float):
            q = int(filled)
            if q <= partial["qty"]:
                return
            if partial["fails"] >= 3:
                # FAIL CLOSED (review r3 #4): protection keeps failing —
                # abort the entry instead of accumulating naked exposure
                raise ProtectionAbort(
                    f"partial protection failed {partial['fails']}x")
            try:
                if partial["trade"] is not None:
                    # replace only after VERIFIED terminal state of the old
                    # stop — unconfirmed cancel + bigger stop stacks quantity
                    outcome = self.cancel_and_verify(partial["trade"],
                                                     timeout=5)
                    if outcome == "unknown":
                        raise RuntimeError(
                            "prior partial stop cancel unresolved")
                    if outcome == "filled":
                        # The old partial exited, but the entry may have filled
                        # additional quantity.  Recompute NET broker exposure
                        # and arm it now; returning here recreated the original
                        # up-to-90s unprotected window.
                        _capture_interim_exit(partial["trade"])
                        partial["trade"] = None
                        partial["qty"] = 0
                net_q = q - int(partial["sold"])
                if net_q <= 0:
                    partial["qty"] = q
                    return
                so = StopOrder("SELL", net_q, stop_px, tif="GTC")
                so.orderRef = f"trendbot:{sym}:stop"
                so.account = getattr(self, "account", None) or ""
                so.outsideRth = True
                t = self.ib.placeOrder(contract, so)
                t0p = time.monotonic()
                ack_to = getattr(self, "partial_ack_timeout", 5.0)
                while time.monotonic() - t0p < ack_to:
                    self.ib.waitOnUpdate(timeout=1)
                    if t.orderStatus.status in ("PreSubmitted", "Submitted"):
                        break
                    if t.orderStatus.status in ("Cancelled", "Inactive"):
                        raise RuntimeError(
                            "partial-protection stop rejected")
                else:
                    raise RuntimeError(
                        "partial-protection stop not acknowledged")
                partial["trade"] = t
                partial["qty"] = q
                self.dlog.log("stops", venue=self.venue,
                              event="partial_protected", sym=sym, qty=net_q,
                              stop_px=stop_px)
            except ProtectionAbort:
                raise
            except Exception:
                partial["fails"] += 1
                raise

        entry_trade = self.ib.placeOrder(contract, o)
        try:
            fill = self._wait_fill(
                entry_trade, on_partial=_protect,
                timeout=getattr(self, "entry_fill_timeout", 90.0))
        except ProtectionAbort as e:
            # FAIL CLOSED: cancel the entry with a VERIFIED terminal state
            # BEFORE any offsetting sell — selling against an entry that
            # might still fill can flip the account short (review r4 #4)
            self.alert(f"🚨 {sym}: aborting entry — partial protection "
                       f"unattainable ({e})")
            outcome = self.cancel_and_verify(entry_trade)
            if outcome == "unknown":
                self.dlog.log("orders", venue=self.venue,
                              event="entry_abort_unresolved", sym=sym)
                self.alert(f"🆘 {sym}: entry cancel UNRESOLVED — no "
                           "offsetting order placed; intent preserved for "
                           "recovery; entries halted by caller")
                raise UnresolvedEntry(
                    f"{sym}: entry state unverifiable during abort")
            pt = partial["trade"]
            if pt is not None:
                pt_out = self.cancel_and_verify(pt, timeout=5)
                if pt_out == "unknown":
                    self.alert(f"🆘 {sym}: interim stop also unresolved "
                               "during abort — manual check advised")
                    raise UnresolvedEntry(
                        f"{sym}: interim stop unverifiable during abort")
                if pt_out == "filled" or pt.orderStatus.filled:
                    _capture_interim_exit(pt)
            gross_fill = (self._fill_from(entry_trade)
                          if float(entry_trade.orderStatus.filled or 0) > 0
                          else None)
            held = float(entry_trade.orderStatus.filled or 0) - \
                float(partial["sold"])
            close_details = []
            if held > 0:
                co = MarketOrder("SELL", int(held))
                co.orderRef = (f"trendbot:{sym}:close:{intent_id}"
                               if intent_id else f"trendbot:{sym}:close")
                co.account = getattr(self, "account", None) or ""
                close_fill = self._wait_fill(
                    self.ib.placeOrder(contract, co), timeout=30)
                close_details = _details(close_fill)
            # verify the invariant before claiming closure — ONLY an
            # explicit broker zero counts; an exception or None is
            # UNRESOLVED, never 'verified' (review r5 #4)
            try:
                residual = self.positions().get(contract.conId, 0)
            except Exception as pe:
                self.alert(f"🆘 {sym}: broker position unverifiable after "
                           f"abort close ({pe}) — intent preserved")
                raise UnresolvedEntry(
                    f"{sym}: broker unverifiable after abort close")
            self.dlog.log("orders", venue=self.venue,
                          event="entry_aborted_protection", sym=sym,
                          cancel_outcome=outcome, closed=held,
                          residual_position=residual)
            if residual:
                self.alert(f"🆘 {sym}: abort left residual position "
                           f"{residual} — manual check")
                raise UnresolvedEntry(
                    f"{sym}: abort residual position {residual}")
            if gross_fill is not None:
                return (_round_trip(
                    gross_fill,
                    list(partial["exit_details"]) + close_details), None)
            raise TimeoutError(
                f"{sym}: entry aborted before fill; cancel and broker-flat "
                "state verified")
        t_fill = time.monotonic()
        # NET vs GROSS (review r3 #4b): if the protection stop FILLED during
        # the entry, part of the position already exited — the final stop
        # must cover the NET quantity, and the round trip must be booked
        pt = partial["trade"]
        if pt is not None and float(pt.orderStatus.filled or 0):
            _capture_interim_exit(pt)
        sold = float(partial["sold"])
        if sold > 0:
            notional = sum(float(d[1]) * float(d[2])
                           for d in partial["exit_details"])
            exit_px = notional / sold if sold else fill.px
            fill.partial_exit = (sold, exit_px)
            fill.partial_exit_details = list(partial["exit_details"])
            fill.qty = max(0.0, fill.qty - sold)
            self.dlog.log("orders", venue=self.venue,
                          event="partial_stop_exited_during_entry", sym=sym,
                          sold=sold, px=exit_px,
                          net_qty=fill.qty)
            if fill.qty == 0:
                return fill, None      # fully round-tripped; nothing to arm
        if fill.qty != qty:
            self.alert(f"⚠️ {sym}: PARTIAL fill {fill.qty}/{qty} — protecting "
                       f"the {fill.qty} actually held")
        self.dlog.log("orders", venue=self.venue, event="entry_filled",
                      sym=sym, px=fill.px, commission=fill.commission,
                      qty=fill.qty, requested=qty, order_id=fill.order_id,
                      submit_to_fill_ms=round((t_fill - t_submit) * 1000))
        try:
            working = (pt is not None and pt.orderStatus.status
                       in ("PreSubmitted", "Submitted"))
            if working and (int(pt.order.totalQuantity) -
                            float(pt.orderStatus.filled or 0)) == int(fill.qty):
                # the protection stop's REMAINDER covers the net position
                self._stop_trades[sym] = pt
                stop_id = pt.order.orderId
            else:
                if pt is not None:
                    outcome = self.cancel_and_verify(pt, timeout=5)
                    if outcome == "unknown":
                        # keep the live stop rather than risk stacking; it
                        # may undersize protection — reconcile verifies
                        self._stop_trades[sym] = pt
                        self.alert(f"⚠️ {sym}: interim stop unresolved — "
                                   "keeping it; protection audit will "
                                   "verify coverage")
                        return fill, pt.order.orderId
                    if outcome == "filled":
                        # Capture the exact executions that arrived during
                        # the final cancel.  Updating only the aggregate qty
                        # would advance state while leaving those execIds
                        # unapplied and their commissions outside accounting.
                        _capture_interim_exit(pt)
                        sold2 = float(partial["sold"])
                        if sold2 > sold:
                            notional = sum(float(d[1]) * float(d[2])
                                           for d in partial["exit_details"])
                            fill.partial_exit = (sold2, notional / sold2)
                            fill.partial_exit_details = list(
                                partial["exit_details"])
                            fill.qty = max(0.0, fill.qty - (sold2 - sold))
                            if fill.qty == 0:
                                return fill, None
                # stop sized to the NET filled quantity, never the request
                stop_id = self.place_stop(sym, contract, int(fill.qty),
                                          stop_px)
            armed_ms = round((time.monotonic() - t_fill) * 1000)
            exec_to_armed_ms = None
            if fill.exec_time is not None:
                from datetime import datetime, timezone
                exec_to_armed_ms = round(
                    (datetime.now(timezone.utc) - fill.exec_time)
                    .total_seconds() * 1000)
            self.dlog.log("stops", venue=self.venue, event="armed", sym=sym,
                          stop_px=stop_px, order_id=stop_id, qty=fill.qty,
                          fill_to_armed_ms=armed_ms,
                          exec_to_armed_ms=exec_to_armed_ms)
            # SLA enforced on the HONEST clock (broker execution -> armed),
            # not the post-wait remainder (review: 66ms was flattering)
            sla = (exec_to_armed_ms if exec_to_armed_ms is not None
                   else armed_ms)
            if sla > self.stop_arm_timeout_s * 1000:
                log.warning("%s stop armed %dms after broker execution "
                            "(> %.1fs target)", sym, sla,
                            self.stop_arm_timeout_s)
                self.dlog.log("stops", venue=self.venue, event="sla_breach",
                              sym=sym, exec_to_armed_ms=sla)
                # escalate — a breach is an operator-visible event, not a
                # log line (position IS protected now; auto-flatten on a
                # breach was reviewed and rejected as riskier than holding)
                self.alert(f"⚠️ {sym}: stop armed {sla/1000:.1f}s after "
                           f"execution (> {self.stop_arm_timeout_s:.0f}s "
                           "SLA) — protected now; latency logged")
            return fill, stop_id
        except Exception as e:
            self.alert(f"🚨 {sym}: STOP FAILED TO ARM ({e}) — flattening now")
            self.dlog.log("stops", venue=self.venue, event="arm_failed_flatten",
                          sym=sym, error=str(e)[:200])
            try:
                close_fill = self.close_market(
                    sym, contract, int(fill.qty))
                residual = self.positions().get(contract.conId, 0)
                if residual != 0:
                    raise UnresolvedEntry(
                        f"{sym}: emergency close left broker residual "
                        f"{residual}")
                return _round_trip(fill, _details(close_fill)), None
            except UnresolvedEntry:
                raise
            except Exception as close_error:
                # A stop may have filled in the cancel race. Resolve only
                # when broker-flat truth and the exit executions are both
                # available for the caller's atomic accounting transition.
                try:
                    residual = self.positions().get(contract.conId, 0)
                    stop_fill = self.check_stop_filled(sym)
                except Exception as verify_error:
                    raise UnresolvedEntry(
                        f"{sym}: stop arm and emergency close unresolved; "
                        f"broker verification failed: {verify_error}") \
                        from close_error
                if residual == 0 and stop_fill is not None:
                    return _round_trip(fill, _details(stop_fill)), None
                raise UnresolvedEntry(
                    f"{sym}: stop arm failed and emergency close is "
                    f"unresolved (residual={residual}, "
                    f"close={close_error})") from close_error

    def place_stop(self, sym: str, contract, qty: int, stop_px: float) -> int:
        o = StopOrder("SELL", qty, stop_px, tif="GTC")
        o.orderRef = f"trendbot:{sym}:stop"
        o.account = getattr(self, "account", None) or ""
        o.outsideRth = True
        trade = self.ib.placeOrder(contract, o)
        # A submitted-but-unacknowledged stop may still be live.  Track it
        # immediately so every market-close path must cancel/verify it.
        self._stop_trades[sym] = trade
        outcome = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10:
            self.ib.waitOnUpdate(timeout=1)
            if trade.orderStatus.status in ("Submitted", "PreSubmitted"):
                return trade.order.orderId
            if trade.orderStatus.status in ("Cancelled", "Inactive"):
                outcome = ("filled" if trade.orderStatus.filled
                           else "cancelled")
                break
            if trade.orderStatus.status == "Filled":
                outcome = "filled"
                break
        if outcome is None:
            outcome = self.cancel_and_verify(
                trade, timeout=getattr(self, "cancel_verify_timeout", 10.0))
        if outcome == "cancelled":
            self._stop_trades.pop(sym, None)
        elif outcome == "unknown":
            raise UnresolvedProtection(
                f"{sym}: submitted stop acknowledgement/cancel unresolved; "
                "order remains tracked")
        raise RuntimeError(f"stop order not acknowledged "
                           f"(status={trade.orderStatus.status}, "
                           f"cancel={outcome})")

    def update_stop(self, sym: str, new_px: float, reason: str = "trail",
                    hw: float | None = None, atr: float | None = None):
        trade = self._stop_trades.get(sym)
        old_px = trade.order.auxPrice if trade is not None else None
        event_key = None
        db = getattr(self, "db", None)
        try:
            if trade is None:
                raise RuntimeError("no tracked stop order")
            event_key = (f"stop-modify:{self.account}:{sym}:"
                         f"{self._order_key(trade)}:{float(new_px)}")
            if db is not None:
                db.begin_event(event_key, sym, "STOP_MODIFY", payload={
                    "old_px": old_px, "new_px": new_px, "reason": reason})
            trade.order.auxPrice = new_px
            submitted = self.ib.placeOrder(trade.contract, trade.order)
            acked = False
            t0 = time.monotonic()
            timeout = getattr(self, "stop_modify_timeout", 10.0)
            while time.monotonic() - t0 < timeout:
                self.ib.waitOnUpdate(timeout=1)
                status = submitted.orderStatus.status
                if status in ("Cancelled", "ApiCancelled", "Inactive"):
                    raise RuntimeError(f"stop modification rejected ({status})")
                # reqOpenOrders is a server round-trip; inspecting the locally
                # mutated Trade alone would be a false acknowledgement.
                opened = self.ib.reqOpenOrders()
                match = next((t for t in opened
                              if t.order.orderId == trade.order.orderId
                              and (not self.account or
                                   t.order.account == self.account)), None)
                if (match is not None and
                        match.orderStatus.status in
                        ("Submitted", "PreSubmitted") and
                        abs(float(match.order.auxPrice) - float(new_px)) <= 1e-9):
                    self._stop_trades[sym] = match
                    trade = match
                    acked = True
                    break
            if not acked:
                raise RuntimeError("stop modification not broker-acknowledged")
            if db is not None:
                db.update_event(event_key, "RESOLVED", payload={
                    "old_px": old_px, "new_px": new_px, "reason": reason,
                    "status": trade.orderStatus.status})
            self.dlog.log("stops", venue=self.venue, event="modified", sym=sym,
                          old_px=old_px, new_px=new_px, reason=reason,
                          hw=hw, atr=atr, order_id=trade.order.orderId)
            return trade.order.orderId
        except Exception as e:
            # fallback: cancel + fresh stop (handles stale post-restart handles)
            self.dlog.log("stops", venue=self.venue, event="modify_failed",
                          sym=sym, error=str(e)[:200], fallback="cancel_replace")
            contract = trade.contract if trade is not None else None
            qty = (int(float(trade.order.totalQuantity) -
                       float(trade.orderStatus.filled or 0))
                   if trade is not None else None)
            if contract is None or qty is None:
                raise
            # the replacement is gated on a VERIFIED cancel — an unresolved
            # cancel + a fresh stop stacks sell quantity (review r3 #5)
            outcome = self.cancel_stop(sym)
            if outcome == "unknown":
                if db is not None and event_key:
                    db.update_event(event_key, "UNRESOLVED",
                                    payload={"cancel_outcome": outcome})
                raise RuntimeError(
                    f"{sym}: stop cancel UNRESOLVED during replace — old "
                    "stop may be live; not stacking a second")
            if outcome == "filled":
                if db is not None and event_key:
                    db.update_event(event_key, "FILLED_PENDING",
                                    payload={"cancel_outcome": outcome})
                raise RuntimeError(
                    f"{sym}: stop FILLED during replace — exit will be "
                    "booked by check_stop_filled; no replacement placed")
            oid = self.place_stop(sym, contract, qty, new_px)
            if db is not None and event_key:
                db.update_event(event_key, "RESOLVED", payload={
                    "resolution": "cancel_replace", "new_order_id": oid})
            self.dlog.log("stops", venue=self.venue, event="replaced", sym=sym,
                          old_px=old_px, new_px=new_px, order_id=oid)
            return oid

    def check_stop_filled(self, sym: str) -> Fill | None:
        trade = self._stop_trades.get(sym)
        if trade is None:
            return None
        filled = float(trade.orderStatus.filled or 0)
        db = getattr(self, "db", None)
        order_key = self._order_key(trade)
        applied = (db.applied_order_qty(order_key, account=self.account)
                   if db is not None else
                   float(getattr(trade, "_booked_qty", 0.0)))
        unapplied_fills = [
            f for f in getattr(trade, "fills", [])
            if not (db is not None and f.execution.execId and
                    db.is_execution_applied(
                        f.execution.execId,
                        account=(getattr(f.execution, "acctNumber", None)
                                 or self.account)))]
        new_qty = (sum(float(f.execution.shares) for f in unapplied_fills)
                   if unapplied_fills else max(0.0, filled - applied))
        status = trade.orderStatus.status
        terminal = status in ("Filled", "Cancelled", "ApiCancelled",
                              "Inactive")
        if status == "Filled":
            fill = self._fill_from(trade)
            fill.qty = new_qty              # only the durable-unapplied part
            # PEEK ONLY (r6 #8): the handle and booked-markers are consumed
            # by mark_stop_booked AFTER the caller's ledgers commit — a
            # booking failure must leave the fill re-observable
            fill._consume = (sym, filled, True)
            fill.order_key = order_key
            fill.filled_total = filled
            self.dlog.log("stops", venue=self.venue, event="filled", sym=sym,
                          px=fill.px, commission=fill.commission,
                          qty=fill.qty)
            return fill if fill.qty > 0 else None
        if new_qty > 0:
            # PARTIAL stop fill (review 2026-07-18 blocker #2): report the
            # newly filled part NOW so state shrinks with the broker; the
            # handle stays — IB keeps the stop working for the remainder.
            fl = Fill(trade.orderStatus.avgFillPrice, None,
                      qty=new_qty,
                      order_id=trade.order.orderId)
            if unapplied_fills:
                fl.exec_details = [
                    (f.execution.execId, f.execution.shares,
                     f.execution.price,
                     (f.commissionReport.commission
                      if f.commissionReport else None))
                    for f in unapplied_fills]
                fl.exec_ids = [d[0] for d in fl.exec_details if d[0]]
            # A partially-filled stop can finish as Cancelled/Inactive after
            # its residual was cancelled.  That terminal handle must be
            # discarded after this fill commits; retaining it forever makes
            # the protection audit see an unworkable "filled" stop and never
            # arm the broker-confirmed residual.
            fl._consume = (sym, filled, terminal)
            fl.order_key = order_key
            fl.filled_total = filled
            self.dlog.log("stops", venue=self.venue, event="partial_filled",
                          sym=sym, px=trade.orderStatus.avgFillPrice,
                          qty=new_qty, total_filled=filled)
            return fl
        if terminal:
            # Every cumulative fill is already durable. The broker has also
            # confirmed that no residual order is working, so the stale
            # in-memory handle is safe to forget.
            self._stop_trades.pop(sym, None)
            self.dlog.log("stops", venue=self.venue,
                          event="terminal_handle_released", sym=sym,
                          status=status, total_filled=filled)
        return None

    def mark_stop_booked(self, sym: str, fill) -> None:
        """Consume a peeked stop fill AFTER the caller's ledgers have
        committed (r6 #8). Idempotent; no-op without a _consume marker."""
        marker = getattr(fill, "_consume", None)
        trade = self._stop_trades.get(sym)
        if marker is None or trade is None:
            return
        _, filled_total, full = marker
        # Durable application is committed by StateDB in the same transaction
        # as the position/trade.  In-memory markers remain only for lightweight
        # executors/tests with no attached DB.
        if getattr(self, "db", None) is None:
            trade._booked_qty = filled_total
            ids = {d[0] for d in getattr(fill, "exec_details", []) if d[0]}
            trade._booked_exec_ids = set(
                getattr(trade, "_booked_exec_ids", set())) | ids
        if full:
            del self._stop_trades[sym]
        self.dlog.log("stops", venue=self.venue, event="booked", sym=sym,
                      qty=fill.qty, full=full)

    def has_working_stop(self, sym: str, qty: float | None = None,
                         con_id: int | None = None,
                         stop_px: float | None = None,
                         px_tol: float = 0.0) -> bool:
        """True iff a live protective stop is working for sym AND matches
        the required IDENTITY — a stop on the wrong contract or at the
        wrong price is not protection (review r4 #2). Checks: working
        status, SELL action, exact stop-market type, exact remaining qty,
        contract conId,
        and stop price within px_tol when given."""
        trade = self._stop_trades.get(sym)
        if trade is None:
            return False
        if trade.orderStatus.status not in ("PreSubmitted", "Submitted"):
            return False
        o = trade.order
        if (getattr(self, "account", None) and
                getattr(o, "account", None) != self.account):
            return False
        if getattr(o, "action", None) != "SELL":
            return False
        if getattr(o, "orderType", None) != "STP":
            return False
        if con_id is not None:
            tc = getattr(trade, "contract", None)
            if tc is None or getattr(tc, "conId", None) != con_id:
                return False
        if stop_px is not None:
            aux = getattr(o, "auxPrice", None)
            if aux is None or abs(float(aux) - stop_px) > max(px_tol, 1e-9):
                return False
        if qty is not None:
            remaining = float(o.totalQuantity) - \
                float(trade.orderStatus.filled or 0)
            # EXACT match required (review r5 #1): an oversized stop is a
            # naked-short generator, not extra safety
            if remaining != qty:
                return False
        return True

    def recover_entries(self) -> list[dict]:
        """Startup: find our WORKING entry orders and today's entry
        executions (crash-window recovery — an entry placed before a crash
        must be adopted or cancelled, never forgotten)."""
        out = []
        self.ib.reqAllOpenOrders()
        self.ib.sleep(1)
        for t in self.ib.openTrades():
            ref = t.order.orderRef or ""
            if getattr(self, 'account', None) and getattr(
                    t.order, "account", None) != self.account:
                continue
            if ref.startswith("trendbot:") and ":entry" in ref:
                out.append({"kind": "working_entry", "ref": ref,
                            "sym": ref.split(":")[1], "trade": t,
                            "order_id": t.order.orderId})
        try:
            for f in self.ib.reqExecutions():
                ref = getattr(f.execution, "orderRef", "") or ""
                if getattr(self, 'account', None) and getattr(
                        f.execution, "acctNumber",
                        self.account) != self.account:
                    continue
                if ref.startswith("trendbot:") and ":entry" in ref:
                    out.append({"kind": "entry_execution", "ref": ref,
                                "sym": ref.split(":")[1],
                                "con_id": f.contract.conId,
                                "local_symbol": f.contract.localSymbol,
                                "qty": f.execution.shares,
                                "px": f.execution.price,
                                "exec_id": f.execution.execId,
                                "time": str(f.execution.time)})
        except Exception as e:
            # an incomplete scan must be VISIBLE — callers treat pending
            # intents as unresolved rather than erased (review r3 #2)
            out.append({"kind": "scan_error", "sym": "*",
                        "error": str(e)[:150]})
            self.dlog.log("orders", venue=self.venue,
                          event="recover_entries_exec_error",
                          error=str(e)[:150])
        if out:
            self.dlog.log("orders", venue=self.venue, event="recover_entries",
                          found=[{k: v for k, v in o.items() if k != "trade"}
                                 for o in out])
        return out

    def recover_bot_executions(self) -> list[dict]:
        """All executions in IBKR's available window for this account/ref.

        Raw rows are used to reconcile stop/roll/emergency fills after a
        process restart.  Durable SQLite capture extends their lifetime beyond
        the broker API's session window.
        """
        out = []
        for f in self.ib.reqExecutions():
            ref = getattr(f.execution, "orderRef", "") or ""
            acct = getattr(f.execution, "acctNumber", None)
            if self.account and acct != self.account:
                continue
            if not ref.startswith("trendbot:"):
                continue
            parts = ref.split(":")
            out.append({
                "ref": ref,
                "sym": parts[1] if len(parts) > 1 else "UNKNOWN",
                "role": parts[2] if len(parts) > 2 else "unknown",
                "side": f.execution.side,
                "qty": float(f.execution.shares),
                "px": float(f.execution.price),
                "exec_id": f.execution.execId,
                "time": str(f.execution.time),
                "con_id": f.contract.conId,
                "local_symbol": f.contract.localSymbol,
                "commission": (f.commissionReport.commission
                               if f.commissionReport else None),
                "account": acct,
            })
        return out

    def open_account_orders(self) -> list[dict]:
        """Every scoped non-terminal order, including manual/foreign refs."""
        self.ib.reqAllOpenOrders()
        self.ib.sleep(1)
        out = []
        for t in self.ib.openTrades():
            ref = t.order.orderRef or ""
            if getattr(self, 'account', None) and getattr(
                    t.order, "account", None) != self.account:
                continue
            if t.orderStatus.status not in (
                    "Filled", "Cancelled", "ApiCancelled", "Inactive"):
                out.append({"ref": ref, "order_id": t.order.orderId,
                            "status": t.orderStatus.status,
                            "action": t.order.action,
                            "qty": t.order.totalQuantity, "trade": t})
        return out

    def open_bot_orders(self) -> list[dict]:
        """All non-terminal orders carrying the bot's durable ref."""
        return [order for order in self.open_account_orders()
                if str(order.get("ref") or "").startswith("trendbot:")]

    def flatten_position(self, label: str, contract, signed_qty: float):
        """Trade a SIGNED broker position to zero (kill-switch use). A
        short is covered with a BUY — an emergency flatten flattens."""
        side = "SELL" if signed_qty > 0 else "BUY"
        o = MarketOrder(side, int(abs(signed_qty)))
        o.orderRef = f"trendbot:{label}:close"
        o.account = getattr(self, "account", None) or ""
        self.dlog.log("orders", venue=self.venue, event="submit_flatten",
                      sym=label, side=side, qty=abs(signed_qty))
        return self._wait_fill(self.ib.placeOrder(contract, o), timeout=30)

    def cancel_and_verify(self, trade, timeout: float = 10.0) -> str:
        """Cancel an order and WAIT for a terminal status. Returns
        'cancelled', 'filled', or 'unknown' — callers must treat 'unknown'
        as unresolved broker state (halt, don't assume)."""
        self.ib.cancelOrder(trade.order)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.ib.waitOnUpdate(timeout=1)
            st = trade.orderStatus.status
            if st in ("Cancelled", "ApiCancelled", "Inactive"):
                return "filled" if trade.orderStatus.filled else "cancelled"
            if st == "Filled":
                return "filled"
        return "unknown"

    def cancel_stop(self, sym: str) -> str:
        """Cancel sym's stop with VERIFIED terminal state. Returns
        'none' | 'cancelled' | 'filled' | 'unknown'. The handle is only
        forgotten on a confirmed cancel; a filled stop stays tracked so
        check_stop_filled books it; 'unknown' keeps everything and callers
        MUST NOT place offsetting orders (review 2026-07-19 #4)."""
        trade = self._stop_trades.get(sym)
        if trade is None:
            return "none"
        if trade.orderStatus.status == "Filled":
            return "filled"
        outcome = self.cancel_and_verify(
            trade, timeout=getattr(self, "cancel_verify_timeout", 10.0))
        self.dlog.log("stops", venue=self.venue, event="cancel_stop",
                      sym=sym, order_id=trade.order.orderId, outcome=outcome)
        if outcome == "cancelled":
            self._stop_trades.pop(sym, None)
        return outcome

    def close_market(self, sym: str, contract, qty: int) -> Fill:
        outcome = self.cancel_stop(sym)
        if outcome == "unknown":
            # a possibly-live stop + a market sell can both fill => short.
            # Refuse; the wake retries after the broker state settles.
            raise RuntimeError(
                f"{sym}: stop cancel UNRESOLVED — refusing to close while "
                "a stop may still be working")
        # C1 guard (review 2026-07-10): the stop may have FILLED in the race
        # window before/while we cancelled (cancel-of-filled rejects async,
        # no exception). Selling qty then would create a naked short. Re-check
        # the account: if the position is already gone, the stop did the work.
        self.ib.sleep(1.0)
        held = self.positions().get(contract.conId, 0)
        if held < qty:
            self.dlog.log("orders", venue=self.venue, event="close_skipped",
                          sym=sym, qty=qty,
                          note=f"position already {held} (stop filled in "
                          "cancel race) — not selling")
            raise RuntimeError(
                f"{sym}: position already {held} at close time — stop filled "
                "in the cancel race; not selling (reconcile will book it)")
        o = MarketOrder("SELL", qty)
        o.orderRef = f"trendbot:{sym}:close"
        o.account = getattr(self, "account", None) or ""
        self.dlog.log("orders", venue=self.venue, event="submit_close",
                      sym=sym, qty=qty)
        fill = self._wait_fill(self.ib.placeOrder(contract, o))
        self.dlog.log("orders", venue=self.venue, event="close_filled",
                      sym=sym, px=fill.px, commission=fill.commission)
        return fill


class IBKRLiveExecutor(IBKRPaperExecutor):
    """Identical mechanics, live gateway. Dormant until interlocks pass."""
    venue = "ibkr_live"
    _account_check = staticmethod(lambda acct: acct.startswith("U"))
    _account_err = "not a live account (live ids start with 'U')"

    def connect(self) -> bool:
        r = super().connect()
        try:
            check_live_interlocks(self.cfg_executor, self.venue, self.account)
        except Exception:
            self.ib.disconnect()
            raise
        return r


def make_executor(cfg: dict, alert, dlog) -> ExecutorBase:
    ex_cfg = cfg["executor"]
    venue = ex_cfg["venue"]
    ib = cfg["ibkr"]
    common = dict(stop_arm_timeout_s=cfg["ops"]["stop_arm_timeout_s"],
                  alert=alert, dlog=dlog, cfg_executor=ex_cfg)
    if venue == "ibkr_paper":
        return IBKRPaperExecutor(ib["host"], ib["paper_port"],
                                 ib["trade_client_id"], **common)
    if venue == "ibkr_live":
        return IBKRLiveExecutor(ib["host"], ib["live_port"],
                                ib["trade_client_id"], **common)
    if venue == "webull_live":
        # DISABLED 2026-07-17 (external review): symbol-vs-conId state
        # incompatibility, unimplemented contract_by_conid, unconfirmed stop
        # arming, tautological account interlock. Requires an independent
        # state adapter + integration tests before it may be selectable.
        raise RuntimeError(
            "webull_live venue is DISABLED pending an independent adapter "
            "and integration test suite — see review 2026-07-17")
    raise ValueError(f"unknown executor venue: {venue}")
