# bot.py — State machine and main orchestration loop.
# All strategy decisions flow through here. Broker and strategy are pure dependencies.

import signal
import sys
import traceback
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

import config
import journal
import logger as L
from broker import BrokerClient
from risk import RiskManager
from strategy import Bar, ORBStrategy, TradeContext


def _parse_time(hhmm: str) -> dtime:
    """Convert 'HH:MM' config string to datetime.time."""
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))


def _minus_minutes(clock_time: dtime, minutes: int) -> dtime:
    """Return `clock_time - minutes` (time-of-day only)."""
    anchor = datetime(2000, 1, 1, clock_time.hour, clock_time.minute)
    return (anchor - timedelta(minutes=minutes)).time()


# ── State machine ──────────────────────────────────────────────────────────────

class BotState(Enum):
    STARTUP = "STARTUP"
    RANGE_BUILDING = "RANGE_BUILDING"
    WAITING_ENTRY = "WAITING_ENTRY"
    IN_TRADE_LONG = "IN_TRADE_LONG"
    IN_TRADE_SHORT = "IN_TRADE_SHORT"
    DAILY_DONE = "DAILY_DONE"
    HALTED = "HALTED"
    CLOSED = "CLOSED"


# ── Bot ────────────────────────────────────────────────────────────────────────

