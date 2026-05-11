"""Position monitor — polls mark price and closes on 2x credit stop."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

from webull_bot.execution import ExecutionEngine
from webull_bot.logger import BotLogger
from webull_bot.market_data import get_spread_mark
from webull_bot.state import BotState, OpenPosition, StateStore


ET = ZoneInfo("America/New_York")
_MARKET_OPEN = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)


@dataclass
class MonitorOutcome:
    closed: bool
    reason: str
    exit_price: float = 0.0
    pnl_pts: float = 0.0
    pnl_usd: float = 0.0


class PositionMonitor:
    def __init__(
        self,
        execution: ExecutionEngine,
        store: StateStore,
        logger: BotLogger,
        monitor_interval_seconds: int = 30,
        eod_close_time: str = "15:45",
    ):
        self.execution = execution
        self.store = store
        self.logger = logger
        self.interval = monitor_interval_seconds
        h, m = eod_close_time.split(":")
        self.eod_time = dtime(int(h), int(m))

    def run_until_closed(self, state: BotState) -> MonitorOutcome:
        """Block until the position is closed (stop, EOD, or expiry)."""
        pos = state.open_position
        if pos is None:
            return MonitorOutcome(closed=False, reason="no position")

        self.logger.info(
            f"[monitor] watching {pos.symbol} {pos.expiry} "
            f"{pos.short_strike}/{pos.long_strike}P "
            f"credit={pos.entry_credit:.2f} stop={pos.stop_price:.2f}"
        )

        while True:
            now_et = datetime.now(ET)
            now_time = now_et.time()

            in_market_hours = _MARKET_OPEN <= now_time < _MARKET_CLOSE

            # EOD forced close
            if now_time >= self.eod_time:
                self.logger.info(f"[monitor] EOD reached ({self.eod_time}) — letting position expire worthless")
                return self._book_expiry(pos, state)

            if in_market_hours:
                mark = get_spread_mark(
                    short_strike=pos.short_strike,
                    long_strike=pos.long_strike,
                    expiry=pos.expiry,
                    yf_options_symbol=pos.yf_options_symbol,
                )

                if mark is not None:
                    self.logger.info(
                        f"[monitor] mark={mark:.2f}  stop={pos.stop_price:.2f}  "
                        f"({pos.short_strike}/{pos.long_strike}P)"
                    )
                    self.logger.order_event("SPREAD_MARK", {
                        "symbol": pos.symbol,
                        "expiry": pos.expiry,
                        "short_strike": pos.short_strike,
                        "long_strike": pos.long_strike,
                        "mark": mark,
                        "stop": pos.stop_price,
                        "entry_credit": pos.entry_credit,
                    })

                    if mark >= pos.stop_price:
                        self.logger.warning(
                            f"[monitor] STOP LOSS triggered: mark {mark:.2f} >= stop {pos.stop_price:.2f}"
                        )
                        return self._execute_stop(pos, state, mark)
                else:
                    self.logger.warning("[monitor] mark price unavailable — will retry")

            time.sleep(self.interval)

    def _execute_stop(self, pos: OpenPosition, state: BotState, mark: float) -> MonitorOutcome:
        result = self.execution.close_spread_market(
            symbol=pos.symbol,
            expiry=pos.expiry,
            short_strike=pos.short_strike,
            long_strike=pos.long_strike,
            quantity=pos.quantity,
            entry_credit=pos.entry_credit,
        )

        self.logger.order_event("STOP_LOSS_CLOSE", {
            "symbol": pos.symbol,
            "expiry": pos.expiry,
            "short_strike": pos.short_strike,
            "long_strike": pos.long_strike,
            "mark_at_trigger": mark,
            "filled": result.filled,
            "fill_price": result.fill_price,
            "detail": result.detail,
        })

        if result.filled:
            exit_price = result.fill_price
        else:
            # If we couldn't confirm fill, use mark as best estimate and log warning
            self.logger.error(f"[monitor] stop-loss close not confirmed: {result.detail}")
            exit_price = mark

        pnl_pts = pos.entry_credit - exit_price  # credit received - debit paid
        pnl_usd = pnl_pts * 100 * pos.quantity

        self._close_state(state, exit_price, pnl_pts, pnl_usd, "STOP_LOSS")

        return MonitorOutcome(
            closed=True,
            reason="STOP_LOSS",
            exit_price=exit_price,
            pnl_pts=pnl_pts,
            pnl_usd=pnl_usd,
        )

    def _book_expiry(self, pos: OpenPosition, state: BotState) -> MonitorOutcome:
        """Book the position as expired worthless (max profit)."""
        exit_price = 0.0
        pnl_pts = pos.entry_credit
        pnl_usd = pnl_pts * 100 * pos.quantity

        self.logger.order_event("EXPIRED_WORTHLESS", {
            "symbol": pos.symbol,
            "expiry": pos.expiry,
            "short_strike": pos.short_strike,
            "long_strike": pos.long_strike,
            "entry_credit": pos.entry_credit,
            "pnl_pts": pnl_pts,
            "pnl_usd": pnl_usd,
        })

        self._close_state(state, exit_price, pnl_pts, pnl_usd, "EXPIRED")

        return MonitorOutcome(
            closed=True,
            reason="EXPIRED",
            exit_price=exit_price,
            pnl_pts=pnl_pts,
            pnl_usd=pnl_usd,
        )

    def _close_state(
        self,
        state: BotState,
        exit_price: float,
        pnl_pts: float,
        pnl_usd: float,
        reason: str,
    ) -> None:
        pos = state.open_position
        if pos is None:
            return

        state.total_trades += 1
        state.total_pnl += pnl_usd
        if pnl_usd >= 0:
            state.wins += 1
        else:
            state.losses += 1

        self.logger.append_trade(
            date=state.trading_date,
            symbol=pos.symbol,
            expiry=pos.expiry,
            short_strike=pos.short_strike,
            long_strike=pos.long_strike,
            entry_credit=pos.entry_credit,
            entry_spx=pos.entry_spx,
            entry_vix=pos.entry_vix,
            exit_price=exit_price,
            pnl_pts=round(pnl_pts, 2),
            pnl_usd=round(pnl_usd, 2),
            exit_reason=reason,
        )

        state.open_position = None
        self.store.save(state)
        self.logger.info(
            f"[monitor] closed: reason={reason} pnl_pts={pnl_pts:.2f} "
            f"pnl_usd=${pnl_usd:.0f}  total_pnl=${state.total_pnl:.0f}"
        )
