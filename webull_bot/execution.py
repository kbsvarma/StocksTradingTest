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
        max_retries: int = 5,
        retry_price_step: float = 0.05,
        retry_wait_seconds: int = 60,
        fill_timeout_seconds: int = 300,
    ) -> FillResult:
        """Place a bull put spread and wait for fill.

        Retries by improving the limit price (lowering the credit we demand)
        if not filled within retry_wait_seconds.

        The position guard (`has_live_position_or_order`) is UNCONDITIONAL —
        there is no parameter to skip it. If a stacked-position workflow is
        ever needed, route through `place_spread_market` (force-entry) which
        is gated by interactive confirmation.
        """
        # ── Symbol whitelist — reject anything outside SPX/NDXP family ──────
        _assert_allowed_symbol(symbol)

        # ── DRY RUN gate ──────────────────────────────────────────────────
        if _dry_run_active():
            cid = uuid.uuid4().hex
            print(f"[DRY_RUN] place_spread {symbol} {short_strike}/{long_strike} qty={quantity} "
                  f"limit={limit_price} → synthesizing fill at {limit_price}", flush=True)
            return _synth_fill(cid, float(limit_price),
                               f"DRY_RUN: would have placed {symbol} {int(short_strike)}/{int(long_strike)} qty={quantity}")

        # ── Hard guard: never place if already live (UNCONDITIONAL) ───────
        blocked, reason = self.has_live_position_or_order()
        if blocked:
            return FillResult(
                filled=False,
                client_order_id="",
                fill_price=0.0,
                status="BLOCKED",
                detail=reason,
            )

        client_order_id = uuid.uuid4().hex
        current_limit = limit_price

        for attempt in range(max_retries + 1):
            order = self._build_order(
                symbol, expiry, short_strike, long_strike, quantity, current_limit,
                client_order_id=client_order_id,
            )

            resp = self.trade.order_v3.place_order(
                account_id=self.account_id,
                new_orders=[order],
            )

            if resp.status_code not in (200, 201):
                return FillResult(
                    filled=False,
                    client_order_id=client_order_id,
                    fill_price=0.0,
                    status="REJECTED",
                    detail=f"HTTP {resp.status_code}: {resp.text[:500]}",
                )

            # Poll for fill
            deadline = time.monotonic() + retry_wait_seconds
            while time.monotonic() < deadline:
                time.sleep(5)
                status = self.get_order_status(client_order_id)
                if status.status == "FILLED":
                    price = status.fill_price or current_limit
                    return FillResult(
                        filled=True,
                        client_order_id=client_order_id,
                        fill_price=price,
                        status="FILLED",
                        detail=f"filled at {price} on attempt {attempt + 1}",
                    )
                if status.status in ("CANCELLED", "REJECTED"):
                    return FillResult(
                        filled=False,
                        client_order_id=client_order_id,
                        fill_price=0.0,
                        status=status.status,
                        detail=f"order {status.status} on attempt {attempt + 1}",
                    )

            if attempt < max_retries:
                # Cancel current order and retry with lower credit demand
                self.cancel_order(client_order_id)
                time.sleep(2)
                client_order_id = uuid.uuid4().hex
                current_limit = round(current_limit - retry_price_step, 2)
                if current_limit <= 0:
                    break

        return FillResult(
            filled=False,
            client_order_id=client_order_id,
            fill_price=0.0,
            status="TIMEOUT",
            detail=f"not filled after {max_retries + 1} attempts",
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
        - Uses order_type=LMT at an aggressively low credit ($0.05) to guarantee
          a fill — Webull does not support MKT for combo options orders.
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
        # Set limit at $0.05 — below any realistic bid, guarantees fill at market
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
            "order_type": "LIMIT",         # Webull doesn't support MKT for combos
            "limit_price": force_limit,   # $0.05 — guarantees fill at market
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
            return FillResult(
                filled=False,
                client_order_id=client_order_id,
                fill_price=0.0,
                status="REJECTED",
                detail=f"FORCE MARKET HTTP {resp.status_code}: {resp.text[:500]}",
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
                return FillResult(
                    filled=False,
                    client_order_id=client_order_id,
                    fill_price=0.0,
                    status=status.status,
                    detail=f"FORCE MARKET order {status.status}",
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
        """Buy back the spread at market to close (debit order).

        For a credit spread, closing means buying back:
        - BUY the short put (was sold to open)
        - SELL the long put (was bought to open)
        We submit as a limit order at a debit of entry_credit * 3 to guarantee fill.
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
        # Max debit we're willing to pay = 2x credit (already at stop) + buffer
        max_debit = round(entry_credit * 2.5, 2)

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
            "order_type": "LIMIT",
            "limit_price": str(max_debit),
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
            return FillResult(
                filled=False,
                client_order_id=client_order_id,
                fill_price=0.0,
                status="REJECTED",
                detail=f"close HTTP {resp.status_code}: {resp.text[:500]}",
            )

        # Poll up to 2 minutes for close fill
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(5)
            status = self.get_order_status(client_order_id)
            if status.status == "FILLED":
                return FillResult(
                    filled=True,
                    client_order_id=client_order_id,
                    fill_price=status.fill_price or max_debit,
                    status="FILLED",
                    detail="stop-loss close filled",
                )
            if status.status in ("CANCELLED", "REJECTED"):
                break

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
                "limit_price": str(max_debit),
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
        try:
            resp = self.trade.order_v3.get_order_detail(
                account_id=self.account_id,
                client_order_id=client_order_id,
            )
            if resp.status_code != 200:
                return OrderStatus(client_order_id, "UNKNOWN", None, {})

            import json
            body = json.loads(resp.text)
            # Webull order detail response structure
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
            return OrderStatus(client_order_id, "UNKNOWN", None, {"error": str(exc)})

    @staticmethod
    def _build_order(
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        client_order_id: Optional[str] = None,
    ) -> dict:
        return ExecutionEngine._build_order_dict(
            symbol, expiry, short_strike, long_strike, quantity, limit_price, client_order_id
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
    ) -> dict:
        def fmt_strike(s: float) -> str:
            return str(int(s)) if s == int(s) else str(s)

        return {
            "client_order_id": client_order_id or uuid.uuid4().hex,
            "combo_type": "NORMAL",
            "option_strategy": "VERTICAL",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol,
            "side": "SELL",
            "order_type": "LIMIT",
            "limit_price": str(limit_price),
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