class Bot:
    def __init__(self) -> None:
        self._log = L.setup_logger()
        self._tz = ZoneInfo(config.TIMEZONE)

        self._state = BotState.STARTUP
        self._broker = BrokerClient(self._log)
        self._strategy = ORBStrategy()
        self._risk = RiskManager()
        self._range_start_time = _parse_time(config.RANGE_START_TIME)
        self._session_end_time = _parse_time(config.SESSION_END_TIME)
        self._vix_check_time = _minus_minutes(self._range_start_time, 1)

        # Trade tracking
        self._direction: Optional[str] = None
        self._trade_ctx: Optional[TradeContext] = None
        self._entry_confirmed: bool = False
        self._protective_confirmed: bool = False
        self._hard_close_submitted: bool = False
        self._halt_reason: Optional[str] = None
        self._daily_summary_done: bool = False
        self._shutting_down: bool = False
        self._journal_trades: list = []  # accumulates trade dicts for EOD summary

        # Timeout sentinels
        self._fill_timer_start: Optional[datetime] = None
        self._protective_timer_start: Optional[datetime] = None

        # Wire broker callbacks
        self._broker.on_bar = self._on_bar
        self._broker.on_entry_fill = self._on_entry_fill
        self._broker.on_exit_fill = self._on_exit_fill
        self._broker.on_hard_close_fill = self._on_hard_close_fill
        self._broker.on_protective_confirmed = self._on_protective_confirmed
        self._broker.on_order_rejected = self._on_order_rejected
        self._broker.on_disconnected = self._on_disconnected
        self._broker.on_reconnected = self._on_reconnected
        self._broker.on_reconnect_failed = self._on_reconnect_failed

        signal.signal(signal.SIGINT, self._handle_sigint)
        signal.signal(signal.SIGTERM, self._handle_sigint)

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self) -> None:
        L.log_info(self._log, "STARTUP", "BOT_START",
                   f"MES ORB Bot | paper={config.PAPER_TRADING} | "
                   f"symbol={config.SYMBOL} | contracts={config.CONTRACTS}")
        L.log_info(self._log, "STARTUP", "CONFIG",
                   f"range={config.RANGE_START_TIME}-{config.RANGE_END_TIME} | "
                   f"cutoff={config.ENTRY_CUTOFF_TIME} | "
                   f"hardClose={config.HARD_CLOSE_TIME} | "
                   f"safetyTarget×={config.SAFETY_TARGET_MULT} | "
                   f"VIXmax={config.VIX_THRESHOLD} | "
                   f"gapMax={config.GAP_THRESHOLD_PCT:.1%} | "
                   f"dailyLoss=${config.DAILY_LOSS_LIMIT} | "
                   f"directionMode={config.DIRECTION_MODE} | "
                   f"trendFilter={'ON' if config.TREND_FILTER_ENABLED else 'OFF'} | "
                   f"vwapStop={'ON' if config.VWAP_TRAILING_STOP else 'OFF'} | "
                   f"warnExit={'ON' if config.WARNING_SIGNAL_EXIT else 'OFF'}")

        if not self._broker.connect():
            L.log_critical(self._log, "STARTUP", "FATAL", "Cannot connect to IBKR. Exiting.")
            sys.exit(1)

        contract = self._broker.resolve_contract()
        if not contract:
            L.log_critical(self._log, "STARTUP", "FATAL", "Cannot resolve MES contract. Exiting.")
            sys.exit(1)

        # Reconcile any pre-existing positions/orders
        reconcile = self._broker.reconcile()
        if reconcile["mes_position_count"] > 0:
            L.log_warning(self._log, "STARTUP", "UNEXPECTED_POSITION",
                          f"Found {reconcile['mes_position_count']} existing MES position(s). "
                          "Bot did not place these.")
            print("\n⚠️  Existing MES position found. "
                  "Type 'confirm' to continue or anything else to abort: ", end="", flush=True)
            if input().strip().lower() != "confirm":
                self._transition(BotState.HALTED, "Aborted by user — existing position")
                self._broker.disconnect()
                return

        # Fetch 20-day ATR for range width filter
        daily_atr = self._broker.get_daily_atr(config.ATR_PERIOD)
        if daily_atr:
            self._strategy.set_daily_atr(daily_atr)
            self._risk.set_daily_atr(daily_atr)
            L.log_info(self._log, "STARTUP", "ATR_SET",
                       f"20-day ATR = {daily_atr:.2f} pts | "
                       f"width filter: [{config.ATR_MIN_WIDTH_MULT * daily_atr:.2f}, "
                       f"{config.ATR_MAX_WIDTH_MULT * daily_atr:.2f}] pts")
        else:
            L.log_warning(self._log, "STARTUP", "ATR_UNAVAILABLE",
                          f"ATR data unavailable — falling back to fixed width filter "
                          f"[{config.MIN_OPENING_RANGE_POINTS}, {config.MAX_OPENING_RANGE_POINTS}] pts")

        # Previous close for gap filter
        prev_close = self._broker.get_prev_session_close()
        if prev_close:
            self._risk.set_prev_close(prev_close)
        else:
            bypass = config.ALLOW_TRADING_WITHOUT_PREV_CLOSE
            L.log_warning(self._log, "STARTUP", "NO_PREV_CLOSE",
                          f"Previous close unavailable. "
                          f"{'Gap filter will be SKIPPED (ALLOW_TRADING_WITHOUT_PREV_CLOSE=True).' if bypass else 'Gap filter will BLOCK trading.'}")

        # Direction mode + trend filter
        # DIRECTION_MODE controls which breakout sides are eligible.
        # TREND_FILTER_ENABLED further narrows on trend-aligned days only.
        if config.DIRECTION_MODE == "LONG_ONLY":
            if config.TREND_FILTER_ENABLED:
                # LONG_ONLY + trend filter: take LONG only when prior session closed up.
                # If prior session closed down, skip the day entirely.
                trend_dir = self._broker.get_trend_direction()
                if trend_dir == "LONG":
                    self._strategy.set_allowed_direction("LONG")
                    L.log_info(self._log, "STARTUP", "DIRECTION",
                               "LONG_ONLY + trend aligned (prev close > prev-prev) → LONG only")
                elif trend_dir == "SHORT":
                    L.log_info(self._log, "STARTUP", "DIRECTION",
                               "LONG_ONLY + trend counter-trend (prev close < prev-prev) → "
                               "HALTING: no trades today (short side structurally weak, "
                               "counter-trend long avoided)")
                    self._halt("DIRECTION_MODE=LONG_ONLY: counter-trend day — no trade")
                    if not self._broker.subscribe_bars():
                        pass   # still subscribe for EOD cleanup
                    L.log_info(self._log, "STARTUP", "READY",
                               "Skipped day. Waiting for session end...")
                    self._broker.run()
                    return
                else:
                    # Cannot determine trend — allow LONG without trend confirmation
                    self._strategy.set_allowed_direction("LONG")
                    L.log_warning(self._log, "STARTUP", "DIRECTION",
                                  "LONG_ONLY: trend direction unavailable — "
                                  "taking LONG-only without trend filter today")
            else:
                # LONG_ONLY without trend filter: always allow LONG
                self._strategy.set_allowed_direction("LONG")
                L.log_info(self._log, "STARTUP", "DIRECTION",
                           "LONG_ONLY (trend filter OFF) → LONG breakouts only")

        elif config.DIRECTION_MODE == "SHORT_ONLY":
            self._strategy.set_allowed_direction("SHORT")
            L.log_info(self._log, "STARTUP", "DIRECTION",
                       "SHORT_ONLY → SHORT breakouts only")

        else:
            # BOTH: apply standard trend filter if enabled
            if config.TREND_FILTER_ENABLED:
                trend_dir = self._broker.get_trend_direction()
                if trend_dir:
                    self._strategy.set_allowed_direction(trend_dir)
                    L.log_info(self._log, "STARTUP", "TREND_FILTER",
                               f"Trend filter ACTIVE: only {trend_dir} breakouts today "
                               f"(prev close {'>' if trend_dir == 'LONG' else '<'} prev-prev close)")
                else:
                    self._strategy.set_allowed_direction(None)
                    L.log_warning(self._log, "STARTUP", "TREND_FILTER_DISABLED",
                                  "Cannot determine trend direction — both directions active today")
            else:
                self._strategy.set_allowed_direction(None)
                L.log_info(self._log, "STARTUP", "TREND_FILTER",
                           "Trend filter OFF (TREND_FILTER_ENABLED=False). Both directions active.")

        if not self._broker.subscribe_bars():
            L.log_critical(self._log, "STARTUP", "FATAL", "Bar subscription failed. Exiting.")
            sys.exit(1)

        L.log_info(self._log, "STARTUP", "READY",
                   "Initialization complete. Waiting for session bars...")

        # Everything from here is driven by bar callbacks
        self._broker.run()

    # ── State machine ──────────────────────────────────────────────────────────

    def _transition(self, new_state: BotState, reason: str) -> None:
        old = self._state
        self._state = new_state
        L.log_info(self._log, new_state.value, "STATE_TRANSITION",
                   f"{old.value} → {new_state.value} | {reason}")

    def _run_vix_check(self) -> None:
        """
        Pull a one-shot VIX value and apply the configured filter.
        On failure, transition to HALTED before the opening range starts.
        """
        vix = self._broker.get_vix()
        self._risk.set_vix(vix)
        result = self._risk.check_vix()
        L.log_info(self._log, self._state.value, "VIX_FILTER",
                   f"{'PASS' if result.passed else 'FAIL'} | {result.reason}")
        if not result.passed:
            self._halt(result.reason)

    # ── Bar event — main dispatch ──────────────────────────────────────────────

    def _on_bar(self, bar_dt: datetime, bar, receipt_time: datetime) -> None:
        """
        Entry point for every completed 1-minute bar.
        Dispatches to the appropriate state handler then runs timeout checks.
        """
        try:
            now = datetime.now(self._tz)
            t = bar_dt.time()

            # Capture today's open from the first bar at or after RANGE_START_TIME
            if self._risk.today_open is None and t >= self._range_start_time:
                self._risk.set_today_open(bar.open)
                L.log_info(self._log, self._state.value, "TODAY_OPEN",
                            f"Today's open set to {bar.open:.2f}")

            # VIX pull: fires once, one minute before RANGE_START_TIME.
            if (t == self._vix_check_time
                    and self._risk.vix_value is None
                    and self._state not in (BotState.HALTED, BotState.CLOSED)):
                self._run_vix_check()

            # Dispatch
            s = self._state
            if s == BotState.STARTUP:
                self._handle_startup(bar_dt, bar, t)
            elif s == BotState.RANGE_BUILDING:
                self._handle_range_building(bar_dt, bar, t)
            elif s == BotState.WAITING_ENTRY:
                self._handle_waiting_entry(bar_dt, bar, t, receipt_time)
            elif s in (BotState.IN_TRADE_LONG, BotState.IN_TRADE_SHORT):
                self._handle_in_trade(bar_dt, bar, t)
            elif s == BotState.DAILY_DONE:
                self._handle_eod(t)
            elif s == BotState.HALTED:
                self._handle_eod(t)

            # Timeout watchdogs — run every bar regardless of state
            self._check_fill_timeout(now)
            self._check_protective_timeout(now)

            # Write live status for dashboard
            self._write_status()

        except Exception:
            L.log_critical(self._log, self._state.value, "UNHANDLED_EXCEPTION",
                           traceback.format_exc())
            self._emergency_close("unhandled exception in _on_bar")

    # ── State handlers ─────────────────────────────────────────────────────────

    def _handle_startup(self, bar_dt: datetime, bar, t: dtime) -> None:
        if t < self._range_start_time:
            return  # pre-open, wait

        # Run gap filter now that we have today's open
        gap_result = self._risk.check_gap()
        L.log_info(self._log, self._state.value, "GAP_FILTER",
                   f"{'PASS' if gap_result.passed else 'FAIL'} | {gap_result.reason}")

        if not gap_result.passed:
            self._halt(gap_result.reason)
            return

        self._strategy.reset()
        self._transition(BotState.RANGE_BUILDING, "Pre-open filters passed")
        # Process this 09:30 bar as a range bar immediately
        self._handle_range_building(bar_dt, bar, t)

    def _handle_range_building(self, bar_dt: datetime, bar, t: dtime) -> None:
        _range_end = _parse_time(config.RANGE_END_TIME)   # e.g. 10:00
        if t < _range_end:
            s_bar = Bar(bar_dt, bar.open, bar.high, bar.low, bar.close, bar.volume)
            self._strategy.add_range_bar(s_bar)

            # Accumulate VWAP from range bars — needed for early VWAP stops
            vwap = self._strategy.update_vwap(s_bar)

            running_high = max(b.high for b in self._strategy._range_bars)
            running_low = min(b.low for b in self._strategy._range_bars)

            L.log_debug(self._log, self._state.value, "RANGE_BAR",
                        f"{t.strftime('%H:%M')} | "
                        f"H={bar.high:.2f} L={bar.low:.2f} C={bar.close:.2f} V={bar.volume} | "
                        f"RunHigh={running_high:.2f} RunLow={running_low:.2f} | "
                        f"VWAP={vwap:.2f}")
        else:
            # First bar at or after RANGE_END_TIME → lock range
            self._lock_range(bar_dt)

    def _lock_range(self, bar_dt: datetime) -> None:
        rng = self._strategy.lock_range(bar_dt)
        if not rng:
            self._halt("No range bars collected — bot may have started late")
            return

        L.log_info(self._log, self._state.value, "RANGE_LOCKED",
                   f"High={rng.high:.2f} Low={rng.low:.2f} "
                   f"Width={rng.width:.2f}pts | bars={rng.bar_count} | "
                   f"lockedAt={bar_dt.strftime('%H:%M:%S')}")

        # Log every range bar for post-session analysis
        for rb in self._strategy.range_bar_summary():
            L.log_debug(self._log, self._state.value, "RANGE_BAR_DETAIL",
                        f"{rb['time']} O={rb['open']} H={rb['high']} "
                        f"L={rb['low']} C={rb['close']} V={rb['volume']}")

        width_result = self._risk.check_range_width(rng.width)
        L.log_info(self._log, self._state.value, "WIDTH_FILTER",
                   f"{'PASS' if width_result.passed else 'FAIL'} | {width_result.reason}")

        if not width_result.passed:
            self._halt(width_result.reason)
            return

        self._transition(BotState.WAITING_ENTRY, "Range locked and width within bounds")

    def _handle_waiting_entry(
        self, bar_dt: datetime, bar, t: dtime, receipt_time: datetime
    ) -> None:
        # Lock range if this is the first bar at/after RANGE_END_TIME and
        # it hasn't been locked yet.
        if not self._strategy.range_locked:
            self._lock_range(bar_dt)
            if self._state != BotState.WAITING_ENTRY:
                return  # lock may have triggered halt

        if t >= _parse_time(config.ENTRY_CUTOFF_TIME):
            self._transition(BotState.DAILY_DONE, "Entry cutoff reached with no trade")
            return

        rng = self._strategy.opening_range
        dist_high = bar.close - rng.high
        dist_low = bar.close - rng.low

        s_bar = Bar(bar_dt, bar.open, bar.high, bar.low, bar.close, bar.volume)

        # Keep VWAP current while waiting for entry signal
        vwap = self._strategy.update_vwap(s_bar)

        L.log_debug(self._log, self._state.value, "SIGNAL_EVAL",
                    f"{t.strftime('%H:%M')} C={bar.close:.2f} | "
                    f"distToHigh={dist_high:+.2f} distToLow={dist_low:+.2f} | "
                    f"VWAP={vwap:.2f} | "
                    f"evalCount={self._strategy.eval_count + 1}")

        signal = self._strategy.evaluate_signal(s_bar)

        if signal:
            detected_at = datetime.now(self._tz)
            bar_age_ms = (detected_at - receipt_time).total_seconds() * 1000
            L.log_info(self._log, self._state.value, "SIGNAL_DETECTED",
                       f"Signal={signal} | bar={t.strftime('%H:%M')} C={bar.close:.2f} | "
                       f"detectedAt={detected_at.strftime('%H:%M:%S.%f')[:-3]} | "
                       f"barAge={bar_age_ms:.0f}ms after receipt | "
                       f"evalCount={self._strategy.eval_count}")
            self._submit_entry(signal, bar.close, bar_dt)

    def _submit_entry(self, direction: str, ref_price: float, trigger_dt: datetime) -> None:
        rng = self._strategy.opening_range

        # Compute protective levels using the trigger bar close as reference price.
        # These will be refined once the actual fill price is known.
        stop_price, target_price = self._strategy.compute_levels(direction, ref_price)

        L.log_info(self._log, self._state.value, "ENTRY_SETUP",
                   f"direction={direction} refPrice={ref_price:.2f} | "
                   f"stop={stop_price:.2f} target={target_price:.2f} | "
                   f"rangeHigh={rng.high:.2f} rangeLow={rng.low:.2f} | "
                   f"rangeWidth={rng.width:.2f}pts | "
                   f"VIX={self._risk.vix_value} | "
                   f"gap={self._risk.check_gap().value}")

        bundle = self._broker.submit_bracket(direction, stop_price, target_price)
        if not bundle:
            self._halt("Bracket order submission failed")
            return

        self._direction = direction
        self._entry_confirmed = False
        self._protective_confirmed = False
        self._fill_timer_start = datetime.now(self._tz)

        # Store initial trade context (entry_price will be updated on fill)
        gap_val = self._risk.check_gap().value or 0.0
        self._trade_ctx = TradeContext(
            direction=direction,
            entry_price=ref_price,
            stop_price=stop_price,
            target_price=target_price,
            trigger_bar_timestamp=trigger_dt,
            trigger_bar_close=ref_price,
            range=rng,
            signal_eval_count=self._strategy.eval_count,
            vix_at_entry=self._risk.vix_value,
            gap_pct_at_entry=gap_val,
            submitted_at=bundle.submitted_at,
        )

    def _handle_in_trade(self, bar_dt: datetime, bar, t: dtime) -> None:
        s_bar = Bar(bar_dt, bar.open, bar.high, bar.low, bar.close, bar.volume)

        # Always keep VWAP current — needed for stop checks below
        vwap = self._strategy.update_vwap(s_bar)

        # Increment the bars-in-trade counter (used by VWAP_MIN_BARS guard)
        self._strategy.increment_trade_bar()

        bundle = self._broker.bundle
        if bundle and bundle.entry_fill_price:
            ep = bundle.entry_fill_price
            sp = self._trade_ctx.stop_price if self._trade_ctx else "?"
            tp = self._trade_ctx.target_price if self._trade_ctx else "?"
            direction = self._direction

            unreal_pts = (
                (bar.close - ep) if direction == "LONG" else (ep - bar.close)
            )
            unreal_usd = unreal_pts * config.MULTIPLIER * config.CONTRACTS

            L.log_debug(self._log, self._state.value, "POSITION_MONITOR",
                        f"{t.strftime('%H:%M')} C={bar.close:.2f} VWAP={vwap:.2f} | "
                        f"entry={ep:.2f} stop={sp} target={tp} | "
                        f"unrealised={unreal_pts:+.2f}pts "
                        f"(${unreal_usd:+.2f} × {config.CONTRACTS}ct) | "
                        f"bars={self._strategy.bars_in_trade}")

        # ── Dynamic exit checks ────────────────────────────────────────────────
        # Priority: VWAP_STOP > WARNING_EXIT > HARD_CLOSE
        # Only submit one exit per bar; return after submitting.

        if not self._hard_close_submitted:
            # 1. VWAP trailing stop
            if self._strategy.check_vwap_stop(s_bar):
                L.log_info(self._log, self._state.value, "VWAP_STOP",
                           f"Close {bar.close:.2f} crossed VWAP {vwap:.2f} against "
                           f"{self._direction} position. Exiting.")
                self._hard_close_submitted = True
                self._broker.close_position_market(self._direction, reason="VWAP_STOP")
                return

            # 2. Warning signal exit (failed breakout early exit)
            if self._strategy.check_warning_signal(s_bar):
                L.log_info(self._log, self._state.value, "WARNING_EXIT",
                           f"Failed breakout: close {bar.close:.2f} below "
                           f"25% ext without reaching 50% ext. Exiting early.")
                self._hard_close_submitted = True
                self._broker.close_position_market(self._direction, reason="WARNING_EXIT")
                return

            # 3. Hard close at HARD_CLOSE_TIME
            if t >= _parse_time(config.HARD_CLOSE_TIME):
                L.log_info(self._log, self._state.value, "HARD_CLOSE_TRIGGER",
                           f"{config.HARD_CLOSE_TIME} reached. Closing position with market order.")
                self._hard_close_submitted = True
                self._broker.close_position_market(self._direction, reason="HARD_CLOSE")

    def _handle_eod(self, t: dtime) -> None:
        if t >= self._session_end_time and not self._daily_summary_done:
            self._log_daily_summary()
            self._transition(BotState.CLOSED, "Session ended")

    # ── Fill callbacks ─────────────────────────────────────────────────────────

    def _on_entry_fill(self, order_id: int, fill_price: float, fill_time: datetime) -> None:
        if self._entry_confirmed:
            return  # guard against duplicate callbacks

        self._entry_confirmed = True
        self._fill_timer_start = None  # cancel fill timeout
        self._protective_timer_start = datetime.now(self._tz)

        # Recompute stop/target from actual fill price
        stop_price, target_price = self._strategy.compute_levels(self._direction, fill_price)

        # Tell strategy the entry direction so VWAP stop and warning exit can activate
        self._strategy.set_entry(self._direction, fill_price)

        if self._trade_ctx:
            self._trade_ctx.entry_price = fill_price
            self._trade_ctx.stop_price = stop_price
            self._trade_ctx.target_price = target_price
            self._trade_ctx.filled_at = fill_time

        L.log_info(self._log, self._state.value, "ENTRY_CONFIRMED",
                   f"CONFIRMED @ {fill_price:.2f} | "
                   f"direction={self._direction} | "
                   f"stop={stop_price:.2f} target={target_price:.2f} | "
                   f"VWAP@entry={self._strategy.session_vwap:.2f} | "
                   f"submitToFill={_elapsed(self._trade_ctx.submitted_at, fill_time)}")

        new_state = (
            BotState.IN_TRADE_LONG if self._direction == "LONG"
            else BotState.IN_TRADE_SHORT
        )
        self._transition(new_state, f"Entry filled @ {fill_price:.2f}")

    def _on_exit_fill(
        self, order_id: int, fill_price: float, fill_time: datetime, reason: str
    ) -> None:
        self._finalize_trade(fill_price, fill_time, reason)

    def _on_hard_close_fill(
        self, order_id: int, fill_price: float, fill_time: datetime,
        reason: str = "HARD_CLOSE"
    ) -> None:
        self._finalize_trade(fill_price, fill_time, reason)

    def _finalize_trade(
        self, fill_price: float, fill_time: datetime, reason: str
    ) -> None:
        bundle = self._broker.bundle
        if not bundle or bundle.entry_fill_price is None:
            L.log_error(self._log, self._state.value, "FINALIZE_ERROR",
                        "finalize_trade called but no entry fill recorded")
            return

        entry_price = bundle.entry_fill_price
        direction = self._direction

        if direction == "LONG":
            pnl_pts = fill_price - entry_price
        else:
            pnl_pts = entry_price - fill_price

        # Scale by contracts: each point is worth MULTIPLIER × CONTRACTS dollars
        gross_usd = pnl_pts * config.MULTIPLIER * config.CONTRACTS
        # Round-trip commission for all contracts
        commission = config.COMMISSION_PER_CONTRACT * 2 * config.CONTRACTS
        net_usd = gross_usd - commission

        hold_min: Optional[str] = None
        if bundle.entry_filled_at:
            secs = (fill_time - bundle.entry_filled_at).total_seconds()
            hold_min = f"{secs / 60:.1f}min"

        outcome = "WIN" if pnl_pts > 0 else "LOSS"

        L.log_info(self._log, self._state.value, "TRADE_CLOSED",
                   f"{outcome} | reason={reason} | direction={direction} | "
                   f"entry={entry_price:.2f} exit={fill_price:.2f} | "
                   f"pnl={pnl_pts:+.2f}pts | "
                   f"gross=${gross_usd:+.2f} commission=${commission:.2f} net=${net_usd:+.2f} | "
                   f"holdTime={hold_min or 'N/A'}")

        # Log full trade context for fine-tuning
        if self._trade_ctx:
            ctx = self._trade_ctx
            L.log_info(self._log, self._state.value, "TRADE_CONTEXT",
                       f"triggerBar={ctx.trigger_bar_timestamp.strftime('%H:%M')} "
                       f"triggerClose={ctx.trigger_bar_close:.2f} | "
                       f"rangeHigh={ctx.range.high:.2f} rangeLow={ctx.range.low:.2f} "
                       f"rangeWidth={ctx.range.width:.2f}pts | "
                       f"signalEvals={ctx.signal_eval_count} | "
                       f"VIX={ctx.vix_at_entry} gap={ctx.gap_pct_at_entry:.4%}")

        daily = self._risk.record_trade(gross_usd)
        L.log_info(self._log, self._state.value, "DAILY_PNL_UPDATE",
                   daily.summary())

        if self._trade_ctx:
            self._trade_ctx.closed_at = fill_time
            self._trade_ctx.close_price = fill_price
            self._trade_ctx.close_reason = reason

        # Write structured trade record for dashboard
        rng = self._strategy.opening_range
        entry_dt = bundle.entry_filled_at or fill_time
        trade_rec = dict(
            entry_time=entry_dt,
            exit_time=fill_time,
            direction=direction,
            entry_price=entry_price,
            exit_price=fill_price,
            exit_reason=reason,
            pnl_pts=pnl_pts,
            gross_usd=gross_usd,
            commission=commission,
            net_usd=net_usd,
            range_high=rng.high if rng else None,
            range_low=rng.low if rng else None,
            range_width=rng.width if rng else None,
            vix_at_entry=self._risk.vix_value,
            gap_pct=self._risk.check_gap().value if self._risk.prev_close else None,
        )
        journal.append_trade(**trade_rec)
        self._journal_trades.append({
            **trade_rec,
            "entry_time": entry_dt.strftime("%H:%M:%S"),
            "exit_time": fill_time.strftime("%H:%M:%S"),
            "outcome": "WIN" if pnl_pts > 0 else "LOSS",
            "hold_min": round((fill_time - entry_dt).total_seconds() / 60, 1),
        })

        loss_check = self._risk.check_daily_loss_limit()
        if not loss_check.passed:
            self._halt(loss_check.reason)
        else:
            self._transition(BotState.DAILY_DONE, f"Trade closed: {reason} @ {fill_price:.2f}")

        self._broker.clear_bundle()
        self._direction = None
        self._trade_ctx = None
        self._hard_close_submitted = False
        self._entry_confirmed = False
        self._protective_confirmed = False
        self._fill_timer_start = None
        self._protective_timer_start = None

    def _on_protective_confirmed(self) -> None:
        self._protective_confirmed = True
        self._protective_timer_start = None
        L.log_info(self._log, self._state.value, "PROTECTIVE_OK",
                   "Stop and target confirmed live. Position fully protected.")

    def _on_order_rejected(self, order_id: int, reason: str) -> None:
        L.log_critical(self._log, self._state.value, "ORDER_REJECTED",
                       f"orderId={order_id} reason={reason}")
        self._halt(f"Order rejected: {reason}")

    # ── Timeout watchdogs ──────────────────────────────────────────────────────

    def _check_fill_timeout(self, now: datetime) -> None:
        if self._fill_timer_start is None or self._entry_confirmed:
            return
        elapsed = (now - self._fill_timer_start).total_seconds()
        if elapsed > config.FILL_TIMEOUT_SECS:
            L.log_critical(self._log, self._state.value, "FILL_TIMEOUT",
                           f"Entry fill not confirmed after {elapsed:.0f}s. "
                           "Cancelling orders and halting.")
            self._broker.cancel_all_orders()
            self._fill_timer_start = None
            self._halt("Entry fill timeout")

    def _check_protective_timeout(self, now: datetime) -> None:
        if (self._protective_timer_start is None
                or self._protective_confirmed
                or not self._entry_confirmed):
            return
        elapsed = (now - self._protective_timer_start).total_seconds()
        if elapsed > config.PROTECTIVE_ORDER_TIMEOUT:
            L.log_critical(self._log, self._state.value, "PROTECTIVE_TIMEOUT",
                           f"Stop/target not confirmed after {elapsed:.0f}s. "
                           "Emergency closing position.")
            self._protective_timer_start = None
            if self._direction:
                self._broker.close_position_market(
                    self._direction, reason="PROTECTIVE_TIMEOUT"
                )
            self._halt("Protective order confirmation timeout")

    # ── Disconnection ──────────────────────────────────────────────────────────

    def _on_disconnected(self) -> None:
        """
        Called immediately when IBKR drops the connection.
        Does NOT attempt a synchronous reconnect here — that would fail with
        "This event loop is already running" because ib_insync fires this callback
        from inside its asyncio event loop.  The async reconnect is scheduled by
        BrokerClient._on_ib_disconnect() via asyncio.ensure_future(); we simply
        log the event and wait for on_reconnected / on_reconnect_failed to fire.
        """
        if self._shutting_down:
            L.log_info(self._log, self._state.value, "DISCONNECTED",
                       "Broker disconnected during shutdown; reconnect suppressed.")
            return

        in_trade = self._state in (BotState.IN_TRADE_LONG, BotState.IN_TRADE_SHORT)
        L.log_critical(self._log, self._state.value, "DISCONNECTED",
                       f"Lost IBKR connection. In trade={in_trade}. "
                       "Async reconnect loop started by broker...")

    def _on_reconnected(self) -> None:
        """
        Fired by broker after _async_reconnect() succeeds.
        Re-subscribe to bar data and reconcile open positions/orders.
        """
        L.log_info(self._log, self._state.value, "RECONNECTED",
                   "Reconnected to IBKR. Re-subscribing bars and reconciling...")
        if not self._broker.subscribe_bars():
            L.log_critical(self._log, self._state.value, "RECONNECT_BARS_FAILED",
                           "Bar re-subscription failed after reconnect. Halting.")
            self._halt("Bar subscription failed after reconnect")
            return
        reconcile = self._broker.reconcile()
        L.log_info(self._log, self._state.value, "RECONCILE_POST_RECONNECT",
                   f"positions={reconcile['mes_position_count']} "
                   f"orders={reconcile['open_order_count']}")

    def _on_reconnect_failed(self) -> None:
        """
        Fired by broker after all reconnect attempts are exhausted.
        Halts the bot — operator must intervene manually.
        """
        L.log_critical(self._log, self._state.value, "RECONNECT_FAILED",
                       "Could not reconnect after all attempts. Bot halted. "
                       "CHECK POSITION MANUALLY.")
        self._halt("Lost connection, reconnect failed")

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _halt(self, reason: str) -> None:
        self._halt_reason = reason
        self._transition(BotState.HALTED, reason)

    def _emergency_close(self, reason: str) -> None:
        if self._direction and self._state in (BotState.IN_TRADE_LONG, BotState.IN_TRADE_SHORT):
            L.log_critical(self._log, self._state.value, "EMERGENCY_CLOSE",
                           f"Attempting emergency market close. reason={reason}")
            self._broker.close_position_market(self._direction, reason=f"EMERGENCY: {reason}")
        self._halt(reason)

    def _handle_sigint(self, sig, frame) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        L.log_info(self._log, self._state.value, "SIGINT",
                   "Shutdown signal received. Closing safely...")
        in_trade = self._state in (BotState.IN_TRADE_LONG, BotState.IN_TRADE_SHORT)
        if in_trade and self._direction:
            L.log_info(self._log, self._state.value, "SIGINT",
                       "Open position detected. Submitting market close...")
            self._broker.close_position_market(self._direction, reason="SIGINT_SHUTDOWN")
            self._broker.sleep(3)
        self._log_daily_summary()
        self._broker.disconnect()
        sys.exit(0)

    def _log_daily_summary(self) -> None:
        if self._daily_summary_done:
            return
        self._daily_summary_done = True

        pnl = self._risk.daily_pnl
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY", "=" * 70)
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY",
                   f"SESSION SUMMARY — {datetime.now(self._tz).strftime('%Y-%m-%d')}")
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY", pnl.summary())
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY",
                   f"finalState={self._state.value} | haltReason={self._halt_reason or 'N/A'}")
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY",
                   f"VIX={self._risk.vix_value or 'N/A'} | "
                   f"prevClose={self._risk.prev_close or 'N/A'} | "
                   f"todayOpen={self._risk.today_open or 'N/A'} | "
                   f"ATR={self._risk.daily_atr or 'N/A'} | "
                   f"signalEvals={self._strategy.eval_count} | "
                   f"sessionVWAP={self._strategy.session_vwap:.2f}")
        rng = self._strategy.opening_range
        if rng:
            L.log_info(self._log, self._state.value, "DAILY_SUMMARY",
                       f"openingRange: high={rng.high} low={rng.low} "
                       f"width={rng.width}pts bars={rng.bar_count}")
        L.log_info(self._log, self._state.value, "DAILY_SUMMARY", "=" * 70)

        # Structured EOD summary for dashboard
        journal.write_summary(
            trades=self._journal_trades,
            final_state=self._state.value,
            halt_reason=self._halt_reason,
            vix=self._risk.vix_value,
            prev_close=self._risk.prev_close,
            today_open=self._risk.today_open,
            atr=self._risk.daily_atr,
            range_high=rng.high if rng else None,
            range_low=rng.low if rng else None,
            range_width=rng.width if rng else None,
            session_vwap=self._strategy.session_vwap,
            signal_evals=self._strategy.eval_count,
        )

    def _write_status(self) -> None:
        """Write live status.json every bar — picked up by the dashboard."""
        try:
            rng = self._strategy.opening_range
            bundle = self._broker.bundle
            daily = self._risk.daily_pnl

            unreal_pts: Optional[float] = None
            unreal_usd: Optional[float] = None
            if (bundle and bundle.entry_fill_price
                    and self._state in (BotState.IN_TRADE_LONG, BotState.IN_TRADE_SHORT)):
                ep = bundle.entry_fill_price
                # Latest close from broker if available, else skip
                last_close = getattr(self._broker, "_last_close", None)
                if last_close is not None:
                    unreal_pts = (last_close - ep) if self._direction == "LONG" else (ep - last_close)
                    unreal_usd = round(unreal_pts * config.MULTIPLIER * config.CONTRACTS, 2)

            journal.write_status(
                state=self._state.value,
                range_high=rng.high if rng else None,
                range_low=rng.low if rng else None,
                range_width=rng.width if rng else None,
                vwap=self._strategy.session_vwap or None,
                direction=self._direction,
                entry_price=bundle.entry_fill_price if bundle else None,
                stop_price=self._trade_ctx.stop_price if self._trade_ctx else None,
                target_price=self._trade_ctx.target_price if self._trade_ctx else None,
                bars_in_trade=self._strategy.bars_in_trade,
                unrealized_pnl_pts=unreal_pts,
                unrealized_pnl_usd=unreal_usd,
                daily_gross_usd=daily.gross_pnl,
                daily_comm_usd=daily.commissions,
                daily_net_usd=daily.net_pnl,
                daily_trades=daily.trade_count,
                daily_wins=daily.win_count,
                vix=self._risk.vix_value,
                halt_reason=self._halt_reason,
            )
        except Exception:
            pass  # never let dashboard writes crash the bot


# ── Utility ────────────────────────────────────────────────────────────────────

def _elapsed(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None or end is None:
        return "N/A"
    return f"{(end - start).total_seconds() * 1000:.0f}ms"
