"""Position-based execution — REDESIGN of execution.place_spread (2026-05-21).

NOT YET DEPLOYED. NOT YET IMPORTED ANYWHERE. Development only.

Why this exists:
  2026-05-21 incident: order filled at broker 255ms after place_order, but our
  status-poll loop ran 60 seconds and never saw FILLED. Resulting orphan position
  required manual intervention. Same kind of failure as 2026-05-20.

What this does differently:
  - Position-based fill detection: poll broker.get_account_position every 1s
    for retry_wait_seconds. If a new SPXW position appears matching our intended
    strikes, that's our fill — start SL monitor regardless of what order status says.
  - Pending-order marker in state.json BEFORE place_order. If process dies mid-flight,
    bot startup reconciliation can promote pending → open from broker data.
  - Atomic state writes (temp+rename).
  - No walk-down. One LIMIT, one cancel attempt. Verified via positions.
  - Captures Webull's internal order_id (not just client_order_id) for completeness.
  - Logs full response bodies for every poll (post-mortem ability).

What this does NOT do:
  - Replace_order (removed entirely until we have paper-account-verified contract)
  - MARKET fallback (was already hard-disabled)
  - Auto-recovery in watchdog (watchdog only alerts; this code does recovery on bot startup)

Status: design + code complete. NOT TESTED end-to-end with real Webull.
"""
from __future__ import annotations

import json
import os
import time
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from webull.trade.trade_client import TradeClient

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


# ── Symbol whitelist (copy from execution.py — must NEVER widen) ─────────────
ALLOWED_OPTION_SYMBOLS: frozenset[str] = frozenset({"SPXW", "SPX", "NDXP"})


@dataclass
class FillResult:
    """Result of place_spread attempt.

    filled=True means the broker SHOWS a position matching our intended strikes.
    Status endpoint not consulted for this decision.
    """
    filled: bool
    status: str           # "FILLED" | "TIMEOUT" | "REJECTED" | "BLOCKED" | "PARTIAL" | "WRONG_STRIKES"
    detail: str

    client_order_id: str = ""
    webull_order_id: str = ""  # Webull's internal order_id from response

    # Filled fields (only if filled=True)
    short_strike: float = 0.0
    long_strike: float = 0.0
    short_iid: str = ""
    long_iid: str = ""
    short_cost: float = 0.0
    long_cost: float = 0.0
    net_credit: float = 0.0   # broker-confirmed actual credit (= short_cost - long_cost)
    fill_detected_at: str = ""  # ISO timestamp when we first saw the position

    # Raw response bodies for post-mortem (truncated)
    place_response_snippet: str = ""
    poll_history: list = field(default_factory=list)

    @property
    def fill_price(self) -> float:
        """Backward-compat alias for execution.FillResult.fill_price.

        main.py reads fill.fill_price to record entry credit; v2 stores it
        as net_credit (broker-confirmed). Same value, different name.
        """
        return self.net_credit


@dataclass
class PendingOrder:
    """Marker written to state.json BEFORE place_order is called.

    If process dies mid-place, bot startup uses this to identify what was
    being attempted and whether broker filled it.
    """
    ts: str
    symbol: str
    expiry: str
    short_strike: float
    long_strike: float
    quantity: int
    intended_credit: float
    intended_limit: float
    client_order_id: str
    attempt: int = 1
    webull_order_id: str = ""  # populated after place returns


def _assert_allowed_symbol(symbol: str) -> None:
    if symbol not in ALLOWED_OPTION_SYMBOLS:
        raise ValueError(
            f"Symbol {symbol!r} NOT on allowed list {sorted(ALLOWED_OPTION_SYMBOLS)}"
        )


def _now_et_iso() -> str:
    return datetime.now(ET).isoformat()


