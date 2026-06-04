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
from webull_bot.ibkr_market_data import (
    get_spread_mark_ibkr,
    open_spread_stream,
    disconnect as ibkr_disconnect,
)
from webull_bot.state import BotState, OpenPosition, StateStore
from webull_bot.alerts import (
    send_alert,
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
        """Block until the position is closed (stop, EOD, or expiry).

        Uses IBKR streaming when available (sub-200ms reaction): subscribes
        ONCE to both legs at start, then polls cached state every ~100ms
        for SL eval. Greeks captured in event_log alongside marks for future
        research (delta-conditional SL etc) — does NOT influence SL today.

        Falls back to per-tick polling (IBKR snapshot or yfinance) if
        streaming fails. SL logic is identical in both paths.
        """
        pos = state.open_position
        if pos is None:
            return MonitorOutcome(closed=False, reason="no position")

        self.logger.info(
            f"[monitor] watching {pos.symbol} {pos.expiry} "
            f"{pos.short_strike}/{pos.long_strike}P "
            f"credit={pos.entry_credit:.2f} stop={pos.stop_price:.2f}"
        )

        # ── T2.2: SL MONITOR ACTIVE Telegram alert (added 2026-05-20) ─────
        # Fired immediately on monitor entry so the user has positive
        # confirmation the SL polling loop is running. Absence of this
        # alert = orphan position (no SL coverage). Non-blocking — wrapped
        # by _safe in alerts.py.
        try:
            send_alert(
                f"🟢 SL MONITOR ACTIVE\n"
                f"{pos.symbol} {int(pos.short_strike)}/{int(pos.long_strike)}P  "
                f"qty={pos.quantity}\n"
                f"credit=${pos.entry_credit:.2f}  stop=${pos.stop_price:.2f}\n"
                f"poll≤{self.interval}s (streaming if IBKR up)"
            )
        except Exception as _e:
            self.logger.warning(f"[monitor] SL-active alert failed (non-fatal): {_e}")

        # Try to open a streaming subscription. On failure (IBKR down, gate
        # disabled, contract qualify fails), stream is None and we drop into
        # the legacy per-tick polling path further down.
        stream = open_spread_stream(
            short_strike=pos.short_strike,
            long_strike=pos.long_strike,
            expiry=pos.expiry,
            symbol=pos.symbol,
        )
        if stream is not None:
            self.logger.info("[monitor] IBKR streaming subscription opened")
        else:
            self.logger.info("[monitor] IBKR streaming unavailable — falling back to polling")

        # Cadence:
        #   stream mode → poll cached values every 100ms (sub-200ms SL reaction)
        #   poll mode   → use configured self.interval (typically 30s)
        # Heartbeat / verbose log every 5s in stream mode (not every tick).
        tick_sleep = 0.1 if stream is not None else self.interval
        verbose_log_every = 5.0  # seconds between INFO mark logs in stream mode
        last_verbose_log = 0.0

        try:
            while True:
                now_et = datetime.now(ET)
                now_time = now_et.time()

                in_market_hours = _MARKET_OPEN <= now_time < _MARKET_CLOSE

                # EOD forced close
                if now_time >= self.eod_time:
                    self.logger.info(f"[monitor] EOD reached ({self.eod_time}) — letting position expire worthless")
                    return self._book_expiry(pos, state)

                if in_market_hours:
                    # ── Read mark + greeks ─────────────────────────────────
                    greeks = None
                    if stream is not None:
                        mark = stream.latest_mark()
                        greeks = stream.latest_greeks()
                        source = "IBKR_stream"
                    else:
                        mark = get_spread_mark_ibkr(
                            short_strike=pos.short_strike,
                            long_strike=pos.long_strike,
                            expiry=pos.expiry,
                        )
                        source = "IBKR"

                    from webull_bot import data_source_health as _dsh
                    if mark is None:
                        _dsh.report("ibkr", up=False)
                        # Fall back to yfinance for THIS tick. We do not tear
                        # down the stream — IBKR may recover on next tick.
                        mark = get_spread_mark(
                            short_strike=pos.short_strike,
                            long_strike=pos.long_strike,
                            expiry=pos.expiry,
                            yf_options_symbol=pos.yf_options_symbol,
                        )
                        source = "yfinance"
                    else:
                        _dsh.report("ibkr", up=True)
                    self._last_exit_source = source

                    # ── Heartbeat (best-effort, never raises) ──────────────
                    try:
                        self._write_heartbeat(state)
                    except Exception:
                        pass

                    if mark is not None:
                        # ── SL trigger check (highest priority) ────────────
                        if mark >= pos.stop_price:
                            trigger_ts = datetime.now(ET)
                            self.logger.warning(
                                f"[monitor] STOP LOSS triggered: mark {mark:.2f} >= stop {pos.stop_price:.2f}"
                            )
                            from webull_bot.event_log import log_event as _le
                            _le("stop_triggered", mark=mark, stop=pos.stop_price,
                                short_strike=pos.short_strike, long_strike=pos.long_strike, source=source)
                            if stream is not None:
                                stream.stop()
                            return self._execute_stop(pos, state, mark, trigger_ts=trigger_ts)

                        # ── Verbose log + event_log write ──────────────────
                        # In stream mode, throttle INFO logs to once per 5s
                        # (otherwise we'd flood with 10 lines/sec). Always
                        # write event_log entries — those are structured.
                        now_mono = time.monotonic()
                        should_verbose = (
                            stream is None
                            or (now_mono - last_verbose_log) >= verbose_log_every
                        )
                        if should_verbose:
                            self.logger.info(
                                f"[monitor] mark={mark:.2f}  stop={pos.stop_price:.2f}  "
                                f"({pos.short_strike}/{pos.long_strike}P)  [{source}]"
                            )
                            last_verbose_log = now_mono

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
                        # Greeks (when available from stream) captured for
                        # future research — NOT used for SL decisions today.
                        tick_payload = dict(
                            symbol=pos.symbol, expiry=pos.expiry,
                            short_strike=pos.short_strike, long_strike=pos.long_strike,
                            mark=mark, stop=pos.stop_price,
                            entry_credit=pos.entry_credit,
                            unrealized_pts=round(pos.entry_credit - mark, 2),
                            source=source,
                        )
                        if greeks is not None:
                            # Flatten short/long greeks under namespaced keys
                            for leg, vals in greeks.items():
                                for k, v in vals.items():
                                    tick_payload[f"{leg}_{k}"] = v
                        _le("monitor_tick", **tick_payload)

                        # 2026-05-22: write tick file for telegram service health check
                        # Telegram service reads this; if stale >30s, alerts "monitor stale"
                        try:
                            import json as _json
                            from pathlib import Path as _Path
                            from datetime import datetime as _dt
                            from zoneinfo import ZoneInfo as _ZI
                            _Path("/tmp/monitor_tick.json").write_text(_json.dumps({
                                "ts": _dt.now(_ZI("America/New_York")).isoformat(),
                                "mark": mark,
                                "stop": pos.stop_price,
                                "source": source,
                                "short_strike": pos.short_strike,
                                "long_strike": pos.long_strike,
                            }))
                        except Exception:
                            pass  # never fail monitor on observability write
                    else:
                        # Throttle "unavailable" warnings the same way as verbose log
                        now_mono = time.monotonic()
                        if stream is None or (now_mono - last_verbose_log) >= verbose_log_every:
                            self.logger.warning("[monitor] mark price unavailable — will retry")
                            last_verbose_log = now_mono
                        from webull_bot.event_log import log_event as _le
                        _le("mark_unavailable",
                            symbol=pos.symbol, short_strike=pos.short_strike, long_strike=pos.long_strike)

                time.sleep(tick_sleep)
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
            # 2026-05-22: clear monitor tick file so telegram service doesn't
            # see stale "fresh" data after monitor exits
            try:
                from pathlib import Path as _Path
                _tick = _Path("/tmp/monitor_tick.json")
                if _tick.exists():
                    _tick.unlink()
            except Exception:
                pass

    def _execute_stop(self, pos: OpenPosition, state: BotState, mark: float,
                       trigger_ts: Optional[datetime] = None) -> MonitorOutcome:
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
                        event_ts=trigger_ts,
                    )
                    # Use mark as best-estimate exit price — we don't know the
                    # real fill since it happened outside our control.
                    exit_price = mark
                    pnl_pts = pos.entry_credit - exit_price
                    pnl_usd = pnl_pts * 100 * pos.quantity
                    self._close_state(state, exit_price, pnl_pts, pnl_usd,
                                      "STOP_LOSS_EXTERNAL", event_ts=trigger_ts)
                    return MonitorOutcome(
                        closed=True, reason="STOP_LOSS_EXTERNAL",
                        exit_price=exit_price, pnl_pts=pnl_pts, pnl_usd=pnl_usd,
                    )

            # ── Try the close ───────────────────────────────────────────
            # A RAISE here (network/API blow-up) must be treated as a failed
            # attempt, not a monitor crash: bubbling out would tear down the
            # whole SL monitor mid-stop. Keep the retry loop alive. (2026-06-04)
            try:
                result = self.execution.close_spread_market(
                    symbol=pos.symbol, expiry=pos.expiry,
                    short_strike=pos.short_strike, long_strike=pos.long_strike,
                    quantity=pos.quantity, entry_credit=pos.entry_credit,
                )
            except Exception as _close_exc:
                last_error = f"{type(_close_exc).__name__}: {_close_exc}"
                self.logger.error(
                    f"[monitor] close attempt {attempt} RAISED: {last_error}; "
                    f"retrying in {retry_wait_sec}s. Position still open."
                )
                alert_close_failed_retry(
                    spread=spread_label, attempt=attempt, error=last_error,
                    mark=mark, stop=pos.stop_price, next_retry_sec=retry_wait_sec,
                    event_ts=trigger_ts,
                )
                time.sleep(retry_wait_sec)
                continue
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
                self._close_state(state, exit_price, pnl_pts, pnl_usd, "STOP_LOSS",
                                  event_ts=trigger_ts)
                alert_stop_fired(
                    spread=spread_label, mark=mark,
                    stop=pos.stop_price, filled=True,
                    event_ts=trigger_ts,
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
                event_ts=trigger_ts,
            )
            time.sleep(retry_wait_sec)

    def _position_still_at_broker(self, pos: OpenPosition) -> bool:
        """Best-effort check: is this specific spread still open at Webull?
        Used between SL close-retry attempts to detect external closures.
        Returns True on any uncertainty — over-retry > miss a needed close.

        Updated 2026-05-22: uses account_v2 combo endpoint. short_iid/long_iid
        in state now both hold the combo's position_id (account_v2 doesn't
        expose per-leg iids). We check if the combo position_id is still in
        the broker's combo list AND if strikes match (defensive — same
        position_id should never have different strikes, but safety first).
        """
        try:
            from webull_bot.safe_api import safe_call
            resp = safe_call(
                self.execution.trade.account_v2.get_account_position,
                account_id=self.execution.account_id,
            )
            if resp is None:
                return True  # API failed entirely → assume still open (safer)
            body = resp.json()
            positions = body if isinstance(body, list) else body.get("data", [])
            for p in positions:
                if (p.get("symbol") == pos.symbol
                        and p.get("option_strategy") == "VERTICAL"
                        and p.get("position_id") == pos.short_iid):
                    # Found the combo — verify strikes match
                    strikes = sorted([float(l.get("option_exercise_price", 0) or 0)
                                      for l in p.get("legs", [])], reverse=True)
                    if len(strikes) == 2 and abs(strikes[0] - pos.short_strike) < 0.01:
                        return True
            # Combo not found by position_id — fallback to strike match
            for p in positions:
                if (p.get("symbol") == pos.symbol
                        and p.get("option_strategy") == "VERTICAL"):
                    strikes = sorted([float(l.get("option_exercise_price", 0) or 0)
                                      for l in p.get("legs", [])], reverse=True)
                    if len(strikes) == 2 and abs(strikes[0] - pos.short_strike) < 0.01:
                        return True  # matched by strikes
            return False  # spread is gone
        except Exception:
            pass
        # On any error, assume still open (safer than skipping a needed close)
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
        event_ts: Optional[datetime] = None,
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
            event_ts=event_ts,
        )
