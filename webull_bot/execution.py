"""Webull execution engine — place, monitor, and cancel bull put spreads via order_v3.

Uses TradeClient.order_v3 (OrderOperationV3) which supports US options.
"""
from __future__ import annotations

import os
import time
import uuid
import threading
from dataclasses import dataclass
from typing import Optional

from webull.trade.trade_client import TradeClient

# ── Symbol whitelist ──────────────────────────────────────────────────────────
# The ONLY option symbols this engine is ever allowed to touch.
# Any call that passes a symbol outside this set is rejected before any API call.
# Only PM-settled weeklies — never AM-settled monthlies (NDX, SPX-without-W).
ALLOWED_OPTION_SYMBOLS: frozenset[str] = frozenset({"SPXW", "SPX", "NDXP"})

# ── DRY RUN MODE (paper / shadow) ────────────────────────────────────────────
# Two independent gates either of which forces dry-run behavior:
#   1. Local env WEBULL_DRY_RUN=1 — explicit per-instance dry-run flag
#   2. SSM mutex /webull-bot/active-instance — if I'm not the active one
#      (see webull_bot/active_instance.py), I run paper regardless.
#
# Both gates fail-CLOSED: any error or ambiguity → dry-run.
# Env var is read on EVERY call (not cached at import) so emergency overrides
# work even mid-process.

if os.environ.get("WEBULL_DRY_RUN") == "1":
    import sys as _sys
    _banner = "█" * 70
    print(_banner, file=_sys.stderr, flush=True)
    print("█  WEBULL_DRY_RUN=1  —  PAPER MODE, NO REAL ORDERS WILL BE PLACED  █", file=_sys.stderr, flush=True)
    print(_banner, file=_sys.stderr, flush=True)


def _dry_run_active() -> bool:
    """Return True if any dry-run gate is in effect.

    Fail-CLOSED design: any uncertainty → dry-run.
    Env var checked on every call (no module-level cache) so a runtime
    override via os.environ takes effect immediately.
    """
    # Gate 1: explicit env flag
    if os.environ.get("WEBULL_DRY_RUN") == "1":
        return True
    # Gate 2: SSM mutex — fail-closed on any error or "unknown" sentinel
    try:
        from webull_bot.active_instance import is_active_instance
        if not is_active_instance():
            return True
    except Exception:
        return True
    return False


# Backward-compat alias (deprecated — use _dry_run_active() instead)
DRY_RUN = os.environ.get("WEBULL_DRY_RUN") == "1"


def _synth_fill(client_order_id: str, fill_price: float, detail: str) -> "FillResult":
    """Build a FillResult that looks like a successful fill, for dry-run mode."""
    return FillResult(
        filled=True,
        client_order_id=client_order_id,
        fill_price=fill_price,
        status="DRY_RUN_FILLED",
        detail=detail,
        short_iid=f"DRY-{client_order_id[:8]}-S",
        long_iid=f"DRY-{client_order_id[:8]}-L",
    )


def _assert_allowed_symbol(symbol: str) -> None:
    """Raise ValueError immediately if symbol is not on the whitelist.

    This is a hard fence — it fires before any network call so there is zero
    chance of accidentally touching stocks, ETFs, or other options in this
    live account.
    """
    if symbol not in ALLOWED_OPTION_SYMBOLS:
        raise ValueError(
            f"Symbol '{symbol}' is NOT on the allowed list {sorted(ALLOWED_OPTION_SYMBOLS)}. "
            "Refusing to place or close any order. Add it to ALLOWED_OPTION_SYMBOLS "
            "explicitly if this is intentional."
        )


@dataclass
class FillResult:
    filled: bool
    client_order_id: str
    fill_price: float        # net credit actually received
    status: str
    detail: str
    short_iid: str = ""      # captured after fill for close-time strike lookup
    long_iid: str = ""


@dataclass
class OrderStatus:
    client_order_id: str
    status: str              # PENDING, WORKING, FILLED, CANCELLED, REJECTED
    fill_price: Optional[float]
    raw: dict


def round_down_to_tick(price: float, tick: float = 0.05) -> float:
    """Round DOWN to the nearest valid options tick.

    SPX/SPXW/NDX/NDXP options trade in $0.05 increments. Webull rejects
    orders with invalid prices (error code [6082]: 'Premium invalid .05
    increments only, FIX_UN_DFD_CANCEL'). This snaps any computed limit
    to a valid tick.

    Round DOWN is conservative for credit spreads (we're SELLING — lower
    limit means we accept slightly less credit, which fills more easily).

    Examples:
      $2.02 → $2.00
      $1.97 → $1.95
      $1.32 → $1.30
      $0.84 → $0.80
    """
    import math
    if price <= 0:
        return 0.0
    return round(math.floor(price / tick) * tick, 2)


def round_up_to_tick(price: float, tick: float = 0.05) -> float:
    """Round UP to the nearest valid options tick.

    Used for BUY (close) limit prices: a higher max-debit makes us more
    willing to pay, increasing fill probability. Mirror of round_down_to_tick.

    Examples:
      $2.02 → $2.05
      $1.97 → $2.00
      $1.32 → $1.35
    """
    import math
    if price <= 0:
        return 0.0
    return round(math.ceil(price / tick) * tick, 2)


def _extract_webull_error(resp_or_body) -> str:
    """Pull a human-readable error from a Webull response or response body.

    Used wherever the bot reports a failure so the actual error_code /
    message surfaces in logs + Telegram instead of being buried in
    `{...}` JSON. Looks at multiple known field locations Webull uses:
    top-level error_code/message, nested data.*, items[].reject_reason.

    Returns "" if no error fields are present (caller should fall back
    to truncated raw text).
    """
    import json as _json
    try:
        if hasattr(resp_or_body, "text"):
            body = _json.loads(resp_or_body.text) if resp_or_body.text else {}
        elif isinstance(resp_or_body, str):
            body = _json.loads(resp_or_body) if resp_or_body else {}
        elif isinstance(resp_or_body, dict):
            body = resp_or_body
        else:
            return ""
    except Exception:
        return ""

    def _check(d: dict) -> str:
        if not isinstance(d, dict):
            return ""
        # Common error field names across Webull endpoints
        ec = (d.get("error_code") or d.get("errorCode")
              or d.get("reject_reason") or d.get("rejectReason"))
        msg = (d.get("message") or d.get("error_msg") or d.get("errorMsg")
               or d.get("msg") or d.get("reject_message") or d.get("rejectMessage"))
        if ec or msg:
            return f"{ec or ''}: {msg or ''}".strip(": ").strip()
        return ""

    # Try top-level
    err = _check(body)
    if err:
        return err
    # Nested under "data"
    data = body.get("data", body)
    if isinstance(data, list) and data:
        data = data[0]
    err = _check(data) if isinstance(data, dict) else ""
    if err:
        return err
    # items[] (multi-leg orders sometimes carry leg-level rejects)
    items = body.get("items") or (data.get("items") if isinstance(data, dict) else None)
    if isinstance(items, list):
        for it in items:
            err = _check(it)
            if err:
                return err
    return ""