def _save_state_atomic(state_path: Path, state_data: dict) -> None:
    """Write state.json atomically via temp + rename.

    Prevents partial reads by concurrent watchdog/dashboard.
    """
    state_path = Path(state_path)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(state_data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(state_path)  # atomic on same filesystem


# ──────────────────────────────────────────────────────────────────────────────
# Position-snapshot helpers
# ──────────────────────────────────────────────────────────────────────────────

class PositionSnapshotter:
    """Encapsulates broker position queries. Fixes default page_size=10 issue."""

    def __init__(self, trade_client: TradeClient, account_id: str):
        self.trade = trade_client
        self.account_id = account_id

    def snapshot_combos(self, rate_limit_retries: int = 3) -> list[dict]:
        """Return list of combo positions for ALLOWED_OPTION_SYMBOLS via account_v2.

        Each combo dict has strikes embedded in legs[], cost_price = entry credit,
        last_price = current mark. NO separate strike-resolver lookup needed.

        This is the new (2026-05-22) post-orphan-#2 fix. Replaces the flat
        snapshot() + build_strike_resolver() path that hit 429 rate limits.
        """
        for attempt in range(rate_limit_retries):
            try:
                r = self.trade.account_v2.get_account_position(account_id=self.account_id)
                if r.status_code == 200:
                    body = r.json()
                    positions = body if isinstance(body, list) else body.get("data", [])
                    # Filter to allowed option combos with VERTICAL strategy
                    result = []
                    for p in positions:
                        if (p.get("symbol") in ALLOWED_OPTION_SYMBOLS
                                and p.get("instrument_type") == "OPTION"
                                and p.get("option_strategy") in ("VERTICAL", "SINGLE")):
                            result.append(p)
                    return result
                if r.status_code == 429:
                    backoff = 1.0 * (2 ** attempt)
                    log.warning(f"snapshot_combos 429, sleeping {backoff}s")
                    time.sleep(backoff)
                    continue
                log.warning(f"snapshot_combos HTTP {r.status_code}")
                return []
            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "TOO_MANY_REQUESTS" in msg:
                    backoff = 1.0 * (2 ** attempt)
                    log.warning(f"snapshot_combos 429 exception, sleeping {backoff}s")
                    time.sleep(backoff)
                    continue
                log.exception(f"snapshot_combos failed: {exc}")
                return []
        return []

    def snapshot(self, rate_limit_retries: int = 3) -> dict[str, dict]:
        """Return dict of iid → holding for all SPXW/SPX/NDXP option positions.

        Uses page_size=100 (Webull max). Paginates if has_next.
        Handles HTTP 429 with exponential backoff.
        Returns {} on error (defensive — caller decides what to do).
        """
        all_holdings: list[dict] = []
        last_iid = None
        max_pages = 10  # 10×100 = 1000 holdings safety ceiling

        def _query_page(last_id):
            """Issue one query with 429 retry."""
            for attempt in range(rate_limit_retries):
                try:
                    if last_id:
                        r = self.trade.account.get_account_position(
                            account_id=self.account_id, page_size=100, last_instrument_id=last_id,
                        )
                    else:
                        r = self.trade.account.get_account_position(
                            account_id=self.account_id, page_size=100,
                        )
                    if r.status_code == 200:
                        return r.json()
                    if r.status_code == 429:
                        backoff = 1.0 * (2 ** attempt)
                        log.warning(f"snapshot 429, sleeping {backoff}s")
                        time.sleep(backoff)
                        continue
                    log.warning(f"snapshot HTTP {r.status_code}")
                    return None
                except Exception as exc:
                    msg = str(exc)
                    if "429" in msg or "TOO_MANY_REQUESTS" in msg:
                        backoff = 1.0 * (2 ** attempt)
                        log.warning(f"snapshot 429 (exception), sleeping {backoff}s")
                        time.sleep(backoff)
                        continue
                    log.exception(f"snapshot query failed: {exc}")
                    return None
            return None

        try:
            for _page in range(max_pages):
                body = _query_page(last_iid)
                if body is None:
                    break
                holdings = body.get("holdings", [])
                all_holdings.extend(holdings)
                if not body.get("has_next") or not holdings:
                    break
                last_iid = holdings[-1].get("instrument_id")
            else:
                log.warning("snapshot exceeded %d pages", max_pages)
        except Exception:
            log.exception("snapshot failed; returning empty")
            return {}

        # Filter to whitelisted option symbols
        return {
            h["instrument_id"]: h
            for h in all_holdings
            if h.get("instrument_id")
            and h.get("symbol") in ALLOWED_OPTION_SYMBOLS
            and h.get("instrument_type") == "OPTION"
        }


# ──────────────────────────────────────────────────────────────────────────────
# Broker → OpenPosition arm helper (2026-05-22 redesign)
# ──────────────────────────────────────────────────────────────────────────────

def arm_sl_from_broker_combo(
    combo: dict,
    stop_multiplier: float = 2.0,
    entry_spx: float = 0.0,
    entry_vix: float = 0.0,
    client_order_id: str = "",
    spx_source: str = "unknown",
    vix_source: str = "unknown",
    chain_source: str = "unknown",
    yf_options_symbol: str = "^SPX",
) -> dict:
    """Convert a broker combo position (from snapshot_combos) into an OpenPosition dict.

    Broker is the SOURCE OF TRUTH for strikes, qty, entry credit. We never derive
    these from intent — always read from the broker's actual position data.

    For BPS (vertical put spread):
      - short leg = put with higher strike (closer to ATM)
      - long leg = put with lower strike (further OTM)
      - entry_credit = combo cost_price (positive for credit spread)
      - stop_price = entry_credit × stop_multiplier
    """
    legs = combo.get("legs") or combo.get("orders") or []
    if len(legs) != 2:
        raise ValueError(f"Expected 2-leg combo, got {len(legs)} legs in {combo.get('position_id', '?')}")

    # Identify short/long by strike (short = higher strike for puts)
    leg_strikes = []
    for leg in legs:
        sk = leg.get("option_exercise_price") or leg.get("strike_price") or leg.get("strike")
        if sk is None:
            raise ValueError(f"Leg missing strike: {leg}")
        leg_strikes.append((float(sk), leg))
    leg_strikes.sort(key=lambda x: -x[0])  # desc by strike
    short_strike, short_leg = leg_strikes[0]
    long_strike, long_leg = leg_strikes[1]

    qty = abs(int(combo.get("quantity", 1) or 1))
    entry_credit = round(abs(float(combo.get("cost_price", 0) or 0)), 2)
    stop_price = round(entry_credit * stop_multiplier, 2)
    expiry = short_leg.get("option_expire_date") or long_leg.get("option_expire_date") or ""
    symbol = combo.get("symbol", "SPXW")

    # Webull account_v2 doesn't expose per-leg instrument_id, only combo position_id.
    # We store position_id in both short_iid and long_iid (same canonical ID).
    # Monitor's alive-check uses snapshot_combos to verify position_id is still present.
    combo_id = combo.get("position_id", "")

    return {
        "symbol": symbol,
        "expiry": expiry,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "quantity": qty,
        "entry_credit": entry_credit,
        "stop_price": stop_price,
        "entry_spx": entry_spx,
        "entry_vix": entry_vix,
        "entry_ts": _now_et_iso(),
        "client_order_id": client_order_id,
        "yf_options_symbol": yf_options_symbol,
        "short_iid": combo_id,   # combo position_id (account_v2 doesn't give per-leg iids)
        "long_iid": combo_id,    # same as short_iid intentionally
        "spx_source": spx_source,
        "vix_source": vix_source,
        "chain_source": chain_source,
    }


def find_new_spxw_combo(
    pre_combos: list[dict],
    post_combos: list[dict],
    expected_symbol: str = "SPXW",
    expected_expiry: Optional[str] = None,
) -> Optional[dict]:
    """Find a combo in post that wasn't in pre (matched by position_id).

    Used by slow-poll fill detection. Returns the new combo or None.
    """
    pre_ids = {c.get("position_id") for c in pre_combos if c.get("position_id")}
    for c in post_combos:
        pid = c.get("position_id")
        if not pid or pid in pre_ids:
            continue
        if c.get("symbol") != expected_symbol:
            continue
        if c.get("option_strategy") != "VERTICAL":
            continue
        if expected_expiry:
            legs = c.get("legs", [])
            if legs and legs[0].get("option_expire_date") != expected_expiry:
                continue
        return c
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Position diff & matching (LEGACY — kept for compatibility, do not use for new code)
# ──────────────────────────────────────────────────────────────────────────────

def _diff_positions(pre: dict[str, dict], post: dict[str, dict]) -> list[dict]:
    """Return holdings present in post but not pre."""
    return [h for iid, h in post.items() if iid not in pre]


def _strike_of(holding: dict) -> Optional[float]:
    """Extract strike from a holding dict. Webull may use various field names.

    Currently positions endpoint does NOT include strike directly — need to
    cross-reference with order history. This function returns None if not
    derivable; caller must use a different match strategy (qty + price proximity).
    """
    for key in ("strike_price", "strike", "strikePrice"):
        v = holding.get(key)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                continue
    return None


def _qty_of(holding: dict) -> int:
    try:
        return int(holding.get("qty", 0))
    except (ValueError, TypeError):
        return 0


def _cost_of(holding: dict) -> float:
    """Extract cost basis (per share, not aggregate)."""
    for key in ("unit_cost", "cost_price", "average_cost", "averageCost", "avg_cost"):
        v = holding.get(key)
        if v is not None:
            try:
                return abs(float(v))
            except (ValueError, TypeError):
                continue
    return 0.0


def find_matching_spread_in_new_positions(
    new_holdings: list[dict],
    expected_short: float,
    expected_long: float,
    expected_qty: int,
    strike_resolver=None,
) -> Optional[dict]:
    """Find a short leg (qty<0) + long leg (qty>0) matching expected strikes.

    Returns dict with keys: short_iid, long_iid, short_holding, long_holding,
    short_cost, long_cost, net_credit. Or None if not found.

    strike_resolver: callable(iid) -> float, used when holdings don't carry strike.
    If None, attempts to extract from holdings directly (may fail; caller is
    responsible for providing resolver via order history).

    Match rules:
      - Short holding: qty = -expected_qty (signed)
      - Long holding:  qty = +expected_qty
      - Strikes must match within $0.01
    """
    # Try to identify short and long candidates
    shorts = [h for h in new_holdings if _qty_of(h) == -expected_qty]
    longs  = [h for h in new_holdings if _qty_of(h) == +expected_qty]

    if not shorts or not longs:
        return None

    def _resolve(h):
        s = _strike_of(h)
        if s is not None:
            return s
        if strike_resolver:
            try:
                return strike_resolver(h.get("instrument_id"))
            except Exception:
                return None
        return None

    short_match = None
    long_match = None
    for h in shorts:
        s = _resolve(h)
        if s is not None and abs(s - expected_short) < 0.01:
            short_match = h
            break
    for h in longs:
        s = _resolve(h)
        if s is not None and abs(s - expected_long) < 0.01:
            long_match = h
            break

    if not (short_match and long_match):
        return None

    short_cost = _cost_of(short_match)
    long_cost = _cost_of(long_match)
    return {
        "short_iid": short_match["instrument_id"],
        "long_iid":  long_match["instrument_id"],
        "short_holding": short_match,
        "long_holding":  long_match,
        "short_cost":    short_cost,
        "long_cost":     long_cost,
        "net_credit":    round(short_cost - long_cost, 2),
    }


def _wrong_strikes_appeared(
    new_holdings: list[dict],
    expected_short: float,
    expected_long: float,
    expected_qty: int,
    strike_resolver=None,
) -> bool:
    """Detect: positions appeared but at DIFFERENT strikes than we intended.

    Critical safety: this means the broker filled a different order, or there's
    a race with another bot/manual action. Refuse to claim this as our fill.
    """
    if not new_holdings:
        return False
    # Check qty matches expected
    matching_qty = [h for h in new_holdings if abs(_qty_of(h)) == expected_qty]
    if not matching_qty:
        return False
    # If any matching-qty holding has a strike that DIFFERS from expected, it's wrong
    for h in matching_qty:
        s = _strike_of(h)
        if s is None and strike_resolver:
            try:
                s = strike_resolver(h.get("instrument_id"))
            except Exception:
                s = None
        if s is None:
            continue  # can't determine; not necessarily wrong
        if abs(s - expected_short) > 0.01 and abs(s - expected_long) > 0.01:
            return True  # this strike isn't either of our intended legs
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Strike resolver (cross-references order history)
# ──────────────────────────────────────────────────────────────────────────────

def build_strike_resolver(trade_client: TradeClient, account_id: str, retries: int = 3):
    """Build a callable iid → strike using today's order history.

    Today's incident: order_history may not include our order immediately
    after place. ALSO: Webull rate-limits with HTTP 429 — handle with backoff.

    Returns None on lookup failure — caller must handle gracefully (e.g.,
    fall back to position-count-based matching).
    """
    iid_to_strike: dict[str, float] = {}
    failure_count = {"n": 0}

    def _refresh():
        for attempt in range(retries):
            try:
                r = trade_client.order.list_today_orders(
                    account_id=account_id, page_size=50,
                )
                body = r.json()
                for order in body.get("orders", []):
                    for item in order.get("items", []):
                        iid = item.get("instrument_id")
                        strike = item.get("strike_price") or item.get("strikPrice")
                        if iid and strike:
                            try:
                                iid_to_strike[iid] = float(strike)
                            except (ValueError, TypeError):
                                pass
                failure_count["n"] = 0  # reset on success
                return
            except Exception as exc:
                # Detect rate limit
                msg = str(exc)
                if "429" in msg or "TOO_MANY_REQUESTS" in msg or "to many requests" in msg:
                    backoff_s = 1.0 * (2 ** attempt)  # 1s, 2s, 4s
                    log.warning(f"build_strike_resolver: rate-limited (attempt {attempt+1}/{retries}), "
                                f"sleeping {backoff_s}s")
                    time.sleep(backoff_s)
                    continue
                # Other errors — give up
                log.warning(f"build_strike_resolver: list_today_orders failed: {exc}")
                failure_count["n"] += 1
                return
        failure_count["n"] += 1

    def _resolve(iid: str) -> Optional[float]:
        if iid in iid_to_strike:
            return iid_to_strike[iid]
        # Lazy refresh
        _refresh()
        return iid_to_strike.get(iid)

    return _resolve


# ──────────────────────────────────────────────────────────────────────────────
# The new place_spread
# ──────────────────────────────────────────────────────────────────────────────

class ExecutionEngineV2:
    """Position-based place_spread. Drop-in candidate for ExecutionEngine.

    Same external interface (place_spread signature), different internal logic.
    """

    def __init__(self, trade_client: TradeClient, account_id: str, state_path: Optional[str] = None):
        self.trade = trade_client
        self.account_id = account_id
        self.snapshotter = PositionSnapshotter(trade_client, account_id)
        self.state_path = Path(state_path) if state_path else None
        # Delegate legacy close/force/status methods to old engine
        # (monitor.py uses these; we only redesigned place_spread for v2)
        from webull_bot.execution import ExecutionEngine as _LegacyEngine
        self._legacy = _LegacyEngine(trade_client, account_id)

    # ── Pass-through to legacy engine for unchanged methods ─────────────
    def close_spread_market(self, *a, **kw):
        return self._legacy.close_spread_market(*a, **kw)

    def place_spread_market(self, *a, **kw):
        return self._legacy.place_spread_market(*a, **kw)

    def close_all_today(self, *a, **kw):
        return self._legacy.close_all_today(*a, **kw)

    def preview_close_all_today(self, *a, **kw):
        return self._legacy.preview_close_all_today(*a, **kw)

    def get_order_status(self, *a, **kw):
        return self._legacy.get_order_status(*a, **kw)

    def preview_spread(self, *a, **kw):
        return self._legacy.preview_spread(*a, **kw)

    def cancel_order(self, *a, **kw):
        return self._legacy.cancel_order(*a, **kw)

    def _fetch_spxw_positions(self):
        return self._legacy._fetch_spxw_positions()

    def _capture_new_legs(self, *a, **kw):
        return self._legacy._capture_new_legs(*a, **kw)

    def _build_close_plan(self, *a, **kw):
        return self._legacy._build_close_plan(*a, **kw)

    # ── Pending-order helpers ────────────────────────────────────────────

    def _write_pending(self, pending: PendingOrder) -> None:
        """Write pending_order to state.json. Atomic. Doesn't touch other fields."""
        if self.state_path is None:
            return
        try:
            if self.state_path.exists():
                state_data = json.loads(self.state_path.read_text())
            else:
                state_data = {}
            state_data["pending_order"] = asdict(pending)
            _save_state_atomic(self.state_path, state_data)
        except Exception:
            log.exception("failed to write pending_order — proceeding anyway")

    def _clear_pending(self) -> None:
        if self.state_path is None:
            return
        try:
            if self.state_path.exists():
                state_data = json.loads(self.state_path.read_text())
                if "pending_order" in state_data:
                    state_data.pop("pending_order")
                    _save_state_atomic(self.state_path, state_data)
        except Exception:
            log.exception("failed to clear pending_order")

    # ── Hard guard (delegated; existing logic is fine) ──────────────────

    def has_live_position_or_order(self):
        """V2 pre-place safety check using account_v2 combo endpoint.

        Returns (blocked: bool, reason: str). Used immediately before any
        place_order to prevent double-orders during retry storms.

        Checks (in order):
          1. state.json has open_position → ABORT (we already armed an SL)
          2. broker has any SPXW VERTICAL combo (last_price > $0.05 = active)
             → ABORT (existing position from prior attempt)

        Either check passing means "do not place new order."
        """
        # 1. State file check (cheap)
        try:
            if self.state_path and Path(self.state_path).exists():
                state = json.loads(Path(self.state_path).read_text())
                op = state.get("open_position")
                if op and op.get("short_strike") and op.get("long_strike"):
                    return (True, f"state.open_position populated: "
                                  f"{int(op['short_strike'])}/{int(op['long_strike'])}P "
                                  f"(SL already armed)")
        except Exception as exc:
            log.warning(f"pre-check: state.json read failed: {exc}")

        # 2. Broker check via account_v2 (combo positions with strikes)
        try:
            combos = self.snapshotter.snapshot_combos()
            active_verticals = []
            for c in combos:
                if c.get("symbol") not in ALLOWED_OPTION_SYMBOLS:
                    continue
                if c.get("option_strategy") != "VERTICAL":
                    continue
                # Filter expired/worthless (last_price < $0.05 = essentially gone)
                try:
                    last_price = abs(float(c.get("last_price", 0) or 0))
                except (ValueError, TypeError):
                    last_price = 0.0
                if last_price < 0.05:
                    continue
                legs = c.get("legs", [])
                if len(legs) == 2:
                    strikes = sorted([float(l.get("option_exercise_price", 0) or 0)
                                      for l in legs], reverse=True)
                    active_verticals.append(
                        f"{c.get('symbol')} {int(strikes[0])}/{int(strikes[1])}P"
                    )
            if active_verticals:
                return (True, f"broker has active vertical(s): {', '.join(active_verticals)} "
                              f"(orphan or prior fill)")
        except Exception as exc:
            log.warning(f"pre-check: broker query failed: {exc} — proceeding (broker check optional)")

        return (False, "")

    # ── Main entry point ────────────────────────────────────────────────

    def place_spread(
        self,
        symbol: str,
        expiry: str,
        short_strike: float,
        long_strike: float,
        quantity: int,
        limit_price: float,
        retry_wait_seconds: int = 300,   # 5 min — slow polling, safer envelope (2026-05-22)
        poll_interval_s: float = 10.0,   # 10s — under Webull rate limit (was 1.0)
        post_cancel_settle_s: float = 5.0,
        # Backward-compat: accept-and-ignore legacy kwargs that main.py passes
        # from the old ExecutionEngine.place_spread signature
        max_retries: int = None,
        retry_price_step: float = None,
        fill_timeout_seconds: int = None,
        entry_market_fallback: bool = False,
        walk_down_step: float = None,
        walk_down_interval_s: int = None,
        walk_down_max_steps: int = None,
        **_legacy_kwargs,  # absorb any other future legacy args
    ) -> FillResult:
        # Legacy params logged-and-ignored (walk-down removed entirely in v2)
        if entry_market_fallback:
            log.info("[v2] entry_market_fallback=True IGNORED — v2 never uses MARKET")
        # walk_down_* legacy ignored — v2 has no walk-down
        """
        Place a bull put spread. Returns filled=True ONLY if broker confirms position.

        Flow:
          1. Symbol whitelist + dry-run + hard guard
          2. Snapshot broker positions BEFORE place
          3. Write PENDING marker
          4. Call place_order
          5. Poll positions every poll_interval_s for retry_wait_seconds
          6. On match → record full position info + clear pending + return filled
          7. On timeout → attempt cancel → sleep → final position check
          8. On wrong strikes appearing → alert + raise (safety)
        """
        _assert_allowed_symbol(symbol)

        # Dry-run handling (delegate to existing)
        from webull_bot.execution import _dry_run_active, _synth_fill
        if _dry_run_active():
            cid = uuid.uuid4().hex
            return FillResult(
                filled=True, status="DRY_RUN_FILLED",
                client_order_id=cid, short_strike=short_strike, long_strike=long_strike,
                net_credit=float(limit_price), fill_detected_at=_now_et_iso(),
                detail=f"DRY_RUN synthesized fill at {limit_price}",
            )

        # Hard guard: existing positions/orders
        blocked, reason = self.has_live_position_or_order()
        if blocked:
            return FillResult(
                filled=False, status="BLOCKED",
                detail=reason,
            )

        # Pre-snapshot via account_v2 (combo positions with strikes embedded)
        pre_combos = self.snapshotter.snapshot_combos()
        log.info(f"pre-snapshot: {len(pre_combos)} existing combo positions")

        # Generate IDs
        cid = uuid.uuid4().hex
        from webull_bot.execution import ExecutionEngine
        existing = ExecutionEngine(self.trade, self.account_id)
        order_dict = existing._build_order_dict(
            symbol, expiry, short_strike, long_strike, quantity,
            float(limit_price), client_order_id=cid, order_type="LIMIT",
        )

        # Write PENDING marker before any external call
        pending = PendingOrder(
            ts=_now_et_iso(),
            symbol=symbol, expiry=expiry,
            short_strike=float(short_strike), long_strike=float(long_strike),
            quantity=int(quantity),
            intended_credit=float(limit_price),
            intended_limit=float(order_dict["limit_price"]),  # post tick-rounding
            client_order_id=cid,
        )
        self._write_pending(pending)
        log.info(f"pending_order written: {pending}")

        # Place
        place_response_text = ""
        try:
            resp = self.trade.order_v3.place_order(
                account_id=self.account_id, new_orders=[order_dict],
            )
            place_response_text = (resp.text or "")[:1500]
            if resp.status_code not in (200, 201):
                self._clear_pending()
                from webull_bot.execution import _extract_webull_error
                wb_err = _extract_webull_error(resp)
                return FillResult(
                    filled=False, status="REJECTED",
                    detail=f"place_order HTTP {resp.status_code} | {wb_err}",
                    client_order_id=cid,
                    place_response_snippet=place_response_text,
                )
            # Extract webull_order_id if present
            try:
                body = json.loads(resp.text)
                pending.webull_order_id = body.get("order_id", "")
                self._write_pending(pending)  # rewrite with new field
            except Exception:
                pass
        except Exception as exc:
            self._clear_pending()
            return FillResult(
                filled=False, status="REJECTED",
                detail=f"place_order exception: {exc}",
                client_order_id=cid,
            )

        log.info(f"place_order returned OK; webull_order_id={pending.webull_order_id}; "
                 f"polling combos every {poll_interval_s}s for {retry_wait_seconds}s "
                 f"(slow-poll via account_v2)")

        # Slow-poll loop — combo-based, no strike resolver needed
        deadline = time.monotonic() + retry_wait_seconds
        poll_history = []
        poll_num = 0
        while time.monotonic() < deadline:
            time.sleep(poll_interval_s)
            poll_num += 1
            # 2026-05-22: refresh heartbeat during long blocking poll so telegram
            # service doesn't false-alarm "bot stale" while we're waiting on a fill
            try:
                if self.state_path:
                    hb_path = Path(self.state_path).parent / "heartbeat.json"
                    if hb_path.exists():
                        hb = json.loads(hb_path.read_text())
                        hb["ts"] = _now_et_iso()
                        hb["slow_poll_active"] = True
                        hb["slow_poll_n"] = poll_num
                        tmp = hb_path.with_suffix(".json.tmp")
                        tmp.write_text(json.dumps(hb))
                        tmp.rename(hb_path)
            except Exception:
                pass  # heartbeat refresh is observability, never fail the trade path

            post_combos = self.snapshotter.snapshot_combos()
            new_combo = find_new_spxw_combo(
                pre_combos, post_combos,
                expected_symbol=symbol, expected_expiry=expiry,
            )
            poll_history.append({
                "poll": poll_num,
                "ts": _now_et_iso(),
                "n_post_combos": len(post_combos),
                "found": new_combo is not None,
            })

            if new_combo is None:
                continue

            # Verify strikes match what we intended (defensive — broker is source of truth,
                # but a wildly different position would indicate someone else placed an order)
            try:
                broker_pos = arm_sl_from_broker_combo(
                    new_combo,
                    stop_multiplier=2.0,  # caller can override post-return via state
                    client_order_id=cid,
                )
            except Exception as exc:
                log.exception(f"failed to parse combo {new_combo.get('position_id')}: {exc}")
                continue

            ss = broker_pos["short_strike"]
            ls = broker_pos["long_strike"]
            if abs(ss - float(short_strike)) > 0.01 or abs(ls - float(long_strike)) > 0.01:
                log.error(
                    f"strike mismatch! intended {short_strike}/{long_strike} "
                    f"vs broker {ss}/{ls} — refusing to claim fill"
                )
                return FillResult(
                    filled=False, status="WRONG_STRIKES",
                    detail=f"broker shows {ss}/{ls} but we intended {short_strike}/{long_strike}",
                    client_order_id=cid,
                    webull_order_id=pending.webull_order_id,
                    place_response_snippet=place_response_text,
                    poll_history=poll_history,
                )

            # FILL CONFIRMED — broker has our position with correct strikes.
            # Do NOT clear pending_order here. The caller (main.run) persists
            # open_position with an atomic store.save() that rewrites the file;
            # since BotState has no pending field, that save wipes pending in the
            # SAME atomic write. Retaining pending until then guarantees the
            # recovery breadcrumb survives any crash in the fill->record window,
            # so reconcile can PROMOTE_PENDING instead of orphan-HALTing.
            # (2026-06-04 RCA: clearing here left a window with NEITHER pending
            # nor open_position when entry crashed -> unrecoverable orphan.)
            log.info(
                f"FILL CONFIRMED after {poll_num} polls ({poll_num*poll_interval_s}s): "
                f"{int(ss)}/{int(ls)}P credit=${broker_pos['entry_credit']} "
                f"stop=${broker_pos['stop_price']}"
            )
            return FillResult(
                filled=True, status="FILLED",
                detail=f"slow-poll detection: filled at credit ${broker_pos['entry_credit']} "
                       f"after {poll_num} polls ({poll_num*poll_interval_s}s elapsed)",
                client_order_id=cid,
                webull_order_id=pending.webull_order_id,
                short_strike=ss,
                long_strike=ls,
                short_iid=broker_pos["short_iid"],
                long_iid=broker_pos["long_iid"],
                short_cost=0.0,  # individual leg costs not exposed in this path; combo cost_price = net credit
                long_cost=0.0,
                net_credit=broker_pos["entry_credit"],
                fill_detected_at=_now_et_iso(),
                place_response_snippet=place_response_text,
                poll_history=poll_history,
            )

        # Timeout: deadline reached without seeing position appear
        log.warning(
            f"slow-poll deadline ({retry_wait_seconds}s) reached after {poll_num} polls; "
            f"no matching combo. Attempting cancel..."
        )

        # Attempt cancel
        try:
            cresp = self.trade.order_v3.cancel_order(
                account_id=self.account_id, client_order_id=cid,
            )
            cancel_status = cresp.status_code
            cancel_body = (cresp.text or "")[:500]
        except Exception as exc:
            cancel_status = 0
            cancel_body = f"exception: {exc}"
        log.info(f"cancel response: HTTP {cancel_status}; body={cancel_body!r}")

        # Settle delay — give broker time to update positions if cancel raced with fill
        time.sleep(post_cancel_settle_s)

        # Final post-cancel check
        final_combos = self.snapshotter.snapshot_combos()
        new_combo = find_new_spxw_combo(
            pre_combos, final_combos,
            expected_symbol=symbol, expected_expiry=expiry,
        )
        if new_combo is not None:
            try:
                broker_pos = arm_sl_from_broker_combo(new_combo, client_order_id=cid)
                ss = broker_pos["short_strike"]
                ls = broker_pos["long_strike"]
                if abs(ss - float(short_strike)) <= 0.01 and abs(ls - float(long_strike)) <= 0.01:
                    # Do NOT clear pending here — see FILL CONFIRMED note above.
                    # main.run persists open_position via atomic save which wipes
                    # pending; retaining it preserves the crash-recovery breadcrumb.
                    log.info(f"cancel-raced-with-fill: position confirmed via final check")
                    return FillResult(
                        filled=True, status="FILLED",
                        detail=f"cancel raced with fill — confirmed via final combo check at "
                               f"credit ${broker_pos['entry_credit']}",
                        client_order_id=cid,
                        webull_order_id=pending.webull_order_id,
                        short_strike=ss,
                        long_strike=ls,
                        short_iid=broker_pos["short_iid"],
                        long_iid=broker_pos["long_iid"],
                        short_cost=0.0, long_cost=0.0,
                        net_credit=broker_pos["entry_credit"],
                        fill_detected_at=_now_et_iso(),
                        place_response_snippet=place_response_text,
                        poll_history=poll_history,
                    )
            except Exception as exc:
                log.exception(f"final-check combo parse failed: {exc}")

        # Truly didn't fill
        self._clear_pending()
        return FillResult(
            filled=False, status="TIMEOUT",
            detail=f"no position appeared after {poll_num} slow-polls + cancel + {post_cancel_settle_s}s settle",
            client_order_id=cid,
            webull_order_id=pending.webull_order_id,
            place_response_snippet=place_response_text,
            poll_history=poll_history,
        )
