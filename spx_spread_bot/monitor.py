from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

from config_loader import BotConfig
from execution import ExecutionEngine
from market_data import MarketDataService
from models import ExitReason, OpenPosition
from trade_logger import BotLogger


@dataclass(slots=True)
class MonitorOutcome:
    closed: bool
    reason: ExitReason | None = None
    exit_price: float = 0.0
    detail: str = ""


class PositionMonitor:
    def __init__(self, cfg: BotConfig, market: MarketDataService, execution: ExecutionEngine, logger: BotLogger):
        self.cfg = cfg
        self.market = market
        self.execution = execution
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)

    def evaluate(
        self,
        now: datetime,
        pos: OpenPosition,
        prefetched_quote: dict | None = None,
    ) -> MonitorOutcome:
        """Evaluate a single position.

        If *prefetched_quote* is supplied ({"bid": …, "ask": …, "mid": …}) it is
        used directly, avoiding a second 2-second market-data subscription.  This
        allows the caller (_monitor_job) to batch-fetch quotes for all positions in
        one 2-second window so no position is blocked waiting for another.
        """
        expiry_day = self._expiry_date(pos.expiry)
        is_expiry_day = expiry_day is not None and now.date() == expiry_day

        # Only fetch live quotes and evaluate mark-based exits during regular
        # market hours (09:30–16:00 ET, Mon–Fri).  After-hours option quotes
        # have garbage bid/ask spreads that can cause false stop-loss triggers.
        now_et = now.astimezone(self.tz)
        in_market_hours = (
            now_et.weekday() < 5
            and _MARKET_OPEN <= now_et.time() < _MARKET_CLOSE
        )

        mark: float | None = None
        if in_market_hours:
            quote = prefetched_quote if prefetched_quote is not None else self.execution.spread_quote_components(pos)
            mark = quote["mid"] if quote else None
            if mark is not None:
                # Log to tick_events (high-frequency) rather than order_events
                # to prevent order_events.jsonl from bloating with every 30s poll.
                sp = pos.short_put_strike or pos.short_call_strike or ""
                mark_key = f"{pos.strategy}_{sp}" if sp else pos.strategy
                mark_payload = {
                    "strategy": pos.strategy,
                    "mark": mark,
                    "spread_bid": quote["bid"] if quote else None,
                    "spread_ask": quote["ask"] if quote else None,
                    "stop": pos.stop_price,
                    "profit_target": pos.profit_target_price,
                    "short_put_strike": pos.short_put_strike,
                    "short_call_strike": pos.short_call_strike,
                }
                self.logger.tick_event_raw("SPREAD_MARK", mark_payload)
                # Also update the small sidecar so _write_status doesn't need
                # to tail-scan the 100MB+ tick_events.jsonl.
                self.logger.update_spread_mark(mark_key, mark_payload)
        else:
            quote = None

        if mark is not None:
            if mark >= pos.stop_price:
                ok, detail = self.execution.close_open_position_market(pos, ExitReason.STOP_LOSS.value)
                if ok:
                    return MonitorOutcome(closed=True, reason=ExitReason.STOP_LOSS, exit_price=mark, detail=detail)
                return MonitorOutcome(closed=False, detail=f"stop-loss close failed: {detail}")

            if not self.cfg.disable_profit_target and mark <= pos.profit_target_price:
                # If broker-side GTC profit order is working OR already filled,
                # do not submit a second close — a filled GTC means the position
                # is already flat; a second BUY would open an untracked long.
                if self.execution.order_is_active(pos.profit_order_id):
                    # Check whether GTC actually filled (status=FILLED) so we
                    # can book the closure rather than silently skipping forever.
                    gtc_filled = self.execution.order_is_filled(pos.profit_order_id)
                    if gtc_filled:
                        return MonitorOutcome(
                            closed=True,
                            reason=ExitReason.PROFIT_TARGET,
                            exit_price=pos.profit_target_price,
                            detail="GTC profit order filled at broker",
                        )
                    return MonitorOutcome(closed=False)
                ok, fill = self.execution.close_open_position_limit(pos, pos.profit_target_price, ExitReason.PROFIT_TARGET.value)
                if ok:
                    return MonitorOutcome(
                        closed=True,
                        reason=ExitReason.PROFIT_TARGET,
                        exit_price=fill or pos.profit_target_price,
                        detail="profit target",
                    )

        # Friday hard close precedence (applies to Friday expiries by design).
        if now.weekday() == 4 and now.time() >= self.cfg.friday_forced_close_time():
            ok, detail = self.execution.close_open_position_market(pos, ExitReason.FRIDAY_FORCE_CLOSE.value)
            if ok:
                return MonitorOutcome(closed=True, reason=ExitReason.FRIDAY_FORCE_CLOSE, exit_price=mark or 0.0, detail=detail)
            return MonitorOutcome(closed=False, detail=f"friday force close failed: {detail}")

        # Distance/expiry handling is only valid on the actual option expiry date.
        # For fallback non-0DTE positions (e.g. 2DTE), keep monitoring stop/target
        # and defer expiry logic until the contract's real expiry day.
        if not is_expiry_day:
            return MonitorOutcome(closed=False)

        # Between eod_check and close, we continuously verify distance-from-strike.
        # If data is available and the short strike is safely away, we mark it so
        # the 16:00 expiry branch can book as safe without false positives.
        if self.cfg.eod_check_time() <= now.time() < time(16, 0):
            try:
                spx = self.market.get_spx_price()
            except Exception as exc:  # noqa: BLE001
                return MonitorOutcome(closed=False, detail=f"eod spx fetch failed: {exc}")

            closest_short_distance = self._closest_short_strike_distance(spx, pos)
            if closest_short_distance <= float(self.cfg.expiry_safety_distance_points):
                ok, detail = self.execution.close_open_position_market(pos, ExitReason.EOD_FORCE_CLOSE.value)
                if ok:
                    return MonitorOutcome(closed=True, reason=ExitReason.EOD_FORCE_CLOSE, exit_price=mark or 0.0, detail=detail)
                return MonitorOutcome(closed=False, detail=f"eod close failed: {detail}")
            pos.eod_distance_safe_confirmed = True

        # At/after 16:00, only mark as safe-expired if we either:
        # 1) already validated safe during pre-close checks, or
        # 2) can still validate safe now via one final SPX snapshot.
        if now.time() >= time(16, 0):
            if not pos.eod_distance_safe_confirmed:
                try:
                    spx = self.market.get_spx_price()
                    if self._closest_short_strike_distance(spx, pos) > float(self.cfg.expiry_safety_distance_points):
                        pos.eod_distance_safe_confirmed = True
                except Exception:  # noqa: BLE001
                    pass

            self.execution.cancel_protective_orders(pos)
            if pos.eod_distance_safe_confirmed:
                return MonitorOutcome(closed=True, reason=ExitReason.EOD_DISTANCE_SAFE, exit_price=0.0, detail="expired")

            fallback_exit = mark if (mark is not None and mark > 0) else float(self.cfg.spread_width)
            return MonitorOutcome(
                closed=True,
                reason=ExitReason.EOD_FORCE_CLOSE,
                exit_price=round(fallback_exit, 2),
                detail="expiry classification unverified-safe; conservative close pricing",
            )

        return MonitorOutcome(closed=False)

    @staticmethod
    def _closest_short_strike_distance(spx: float, pos: OpenPosition) -> float:
        short_strikes: list[float] = []
        if pos.short_put_strike > 0:
            short_strikes.append(pos.short_put_strike)
        if pos.short_call_strike > 0:
            short_strikes.append(pos.short_call_strike)

        if not short_strikes:
            return 10_000.0
        return min(abs(spx - strike) for strike in short_strikes)

    @staticmethod
    def _expiry_date(expiry_yyyymmdd: str) -> date | None:
        raw = str(expiry_yyyymmdd or "").strip()
        if len(raw) != 8 or not raw.isdigit():
            return None
        try:
            return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            return None