class ExecutionEngine:
    def __init__(self, trade_client: TradeClient, account_id: str):
        self.trade = trade_client
        self.account_id = account_id

    # ── Hard guard ────────────────────────────────────────────────────────────

    def has_live_position_or_order(self) -> tuple[bool, str]:
        """Check Webull directly for any open orders or existing SPXW option positions.

        Returns (True, reason) if blocked, (False, "") if clear to trade.
        This is called before EVERY order placement — no exceptions.
        """
        try:
            # 1. Check for any working/pending orders
            resp = self.trade.order.list_open_orders(account_id=self.account_id)
            data = resp.json()
            open_count = data.get("pageSize", 0)
            orders = data.get("orders", [])
            if open_count > 0 or orders:
                return True, f"BLOCKED: {open_count} open order(s) already working"

            # 2. Check account positions — paginate to completion (max 20 pages
            #    as a sanity ceiling). Aborts on any partial-fail to fail-closed.
            holdings: list[dict] = []
            last_id = None
            for _page in range(20):
                if last_id:
                    r = self.trade.account.get_account_position(
                        account_id=self.account_id, last_instrument_id=last_id,
                    )
                else:
                    r = self.trade.account.get_account_position(
                        account_id=self.account_id,
                    )
                d = r.json()
                page_holdings = d.get("holdings", [])
                holdings.extend(page_holdings)
                if not d.get("has_next") or not page_holdings:
                    break
                last_id = page_holdings[-1]["instrument_id"]
            else:
                # Hit the 20-page ceiling without seeing has_next=False —
                # fail-closed so we never trade with incomplete position view.
                return True, "BLOCKED: position pagination exceeded 20 pages — fail-closed"

            spxw_opts = [
                h for h in holdings
                if h.get("symbol") in ALLOWED_OPTION_SYMBOLS
                and h.get("instrument_type") == "OPTION"
            ]
            if spxw_opts:
                detail = ", ".join(
                    f"{h['symbol']} qty={h['qty']}" for h in spxw_opts
                )
                return True, f"BLOCKED: existing option position(s): {detail}"

            return False, ""

        except Exception as exc:
            # Fail safe — if we can't confirm it's clear, do NOT trade
            return True, f"BLOCKED: could not verify clear state ({exc})"

    def preview_spread(
        self,
        symbol: str,
        expiry: str,           # YYYY-MM-DD
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,    # net credit limit (e.g., 2.00)
    ) -> dict:
        """Preview a bull put spread order. Returns raw API response dict."""
        _assert_allowed_symbol(symbol)
        order = self._build_order(symbol, expiry, short_strike, long_strike, quantity, limit_price)
        resp = self.trade.order_v3.preview_order(
            account_id=self.account_id,
            preview_orders=[order],
        )
        return {"status_code": resp.status_code, "body": resp.text}

    def place_spread(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        max_retries: int = 5,           # legacy — unused
        retry_price_step: float = 0.05, # legacy — unused
        retry_wait_seconds: int = 60,   # total budget for LIMIT walk-down loop
        fill_timeout_seconds: int = 300, # legacy — unused
        entry_market_fallback: bool = False,  # HARD-DISABLED in code (T1.4)
        # walk-down params (T1.2):
        walk_down_step: float = 0.05,   # how much to drop per walk
        walk_down_interval_s: int = 15, # how often to walk down
        walk_down_max_steps: int = 3,   # cap walks (3 walks + initial = 4 prices over 60s)
        poll_interval_s: float = 2.0,   # poll cadence (T1.3 — was 5s)
    ) -> FillResult:
        """Place a bull put spread with walk-down LIMIT execution (rewritten 2026-05-20).

        DESIGN:
          1. Snapshot pre-existing SPXW holdings (for post-order reconciliation)
          2. Place LIMIT at `limit_price` (already round_down_to_tick'd)
          3. Poll every 2s. Every 15s without fill, atomically `replace_order` to drop $0.05
          4. Up to 3 walks = 4 price points tried over 60s
          5. If fill: return immediately
          6. If timeout: cancel safely (HTTP 417 handling), then reconcile against
             broker positions. If a new SPXW spread appeared matching our strikes,
             treat as filled regardless of polling status — use broker's cost basis.
          7. MARKET fallback is HARD-DISABLED in code: Webull combo MARKET gives
             50-77% slippage (verified 2026-05-15, 2026-05-20). Never use.

        Returns FillResult with filled=True only when we have high confidence
        the order is at the broker (either polling confirmed OR broker reconciled).
        """
        # ── Symbol whitelist ─────────────────────────────────────────────
        _assert_allowed_symbol(symbol)

        # ── DRY RUN gate ─────────────────────────────────────────────────
        if _dry_run_active():
            cid = uuid.uuid4().hex
            print(f"[DRY_RUN] place_spread {symbol} {short_strike}/{long_strike} qty={quantity} "
                  f"limit={limit_price} → synthesizing fill at {limit_price}", flush=True)
            return _synth_fill(cid, float(limit_price),
                               f"DRY_RUN: would have placed {symbol} {int(short_strike)}/{int(long_strike)} qty={quantity}")

        # ── T1.4: HARD-DISABLE MARKET FALLBACK ───────────────────────────
        # entry_market_fallback parameter is accepted for backward-compat but
        # IGNORED. Webull combo MARKET orders execute leg-by-leg at NBBO,
        # producing 50-77% slippage. There is no documented midpoint-peg or
        # complex-MARKET order type in the SDK. We refuse to use MARKET on combos.
        if entry_market_fallback:
            print(f"[WARN] entry_market_fallback=true IGNORED (hard-disabled in code 2026-05-20 — Webull combo MARKET=77% slippage)", flush=True)

        # ── Hard guard: never place if already live (UNCONDITIONAL) ──────
        blocked, reason = self.has_live_position_or_order()
        if blocked:
            return FillResult(
                filled=False, client_order_id="", fill_price=0.0,
                status="BLOCKED", detail=reason,
            )

        # ── T1.7: Snapshot SPXW holdings BEFORE placing (for reconciliation) ─
        pre_holdings = self._snapshot_spxw_holdings()

        # ── PLACE INITIAL LIMIT ──────────────────────────────────────────
        cid_limit = uuid.uuid4().hex
        current_limit = round(float(limit_price), 2)
        order = self._build_order(
            symbol, expiry, short_strike, long_strike, quantity, current_limit,
            client_order_id=cid_limit, order_type="LIMIT",
        )
        resp = self.trade.order_v3.place_order(
            account_id=self.account_id, new_orders=[order],
        )
        if resp.status_code not in (200, 201):
            wb_err = _extract_webull_error(resp)
            detail = (f"limit attempt: HTTP {resp.status_code} | {wb_err}" if wb_err
                      else f"limit attempt: HTTP {resp.status_code}: {resp.text[:300]}")
            return FillResult(
                filled=False, client_order_id=cid_limit, fill_price=0.0,
                status="REJECTED", detail=detail,
            )

        # ── T1.2 + T1.3: WALK-DOWN POLL LOOP ─────────────────────────────
        deadline = time.monotonic() + retry_wait_seconds
        last_walk = time.monotonic()
        walks_done = 0
        prices_tried = [current_limit]

        while time.monotonic() < deadline:
            time.sleep(poll_interval_s)
            status = self.get_order_status(cid_limit)

            if status.status == "FILLED":
                price = status.fill_price or current_limit
                return FillResult(
                    filled=True, client_order_id=cid_limit, fill_price=price,
                    status="FILLED",
                    detail=f"limit filled @ {price} after {walks_done} walk-downs (tried {prices_tried})",
                )
            if status.status in ("CANCELLED", "REJECTED"):
                wb_err = _extract_webull_error(status.raw)
                detail = (f"limit attempt {status.status} | {wb_err}"
                          if wb_err else f"limit attempt {status.status}")
                return FillResult(
                    filled=False, client_order_id=cid_limit, fill_price=0.0,
                    status=status.status, detail=detail,
                )

            # Walk down if time elapsed and walks remain
            elapsed_since_walk = time.monotonic() - last_walk
            if elapsed_since_walk >= walk_down_interval_s and walks_done < walk_down_max_steps:
                new_limit = round(current_limit - walk_down_step, 2)
                # Defensive: don't go negative or absurdly low
                if new_limit < 0.05:
                    break
                modify = self._build_order(
                    symbol, expiry, short_strike, long_strike, quantity, new_limit,
                    client_order_id=cid_limit, order_type="LIMIT",
                )
                try:
                    replace_resp = self.trade.order_v3.replace_order(
                        account_id=self.account_id, modify_orders=[modify],
                    )
                    if replace_resp.status_code in (200, 201):
                        current_limit = new_limit
                        walks_done += 1
                        last_walk = time.monotonic()
                        prices_tried.append(current_limit)
                        print(f"[walk-down] step {walks_done}/{walk_down_max_steps}: limit → ${current_limit}", flush=True)
                    else:
                        # Replace failed (e.g. order already filling) — just keep polling
                        wb_err = _extract_webull_error(replace_resp)
                        print(f"[walk-down] replace failed: HTTP {replace_resp.status_code} {wb_err}; continuing poll on ${current_limit}", flush=True)
                        last_walk = time.monotonic()  # avoid spamming replace attempts
                except Exception as exc:
                    print(f"[walk-down] replace exception: {exc}; continuing poll", flush=True)
                    last_walk = time.monotonic()

        # ── TIMEOUT: cancel safely, then reconcile against broker ────────
        # T1.5: HTTP 417 cancel safety
        cancel_safe_result = self._cancel_with_safety(cid_limit)
        if cancel_safe_result["filled"]:
            # Cancel saw it was actually filled — accept the fill
            price = cancel_safe_result["fill_price"] or current_limit
            return FillResult(
                filled=True, client_order_id=cid_limit, fill_price=price,
                status="FILLED",
                detail=f"cancel-time-check found filled @ {price} (tried prices: {prices_tried})",
            )

        # ── T1.7: Post-order broker reconciliation ───────────────────────
        # Even if cancel succeeded and polling never saw fill, broker may have
        # filled and confirmation is lagging. Snapshot positions and look for
        # new SPXW matching our strikes.
        time.sleep(2)  # give broker a moment to update positions endpoint
        post_holdings = self._snapshot_spxw_holdings()
        reconciled = self._find_new_spread_in_holdings(
            pre_holdings, post_holdings, short_strike, long_strike,
        )
        if reconciled is not None:
            broker_credit = reconciled["credit"]
            print(f"[reconcile] orphan detected via broker holdings — short_cost=${reconciled['short_cost']:.2f} long_cost=${reconciled['long_cost']:.2f} net_credit=${broker_credit:.2f}", flush=True)
            return FillResult(
                filled=True, client_order_id=cid_limit, fill_price=broker_credit,
                status="FILLED",
                detail=f"RECONCILED from broker holdings after timeout (tried {prices_tried}); net_credit=${broker_credit:.2f}",
            )

        # Truly didn't fill — broker has no matching position
        return FillResult(
            filled=False, client_order_id=cid_limit, fill_price=0.0,
            status="TIMEOUT",
            detail=f"LIMIT not filled in {retry_wait_seconds}s ({walks_done} walks, tried {prices_tried}); broker has no matching position; entry_market_fallback DISABLED (combo MARKET unsafe)",
        )

    def place_spread_market(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
    ) -> FillResult:
        """Force-place a bull put spread at MARKET price — bypass mode only.

        Rules:
        - Symbol whitelist (ALLOWED_OPTION_SYMBOLS) is still enforced — hard stop.
        - Does NOT call has_live_position_or_order() — caller is responsible for
          showing a confirmation prompt before calling this.
        - Single attempt, no retries, no loops. Places exactly 1 order.
        - Uses order_type=MARKET (Webull DOES support MARKET for combo options
          orders — verified 2026-05-14; the previous "use LIMIT at $0.05 because
          MKT is rejected" workaround was based on a wrong code comment. Webull
          rejects "MKT" the abbreviation but accepts "MARKET" the full word.)
        - Polls every 2s up to 60s for the fill confirmation.
        """
        _assert_allowed_symbol(symbol)

        # ── DRY RUN gate ──────────────────────────────────────────────────
        if _dry_run_active():
            cid = uuid.uuid4().hex
            print(f"[DRY_RUN] place_spread_market {symbol} {short_strike}/{long_strike} qty={quantity} "
                  f"→ synthesizing market fill", flush=True)
            return _synth_fill(cid, 0.0,
                               f"DRY_RUN: would have force-market placed {symbol} {int(short_strike)}/{int(long_strike)} qty={quantity}")

        client_order_id = uuid.uuid4().hex
        # MARKET orders ignore limit_price but Webull's API schema still requires
        # the field. Pass a placeholder.
        force_limit = "0.05"

        def _fmts(s: float) -> str:
            return str(int(s)) if s == int(s) else str(s)

        order = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "SELL",
            "order_type": "MARKET",        # Webull accepts MARKET (not "MKT")
            "limit_price": force_limit,   # ignored for MARKET, schema-required
            "quantity": str(quantity),
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "position_intent": "SELL_TO_OPEN",
            "legs": [
                {
                    "side": "SELL",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": _fmts(short_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
                {
                    "side": "BUY",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": _fmts(long_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
            ],
        }

        resp = self.trade.order_v3.place_order(
            account_id=self.account_id,
            new_orders=[order],
        )

        if resp.status_code not in (200, 201):
            wb_err = _extract_webull_error(resp)
            detail = (f"FORCE MARKET HTTP {resp.status_code} | {wb_err}" if wb_err
                      else f"FORCE MARKET HTTP {resp.status_code}: {resp.text[:300]}")
            return FillResult(
                filled=False,
                client_order_id=client_order_id,
                fill_price=0.0,
                status="REJECTED",
                detail=detail,
            )

        # Snapshot pre-existing SPXW position iids so we can identify the new legs after fill
        prior_iids = {h["instrument_id"] for h in self._fetch_spxw_positions()}

        # Market orders should fill within seconds — poll every 2s up to 60s
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(2)
            status = self.get_order_status(client_order_id)
            if status.status == "FILLED":
                price = status.fill_price or 0.0
                short_iid, long_iid = self._capture_new_legs(prior_iids)
                return FillResult(
                    filled=True,
                    client_order_id=client_order_id,
                    fill_price=price,
                    status="FILLED",
                    detail=f"FORCE MARKET filled at {price:.2f}",
                    short_iid=short_iid,
                    long_iid=long_iid,
                )
            if status.status in ("CANCELLED", "REJECTED"):
                wb_err = _extract_webull_error(status.raw)
                detail = (f"FORCE MARKET order {status.status} | {wb_err}" if wb_err
                          else f"FORCE MARKET order {status.status}")
                return FillResult(
                    filled=False,
                    client_order_id=client_order_id,
                    fill_price=0.0,
                    status=status.status,
                    detail=detail,
                )

        return FillResult(
            filled=False,
            client_order_id=client_order_id,
            fill_price=0.0,
            status="TIMEOUT",
            detail="FORCE MARKET order not confirmed within 60s — check broker manually",
        )

    def close_spread_market(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        entry_credit: float,
    ) -> FillResult:
        """Buy back the spread at MARKET to close (debit order).

        Used by the SL trigger path: when mark hits stop, we want OUT now —
        slippage is acceptable, missing the close is not. Webull accepts
        order_type=MARKET for combo options orders (verified 2026-05-14).

        For a credit spread, closing means:
        - BUY the short put (was sold to open)
        - SELL the long put (was bought to open)
        Net effect = pay a debit (the spread's current ask).

        `entry_credit` is no longer used to derive a limit ceiling — kept in
        the signature for backward-compat with callers that pass it.
        """
        _assert_allowed_symbol(symbol)

        # ── DRY RUN gate ──────────────────────────────────────────────────
        if _dry_run_active():
            cid = uuid.uuid4().hex
            # In dry-run, "close at market" assumes we paid 2× credit (stop trigger)
            est_debit = entry_credit * 2.0
            print(f"[DRY_RUN] close_spread_market {symbol} {short_strike}/{long_strike} qty={quantity} "
                  f"→ synthesized close at est_debit={est_debit:.2f}", flush=True)
            return _synth_fill(cid, est_debit,
                               f"DRY_RUN: would have closed {symbol} {int(short_strike)}/{int(long_strike)} qty={quantity} at est ${est_debit:.2f}")

        client_order_id = uuid.uuid4().hex
        # MARKET orders ignore limit_price but Webull schema still requires the
        # field. Tick-align it anyway (defensive — Webull error 6082).
        placeholder_limit = round_up_to_tick(round(entry_credit * 2.5, 2))

        def fmt_strike(s: float) -> str:
            return str(int(s)) if s == int(s) else str(s)

        order = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "BUY",
            "order_type": "MARKET",                 # was LIMIT — switched 2026-05-14
            "limit_price": str(placeholder_limit), # ignored for MARKET
            "quantity": str(quantity),
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "legs": [
                {
                    "side": "BUY",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(short_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                    "position_intent": "BUY_TO_CLOSE",   # per-leg — required by Webull
                },
                {
                    "side": "SELL",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(long_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                    "position_intent": "SELL_TO_CLOSE",  # per-leg — required by Webull
                },
            ],
        }

        resp = self.trade.order_v3.place_order(
            account_id=self.account_id,
            new_orders=[order],
        )

        if resp.status_code not in (200, 201):
            wb_err = _extract_webull_error(resp)
            detail = (f"close HTTP {resp.status_code} | {wb_err}" if wb_err
                      else f"close HTTP {resp.status_code}: {resp.text[:300]}")
            return FillResult(
                filled=False,
                client_order_id=client_order_id,
                fill_price=0.0,
                status="REJECTED",
                detail=detail,
            )

        # Poll up to 2 minutes for close fill. Capture last-known status info
        # so that if we exit on REJECTED, the detail string carries Webull's
        # actual reason (e.g. FIX_UN_DFD_CANCEL).
        last_rejected_detail = ""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(5)
            status = self.get_order_status(client_order_id)
            if status.status == "FILLED":
                # 2026-06-04: was `status.fill_price or max_debit` — but max_debit
                # is undefined in this scope (NameError when the broker reports
                # FILLED without a fill_price). Fall back to placeholder_limit
                # (≈2.5× entry credit, the market-order debit estimate) so a
                # genuinely-filled SL close is reported as filled, not crashed.
                return FillResult(
                    filled=True,
                    client_order_id=client_order_id,
                    fill_price=status.fill_price or placeholder_limit,
                    status="FILLED",
                    detail="stop-loss close filled",
                )
            if status.status in ("CANCELLED", "REJECTED"):
                wb_err = _extract_webull_error(status.raw)
                last_rejected_detail = (f"close {status.status} | {wb_err}"
                                        if wb_err
                                        else f"close {status.status}")
                # Return the rejected status so the caller (monitor) can
                # see WHY it failed and decide retry strategy.
                return FillResult(
                    filled=False,
                    client_order_id=client_order_id,
                    fill_price=0.0,
                    status=status.status,
                    detail=last_rejected_detail,
                )

        return FillResult(
            filled=False,
            client_order_id=client_order_id,
            fill_price=0.0,
            status="TIMEOUT",
            detail="stop-loss close not confirmed within 2 min",
        )

    # ── Fast parallel helpers ─────────────────────────────────────────────────

    def _capture_new_legs(
        self, prior_iids: set[str], retries: int = 3, delay_s: float = 1.0,
    ) -> tuple[str, str]:
        """After a fill, fetch positions and identify the newly-opened short/long leg iids.

        Retries up to `retries` times with `delay_s` between attempts because the
        Webull positions endpoint can lag the fill confirmation by a second or two.
        Returns ("", "") only if every attempt fails — close-all will then fall
        back to matching by qty/sign against state-saved strikes.
        """
        for attempt in range(retries):
            try:
                positions = self._fetch_spxw_positions()
            except Exception:
                positions = []
            new_legs = [p for p in positions if p["instrument_id"] not in prior_iids]
            short_iid = next(
                (p["instrument_id"] for p in new_legs if int(p.get("qty", 0)) < 0), ""
            )
            long_iid = next(
                (p["instrument_id"] for p in new_legs if int(p.get("qty", 0)) > 0), ""
            )
            if short_iid and long_iid:
                return short_iid, long_iid
            if attempt < retries - 1:
                time.sleep(delay_s)
        return short_iid, long_iid

    def _fetch_strike_map(self) -> dict[str, float]:
        """Build instrument_id → strike from today's order history. Single large page."""
        iid_to_strike: dict[str, float] = {}
        try:
            r = self.trade.order.list_today_orders(
                account_id=self.account_id, page_size=50
            )
            for o in r.json().get("orders", []):
                for item in o.get("items", []):
                    iid = item.get("instrument_id")
                    strike = item.get("strike_price") or item.get("strikPrice")
                    if iid and strike:
                        iid_to_strike[iid] = float(strike)
        except Exception:
            pass
        return iid_to_strike

    def _fetch_spxw_positions(self) -> list[dict]:
        """Fetch all SPXW/SPX option positions in one or two calls."""
        all_holdings: list[dict] = []
        try:
            r = self.trade.account.get_account_position(account_id=self.account_id)
            d = r.json()
            holdings = d.get("holdings", [])
            all_holdings.extend(holdings)
            if d.get("has_next") and holdings:
                r2 = self.trade.account.get_account_position(
                    account_id=self.account_id,
                    last_instrument_id=holdings[-1]["instrument_id"],
                )
                all_holdings.extend(r2.json().get("holdings", []))
        except Exception:
            pass
        return [
            h for h in all_holdings
            if h.get("symbol") in ALLOWED_OPTION_SYMBOLS
            and h.get("instrument_type") == "OPTION"
        ]

    # ── T1.5 + T1.7 helpers (added 2026-05-20) ───────────────────────────────

    def _snapshot_spxw_holdings(self) -> dict[str, dict]:
        """Return current SPXW/SPX/NDXP option holdings keyed by instrument_id.

        Used by place_spread() for pre/post reconciliation: any iid present
        AFTER but not BEFORE is a newly-opened leg. Each value carries
        whatever fields Webull returns (qty, cost_price, last_price, etc.)
        so the caller can compute net credit from the new legs.

        Defensively returns {} on any error rather than raising — the caller
        treats an empty snapshot as "no prior positions" which is safe
        because the hard guard (`has_live_position_or_order`) already
        ran and confirmed clear state.
        """
        try:
            positions = self._fetch_spxw_positions()
        except Exception as exc:
            print(f"[snapshot] error fetching SPXW positions: {exc}", flush=True)
            return {}
        return {h["instrument_id"]: h for h in positions if h.get("instrument_id")}

    def _find_new_spread_in_holdings(
        self,
        pre: dict[str, dict],
        post: dict[str, dict],
        short_strike: float,
        long_strike: float,
    ) -> Optional[dict]:
        """Identify a newly-opened spread matching (short_strike, long_strike).

        Returns dict {short_iid, long_iid, short_cost, long_cost, credit} or None.

        Approach:
          1. new_iids = post-keys minus pre-keys
          2. Resolve each new iid's strike via _fetch_strike_map() (today's orders)
          3. Match short_strike (qty<0) and long_strike (qty>0)
          4. Compute net credit = short_cost - long_cost

        Net credit is reported as a POSITIVE number (per-share, not ×100).
        Returns None if we can't unambiguously identify both legs.
        """
        new_iids = set(post.keys()) - set(pre.keys())
        if not new_iids:
            return None

        # Build iid → strike map from today's order history
        strike_map = self._fetch_strike_map()

        short_match = None
        long_match = None
        for iid in new_iids:
            holding = post[iid]
            try:
                qty = int(holding.get("qty", 0))
            except (ValueError, TypeError):
                continue
            strike = strike_map.get(iid)
            if strike is None:
                continue
            if abs(strike - short_strike) < 0.01 and qty < 0:
                short_match = (iid, holding)
            elif abs(strike - long_strike) < 0.01 and qty > 0:
                long_match = (iid, holding)

        if not (short_match and long_match):
            print(f"[reconcile] could not identify both legs: short={short_match}, long={long_match}, new_iids={new_iids}, strike_map_size={len(strike_map)}", flush=True)
            return None

        def _cost(h: dict) -> float:
            for k in ("cost_price", "costPrice", "average_cost", "averageCost", "avg_cost"):
                v = h.get(k)
                if v is not None:
                    try:
                        return abs(float(v))
                    except (ValueError, TypeError):
                        continue
            return 0.0

        short_cost = _cost(short_match[1])
        long_cost = _cost(long_match[1])
        credit = round(short_cost - long_cost, 2)
        return {
            "short_iid": short_match[0],
            "long_iid": long_match[0],
            "short_cost": short_cost,
            "long_cost": long_cost,
            "credit": credit,
        }

    def _cancel_with_safety(self, client_order_id: str) -> dict:
        """Cancel an order; if cancel fails with HTTP 417 INVALID_PARAMETER,
        re-check the order's actual status — the cancel may have failed because
        it already FILLED.

        Returns dict:
          {"filled": bool, "fill_price": Optional[float], "cancelled": bool, "detail": str}

        T1.5 fix: previously, cancel failure was treated as "didn't cancel" and
        the bot escalated to MARKET fallback on top of an already-filled order
        (verified 2026-05-20 — caused doubled position when force_place ran twice
        and one of them silently filled via belated cancel).
        """
        try:
            resp = self.trade.order_v3.cancel_order(
                account_id=self.account_id,
                client_order_id=client_order_id,
            )
            http = resp.status_code
            if http in (200, 201):
                return {"filled": False, "fill_price": None, "cancelled": True,
                        "detail": f"cancel ok HTTP {http}"}
            # Non-2xx: check actual status before deciding it didn't cancel
            wb_err = _extract_webull_error(resp)
            print(f"[cancel] HTTP {http} ({wb_err}) — rechecking order status before escalating", flush=True)
        except Exception as exc:
            print(f"[cancel] exception {exc} — rechecking order status before escalating", flush=True)
            wb_err = str(exc)
            http = 0

        # Recheck — may have filled between our last poll and our cancel attempt
        try:
            time.sleep(1)  # let broker settle
            status = self.get_order_status(client_order_id)
            if status.status == "FILLED":
                price = status.fill_price
                print(f"[cancel] order was FILLED at {price} — cancel HTTP {http} was due to filled state, NOT a real error", flush=True)
                return {"filled": True, "fill_price": price, "cancelled": False,
                        "detail": f"cancel-time recheck found FILLED @ {price}"}
            if status.status in ("CANCELLED", "REJECTED"):
                return {"filled": False, "fill_price": None, "cancelled": True,
                        "detail": f"cancel-time recheck: order already {status.status}"}
            # Still working — cancel genuinely failed. Try one more time.
            try:
                resp2 = self.trade.order_v3.cancel_order(
                    account_id=self.account_id,
                    client_order_id=client_order_id,
                )
                return {"filled": False, "fill_price": None,
                        "cancelled": resp2.status_code in (200, 201),
                        "detail": f"retry cancel HTTP {resp2.status_code}"}
            except Exception as exc2:
                return {"filled": False, "fill_price": None, "cancelled": False,
                        "detail": f"retry cancel exception: {exc2}"}
        except Exception as exc:
            return {"filled": False, "fill_price": None, "cancelled": False,
                    "detail": f"cancel-time recheck failed: {exc}"}

    def _build_close_plan(
        self, expiry: str, symbol: str,
        known_iid_strikes: Optional[dict[str, float]] = None,
        side_strikes: Optional[list[dict]] = None,
    ) -> tuple[list[dict], list[dict]]:
        """Fetch positions + strike map in parallel and return (spreads_to_close, errors).

        Hard-rejects any symbol not on ALLOWED_OPTION_SYMBOLS before touching the API.

        Strike resolution order:
          1. `known_iid_strikes` — authoritative iid→strike map (from state.json)
          2. Live order-history strike map (rarely useful — combos return shared iid)
          3. `side_strikes` — list of {"qty", "short_strike", "long_strike"} dicts
             from state.open_position, used as a last-resort fallback when iids
             never got captured at placement time. Match by abs(qty).

        Each spread dict has: short_strike, long_strike, qty, current_mark, max_debit, symbol, expiry
        """
        _assert_allowed_symbol(symbol)   # hard fence before any network call

        iid_map: dict[str, float] = {}
        positions: list[dict] = []
        errors: list[dict] = []

        def _get_map():
            iid_map.update(self._fetch_strike_map())

        def _get_pos():
            positions.extend(self._fetch_spxw_positions())

        t1 = threading.Thread(target=_get_map, daemon=True)
        t2 = threading.Thread(target=_get_pos, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Merge caller-provided iid→strike map (e.g. from state.json). This is the
        # authoritative source because order history returns the combo's iid, not legs.
        if known_iid_strikes:
            for k, v in known_iid_strikes.items():
                iid_map.setdefault(k, float(v))

        if not positions:
            return [], [{"info": "no SPXW option positions — nothing to close"}]

        shorts = sorted(
            [h for h in positions if int(h["qty"]) < 0],
            key=lambda x: abs(int(x["qty"])), reverse=True
        )
        longs = [h for h in positions if int(h["qty"]) > 0]

        spreads = []

        def _fmts(s) -> str:
            if s is None: return "?"
            return str(int(s)) if s == int(s) else str(s)

        for short_pos in shorts:
            qty = abs(int(short_pos["qty"]))
            short_iid = short_pos["instrument_id"]
            short_strike = iid_map.get(short_iid)

            long_pos = next((lp for lp in longs if int(lp["qty"]) == qty), None)
            if long_pos is None and longs:
                long_pos = longs[0]

            long_iid = long_pos["instrument_id"] if long_pos else None
            long_strike = iid_map.get(long_iid) if long_iid else None

            # Fallback: match by qty against state-saved side_strikes
            if (short_strike is None or long_strike is None) and side_strikes:
                hit = next(
                    (s for s in side_strikes if int(s.get("qty", 0)) == qty), None
                )
                if hit:
                    if short_strike is None:
                        short_strike = float(hit["short_strike"])
                    if long_strike is None:
                        long_strike = float(hit["long_strike"])
                    errors.append({
                        "info": f"resolved {short_iid}/{long_iid} via qty-match fallback "
                                f"→ {int(short_strike)}/{int(long_strike)} (qty={qty})"
                    })

            if short_strike is None or long_strike is None:
                errors.append({
                    "error": f"unknown strikes for {short_iid}/{long_iid} — "
                             f"map has {len(iid_map)} entries, no qty-match in state"
                })
                continue

            short_mark = float(short_pos.get("last_price", 0))
            long_mark  = float(long_pos.get("last_price", 0)) if long_pos else 0
            spread_width = abs(short_strike - long_strike)
            max_debit = max(round(spread_width * 0.20, 2), 5.00)

            spreads.append({
                "spread":       f"{_fmts(short_strike)}/{_fmts(long_strike)}P",
                "qty":          qty,
                "current_mark": round(short_mark - long_mark, 2),
                "max_debit":    max_debit,
                "short_strike": short_strike,
                "long_strike":  long_strike,
                "symbol":       symbol,
                "expiry":       expiry,
            })

            longs = [lp for lp in longs if lp["instrument_id"] != (long_iid or "")]

        return spreads, errors

    def preview_close_all_today(
        self, expiry: str, symbol: str = "SPXW",
        known_iid_strikes: Optional[dict[str, float]] = None,
        side_strikes: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Return what close_all_today() would close without placing any orders.

        Both API calls (positions + strike map) fire in parallel — typically
        returns in under 2 seconds.  Call this first, show the user the table,
        get confirmation, then call close_all_today().

        Returns a list of dicts: [{spread, qty, current_mark, max_debit}, ...]
        Error/info dicts are appended if anything went wrong.
        """
        spreads, errors = self._build_close_plan(
            expiry, symbol, known_iid_strikes, side_strikes,
        )
        if errors and not spreads:
            return errors
        return spreads + errors

    def close_all_today(
        self, expiry: str, symbol: str = "SPXW",
        known_iid_strikes: Optional[dict[str, float]] = None,
        side_strikes: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Emergency close: find every open SPXW option position and close ALL simultaneously.

        Workflow:
          1. _build_close_plan(): fetch positions + strike map in parallel (no sleeps)
          2. Fire ALL close orders at the same time via threads
          3. Each thread polls independently every 2s up to 45s for fill

        Returns a list of result dicts, one per spread attempted.
        """
        spreads, errors = self._build_close_plan(
            expiry, symbol, known_iid_strikes, side_strikes,
        )
        if not spreads:
            return errors

        thread_results: list[dict] = []
        lock = threading.Lock()

        def _place_and_poll(sp: dict) -> None:
            cid = uuid.uuid4().hex
            qty = sp["qty"]
            short_strike = sp["short_strike"]
            long_strike  = sp["long_strike"]
            max_debit    = sp["max_debit"]

            # ── DRY RUN gate ──────────────────────────────────────────────
            if _dry_run_active():
                est_debit = float(sp.get("current_mark", 0)) or float(max_debit) / 2
                print(f"[DRY_RUN] close_all leg {sp.get('spread')} qty={qty} → synth close at {est_debit:.2f}", flush=True)
                with lock:
                    thread_results.append({
                        "spread":      sp["spread"],
                        "qty":         qty,
                        "max_debit":   max_debit,
                        "placed":      True,
                        "http":        200,
                        "fill_status": "DRY_RUN_FILLED",
                        "fill_price":  est_debit,
                        "detail":      "DRY_RUN — no real order sent",
                    })
                return

            def _fmts(s: float) -> str:
                return str(int(s)) if s == int(s) else str(s)

            order = {
                "client_order_id": cid,
                "combo_type": "NORMAL",
                "option_strategy": "VERTICAL",
                "instrument_type": "OPTION",
                "market": "US",
                "symbol": sp["symbol"],
                "side": "BUY",
                "order_type": "LIMIT",
                "limit_price": str(round_up_to_tick(max_debit)),
                "quantity": str(qty),
                "entrust_type": "QTY",
                "time_in_force": "DAY",
                "legs": [
                    {
                        "side": "BUY", "quantity": str(qty), "symbol": sp["symbol"],
                        "strike_price": _fmts(short_strike),
                        "option_expire_date": sp["expiry"],
                        "instrument_type": "OPTION", "option_type": "PUT", "market": "US",
                        "position_intent": "BUY_TO_CLOSE",
                    },
                    {
                        "side": "SELL", "quantity": str(qty), "symbol": sp["symbol"],
                        "strike_price": _fmts(long_strike),
                        "option_expire_date": sp["expiry"],
                        "instrument_type": "OPTION", "option_type": "PUT", "market": "US",
                        "position_intent": "SELL_TO_CLOSE",
                    },
                ],
            }

            def _log(msg: str) -> None:
                from datetime import datetime as _dt
                print(f"[{_dt.now().strftime('%H:%M:%S')}] [CLOSE {sp['spread']}] {msg}", flush=True)

            _log(f"submitting close @ max_debit={max_debit}")
            resp = self.trade.order_v3.place_order(
                account_id=self.account_id, new_orders=[order]
            )
            placed_ok = resp.status_code in (200, 201)
            _log(f"submit HTTP {resp.status_code} — {'accepted' if placed_ok else 'REJECTED'}")
            if not placed_ok:
                _log(f"reject body: {resp.text[:200]}")

            fill_status = "UNKNOWN"
            fill_price  = None
            if placed_ok:
                deadline = time.monotonic() + 45
                tick = 0
                while time.monotonic() < deadline:
                    time.sleep(2)
                    tick += 1
                    s = self.get_order_status(cid)
                    if tick % 3 == 0:
                        _log(f"poll {tick*2}s — status={s.status}")
                    if s.status == "FILLED":
                        fill_status = "FILLED"
                        fill_price  = s.fill_price
                        _log(f"FILLED @ {fill_price}")
                        break
                    if s.status in ("CANCELLED", "REJECTED"):
                        fill_status = s.status
                        _log(f"order {s.status} — bailing")
                        break
                else:
                    _log("timed out after 45s without fill")

            result = {
                "spread":      sp["spread"],
                "qty":         qty,
                "max_debit":   max_debit,
                "placed":      placed_ok,
                "http":        resp.status_code,
                "fill_status": fill_status,
                "fill_price":  fill_price,
                "detail":      resp.text[:200] if not placed_ok else "",
            }
            with lock:
                thread_results.append(result)

        # Fire all close orders simultaneously
        threads = [
            threading.Thread(target=_place_and_poll, args=(sp,), daemon=True)
            for sp in spreads
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        return thread_results + errors

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            resp = self.trade.order_v3.cancel_order(
                account_id=self.account_id,
                client_order_id=client_order_id,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def get_order_status(self, client_order_id: str) -> OrderStatus:
        """Fetch order status with HTTP-429 backoff.

        Webull rate-limits get_order_detail when called rapidly (observed
        2026-05-15: a 2s polling loop hit HTTP 429 'TOO_MANY_REQUESTS' after
        ~3-5 calls, returning UNKNOWN even though the order had filled in
        seconds). This caused force-market polling to time out at 60s while
        the order was actually FILLED at second 5.

        Behavior: if the SDK raises a ServerException with HTTP 429, sleep
        and retry up to 3 times with exponential backoff (0.5s, 1s, 2s).
        Other errors fall through as UNKNOWN.
        """
        import json  # 'time' is module-level; do not shadow it locally
        for attempt in range(3):
            try:
                resp = self.trade.order_v3.get_order_detail(
                    account_id=self.account_id,
                    client_order_id=client_order_id,
                )
                if resp.status_code != 200:
                    return OrderStatus(client_order_id, "UNKNOWN", None, {"http_status": resp.status_code})

                body = json.loads(resp.text)
                # Note: body['items'][i]['instrument_id'] is NOT a reliable
                # per-leg id in Webull responses — observed 2026-05-15 that
                # both legs of a combo show the SAME iid (apparently the
                # underlying symbol's id, not the option contract). To get
                # real leg iids, query account positions via _capture_new_legs.
                data = body.get("data", body)
                if isinstance(data, list):
                    data = data[0] if data else {}

                raw_status = str(data.get("status", data.get("orderStatus", "UNKNOWN"))).upper()
                status = _normalize_status(raw_status)

                fill_price = None
                avg_price = data.get("avgFilledPrice") or data.get("filledPrice")
                if avg_price:
                    try:
                        fill_price = float(avg_price)
                    except (ValueError, TypeError):
                        pass

                return OrderStatus(client_order_id, status, fill_price, data)
            except Exception as exc:
                msg = str(exc)
                # Detect Webull's 429 rate-limit response and back off
                if "429" in msg or "TOO_MANY_REQUESTS" in msg or "to many requests" in msg:
                    if attempt < 2:
                        time.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s, 2s
                        continue
                    # Exhausted retries — return RATE_LIMITED so caller can
                    # distinguish this from a genuinely missing order.
                    return OrderStatus(client_order_id, "RATE_LIMITED", None,
                                       {"error": "HTTP 429 after retries"})
                return OrderStatus(client_order_id, "UNKNOWN", None, {"error": msg})
        return OrderStatus(client_order_id, "UNKNOWN", None, {"error": "fell through retry loop"})

    @staticmethod
    def _build_order(
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        client_order_id: Optional[str] = None,
        order_type: str = "LIMIT",
    ) -> dict:
        return ExecutionEngine._build_order_dict(
            symbol, expiry, short_strike, long_strike, quantity, limit_price,
            client_order_id, order_type=order_type,
        )

    @staticmethod
    def _build_order_dict(
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        client_order_id: Optional[str] = None,
        order_type: str = "LIMIT",          # "LIMIT" or "MARKET". Webull accepts
                                            # "MARKET" (full word — "MKT" is rejected
                                            # as invalid; verified 2026-05-14).
    ) -> dict:
        def fmt_strike(s: float) -> str:
            return str(int(s)) if s == int(s) else str(s)

        # ── Tick-align the limit price ──────────────────────────────────
        # SPX/NDX options require $0.05 ticks. Webull rejects with
        # "Premium invalid .05 increments only, FIX_UN_DFD_CANCEL" otherwise.
        # MARKET orders ignore limit_price but rounding it doesn't hurt.
        # We round DOWN (conservative for credit spreads — accept less, fill faster).
        safe_limit_str = f"{round_down_to_tick(float(limit_price)):.2f}"

        return {
            "client_order_id": client_order_id or uuid.uuid4().hex,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "SELL",
            "order_type": order_type,
            # Webull ignores limit_price for MARKET orders but the field is still
            # required by the API schema. Tick-aligned for LIMIT safety.
            "limit_price": safe_limit_str,
            "quantity": str(quantity),
            "entrust_type": "QTY",
            "time_in_force": "DAY",
            "position_intent": "SELL_TO_OPEN",
            "legs": [
                {
                    "side": "SELL",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(short_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
                {
                    "side": "BUY",
                    "quantity": str(quantity),
                    "symbol": symbol,
                    "strike_price": fmt_strike(long_strike),
                    "option_expire_date": expiry,
                    "instrument_type": "OPTION",
                    "option_type": "PUT",
                    "market": "US",
                },
            ],
        }


def _normalize_status(raw: str) -> str:
    mapping = {
        "FILLED": "FILLED",
        "ALL_FILLED": "FILLED",
        "PARTIALLY_FILLED": "WORKING",
        "WORKING": "WORKING",
        "PENDING": "WORKING",
        "SUBMITTED": "WORKING",
        "PENDING_SUBMIT": "WORKING",
        "CANCELLED": "CANCELLED",
        "CANCELED": "CANCELLED",
        "REJECTED": "REJECTED",
        "INACTIVE": "CANCELLED",
    }
    return mapping.get(raw, "UNKNOWN")
