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
from webull_bot.ibkr_market_data import get_spread_mark_ibkr, disconnect as ibkr_disconnect
from webull_bot.state import BotState, OpenPosition, StateStore
from webull_bot.alerts import (
    alert_stop_fired, alert_position_closed,
    alert_close_failed_retry, alert_close_failed_eod,
    alert_close_resolved_externally,
)


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
        # Heartbeat path = same dir as state.json (so dashboard finds it).
        from pathlib import Path as _Path
        self._heartbeat_path = _Path(store.path).parent / "heartbeat.json"

    def _write_heartbeat(self, state: BotState) -> None:
        """Best-effort heartbeat write — mirrors main._write_heartbeat shape so
        the dashboard's parse logic stays unchanged."""
        import json as _json
        from datetime import datetime as _dt
        hb = {
            "ts": _dt.now(ET).isoformat(),
            "trading_date": state.trading_date,
            "trade_taken_today": state.trade_taken_today,
            "total_trades": state.total_trades,
            "wins": state.wins,
            "losses": state.losses,
            "total_pnl": round(state.total_pnl, 2),
            "has_open_position": state.open_position is not None,
        }
        self._heartbeat_path.write_text(_json.dumps(hb), encoding="utf-8")

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
                # Try IBKR real-time first, fall back to yfinance
                mark = get_spread_mark_ibkr(
                    short_strike=pos.short_strike,
                    long_strike=pos.long_strike,
                    expiry=pos.expiry,
                )
                source = "IBKR"
                from webull_bot import data_source_health as _dsh
                if mark is None:
                    _dsh.report("ibkr", up=False)
                    mark = get_spread_mark(
                        short_strike=pos.short_strike,
                        long_strike=pos.long_strike,
                        expiry=pos.expiry,
                        yf_options_symbol=pos.yf_options_symbol,
                    )
                    source = "yfinance"
                else:
                    _dsh.report("ibkr", up=True)
                # Remember the source used for THIS tick so that whenever
                # _close_state runs, it can record which source informed the
                # exit decision (set even if mark is None — caller will see).
                self._last_exit_source = source

                # Heartbeat — write on every monitor tick so dashboard knows
                # bot is alive even when in monitor mode (not just entry-scan).
                # Best-effort: never raise into the trading loop.
                try:
                    self._write_heartbeat(state)
                except Exception:
                    pass

                if mark is not None:
                    self.logger.info(
                        f"[monitor] mark={mark:.2f}  stop={pos.stop_price:.2f}  "
                        f"({pos.short_strike}/{pos.long_strike}P)  [{source}]"
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
                    from webull_bot.event_log import log_event as _le
                    _le("monitor_tick",
                        symbol=pos.symbol, expiry=pos.expiry,
                        short_strike=pos.short_strike, long_strike=pos.long_strike,
                        mark=mark, stop=pos.stop_price,
                        entry_credit=pos.entry_credit,
                        unrealized_pts=round(pos.entry_credit - mark, 2),
                        source=source)

                    if mark >= pos.stop_price:
                        self.logger.warning(
                            f"[monitor] STOP LOSS triggered: mark {mark:.2f} >= stop {pos.stop_price:.2f}"
                        )
                        _le("stop_triggered", mark=mark, stop=pos.stop_price,
                            short_strike=pos.short_strike, long_strike=pos.long_strike, source=source)
                        return self._execute_stop(pos, state, mark)
                else:
                    self.logger.warning("[monitor] mark price unavailable — will retry")
                    from webull_bot.event_log import log_event as _le
                    _le("mark_unavailable",
                        symbol=pos.symbol, short_strike=pos.short_strike, long_strike=pos.long_strike)

            time.sleep(self.interval)

    def _execute_stop(self, pos: OpenPosition, state: BotState, mark: float) -> MonitorOutcome:
        """Trigger stop-loss close. Retries on failure. Per the SL close
        failure handling invariant (memory/sl_close_failure_handling.md):

          - Position is NEVER abandoned in state.json on close failure.
          - Retry close until either (a) it fills, (b) position vanishes
            from broker (user closed manually), or (c) EOD reached.
          - Telegram-alert on EVERY failed attempt with the actual error.
          - At EOD with still-open position, fire LOUD manual-action alert
            and return without modifying state — bot exits monitor loop
            but the position record stays.
        """
        spread_label = f"{int(pos.short_strike)}/{int(pos.long_strike)}P"
        self.logger.warning(
            f"[monitor] STOP LOSS triggered: mark {mark:.2f} >= stop {pos.stop_price:.2f} — beginning close-retry loop"
        )

        retry_wait_sec = 30
        attempt = 0
        last_error = "no attempt yet"

        while True:
            attempt += 1

            # ── Bail-out 1: EOD reached ─────────────────────────────────
            now_et = datetime.now(ET)
            if now_et.time() >= self.eod_time:
                self.logger.error(
                    f"[monitor] EOD ({self.eod_time}) reached with stop-loss close still failing "
                    f"({attempt - 1} attempts). LEAVING POSITION OPEN — manual action required."
                )
                self.logger.order_event("STOP_LOSS_CLOSE_FAILED_EOD", {
                    "symbol": pos.symbol, "expiry": pos.expiry,
                    "short_strike": pos.short_strike, "long_strike": pos.long_strike,
                    "attempts": attempt - 1, "last_error": last_error,
                    "mark_at_trigger": mark,
                })
                from webull_bot.event_log import log_event as _le
                _le("stop_close_failed_eod",
                    short_strike=pos.short_strike, long_strike=pos.long_strike,
                    attempts=attempt - 1, last_error=last_error, mark=mark)
                alert_close_failed_eod(
                    spread=spread_label, attempts=attempt - 1,
                    last_error=last_error, mark=mark, stop=pos.stop_price,
                )
                # Critical: do NOT call _close_state. Position record stays
                # in state.json. Bot returns from monitor loop but position
                # is not booked as closed. User must close in app.
                return MonitorOutcome(
                    closed=False,
                    reason="STOP_CLOSE_FAILED_EOD",
                    exit_price=mark,  # informational only
                    pnl_pts=pos.entry_credit - mark,
                    pnl_usd=(pos.entry_credit - mark) * 100 * pos.quantity,
                )

            # ── Bail-out 2: position vanished from broker (closed externally) ─
            # If user closed in the app between attempts, OR a previous attempt's
            # order eventually filled despite us thinking it failed, the position
            # is gone. Stop retrying — we'd be opening a new position otherwise.
            if attempt > 1:
                try:
                    still_open = self._position_still_at_broker(pos)
                except Exception as exc:
                    self.logger.warning(
                        f"[monitor] could not verify broker position before retry {attempt} "
                        f"({exc}); proceeding with retry"
                    )
                    still_open = True
                if not still_open:
                    self.logger.info(
                        f"[monitor] broker shows position no longer open (closed externally "
                        f"or filled belatedly). Stopping retry loop after {attempt - 1} attempts."
                    )
                    alert_close_resolved_externally(
                        spread=spread_label, attempts=attempt - 1,
                    )
                    # Use mark as best-estimate exit price — we don't know the
                    # real fill since it happened outside our control.
                    exit_price = mark
                    pnl_pts = pos.entry_credit - exit_price
                    pnl_usd = pnl_pts * 100 * pos.quantity
                    self._close_state(state, exit_price, pnl_pts, pnl_usd,
                                      "STOP_LOSS_EXTERNAL")
                    return MonitorOutcome(
                        closed=True, reason="STOP_LOSS_EXTERNAL",
                        exit_price=exit_price, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                    )

            # ── Try the close ───────────────────────────────────────────
            result = self.execution.close_spread_market(
                symbol=pos.symbol, expiry=pos.expiry,
                short_strike=pos.short_strike, long_strike=pos.long_strike,
                quantity=pos.quantity, entry_credit=pos.entry_credit,
            )
            self.logger.order_event("STOP_LOSS_CLOSE_ATTEMPT", {
                "attempt": attempt, "filled": result.filled,
                "fill_price": result.fill_price, "detail": result.detail,
                "mark_at_trigger": mark,
            })

            if result.filled:
                # ── SUCCESS ─────────────────────────────────────────────
                exit_price = result.fill_price
                pnl_pts = pos.entry_credit - exit_price
                pnl_usd = pnl_pts * 100 * pos.quantity
                self._close_state(state, exit_price, pnl_pts, pnl_usd, "STOP_LOSS")
                alert_stop_fired(
                    spread=spread_label, mark=mark,
                    stop=pos.stop_price, filled=True,
                )
                return MonitorOutcome(
                    closed=True, reason="STOP_LOSS",
                    exit_price=exit_price, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                )

            # ── FAILURE — alert + wait + retry ──────────────────────────
            last_error = result.detail or result.status or "unknown"
            self.logger.error(
                f"[monitor] close attempt {attempt} FAILED: {last_error}; "
                f"retrying in {retry_wait_sec}s. Position still open."
            )
            alert_close_failed_retry(
                spread=spread_label, attempt=attempt, error=last_error,
                mark=mark, stop=pos.stop_price, next_retry_sec=retry_wait_sec,
            )
            time.sleep(retry_wait_sec)

    def _position_still_at_broker(self, pos: OpenPosition) -> bool:
        """Best-effort check: is this specific spread still open at Webull?
        Used between SL close-retry attempts to detect external closures
        (user manually closed, or a prior attempt filled belatedly).
        Returns True on any uncertainty — we'd rather over-retry than
        skip a needed close."""
        try:
            # Use the leg iids if we have them — most precise check
            if pos.short_iid and pos.long_iid:
                resp = self.execution.trade.account.get_account_position(
                    account_id=self.execution.account_id,
                )
                holdings = resp.json().get("holdings", []) or []
                iids_present = {str(h.get("instrument_id")) for h in holdings
                                if h.get("instrument_type") == "OPTION"}
                # If EITHER leg is gone, treat the spread as closed/closing
                return (pos.short_iid in iids_present) and (pos.long_iid in iids_present)
        except Exception:
            pass
        # Without iids, can't check reliably — assume still open
        return True

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

        # Capture source provenance: entry-time sources from the OpenPosition
        # record; exit source = whatever the most recent monitor tick used.
        # `getattr` keeps backward compat with old state.json files that don't
        # have these fields (loaded via state.py's filtered constructor).
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
            spx_source=getattr(pos, "spx_source", "unknown"),
            vix_source=getattr(pos, "vix_source", "unknown"),
            chain_source=getattr(pos, "chain_source", "unknown"),
            exit_source=getattr(self, "_last_exit_source", "unknown"),
        )

        state.open_position = None
        self.store.save(state)
        self.logger.info(
            f"[monitor] closed: reason={reason} pnl_pts={pnl_pts:.2f} "
            f"pnl_usd=${pnl_usd:.0f}  total_pnl=${state.total_pnl:.0f}"
        )

        from webull_bot.event_log import log_event as _le
        _le("position_closed",
            symbol=pos.symbol, expiry=pos.expiry,
            short_strike=pos.short_strike, long_strike=pos.long_strike,
            reason=reason, entry_credit=pos.entry_credit,
            exit_price=exit_price, pnl_pts=round(pnl_pts, 2), pnl_usd=round(pnl_usd, 2),
            wins=state.wins, losses=state.losses, total_pnl=round(state.total_pnl, 2))

        alert_position_closed(
            spread=f"{int(pos.short_strike)}/{int(pos.long_strike)}P",
            reason=reason,
            entry_credit=pos.entry_credit,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
            wins=state.wins,
            losses=state.losses,
            total_pnl=state.total_pnl,
        )
