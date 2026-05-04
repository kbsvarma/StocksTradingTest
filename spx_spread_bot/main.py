from __future__ import annotations

import asyncio
import json
import signal
import sys
import threading
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from ib_insync import IB

from config_loader import BotConfig, load_config
from execution import ExecutionEngine
from market_data import MarketDataService
from models import Decision, ExitReason, LegDirection, RuntimeState, TradeRecord
from monitor import PositionMonitor
from signal_engine import SignalEngine
from state_store import RuntimeStateStore
from trade_logger import BotLogger


class SPXSpreadBotApp:
    def __init__(self, config_path: str = "config.yaml"):
        self.cfg: BotConfig = load_config(config_path)

        self.logger = BotLogger(
            logs_dir=self.cfg.logs_dir,
            signal_events_file=self.cfg.signal_events_file,
            order_events_file=self.cfg.order_events_file,
            tick_events_file=self.cfg.tick_events_file,
            trade_csv_file=self.cfg.trade_csv_file,
            daily_summary_file=self.cfg.daily_summary_file,
        )

        self.ib = IB()
        # Prevent indefinite hangs on blocking ib_insync calls after disconnects.
        self.ib.RequestTimeout = 8
        self.market = MarketDataService(self.ib, self.cfg, self.logger)
        self.execution = ExecutionEngine(self.ib, self.cfg, self.logger)
        self.signal_engine = SignalEngine(self.ib, self.cfg, self.market, self.logger)
        self.monitor = PositionMonitor(self.cfg, self.market, self.execution, self.logger)

        self.state_store = RuntimeStateStore(self.cfg.state_file)
        self.state: RuntimeState = self.state_store.load()
        self._sync_state_aliases()

        self.scheduler = BackgroundScheduler(
            timezone=self.cfg.timezone,
            job_defaults={"coalesce": True, "max_instances": 1},
            # max_workers=1 serialises all jobs through a single thread so that
            # concurrent ib_insync calls never race on loop.run_until_complete().
            executors={"default": {"type": "threadpool", "max_workers": 1}},
        )
        self.tz = ZoneInfo(self.cfg.timezone)
        self.status_path = Path(self.cfg.status_file)
        self._connect_lock = threading.Lock()
        self._last_connection_healthcheck = 0.0

        self._running = False
        self._net_liq: float | None = None
        self._last_price: float = 0.0
        self._last_price_ts: str = ""
        self._last_spread_mark: float | None = None
        self.ib.accountValueEvent += self._on_account_value

    def _ensure_thread_event_loop(self) -> None:
        """Set ib_insync's event loop as the current loop for this APScheduler thread.

        ib_insync captures the asyncio event loop at connect() time and stores it in
        IB.loop.  APScheduler rotates jobs across a thread pool; each fresh worker
        thread starts with no current event loop.  Python 3.10+ raises RuntimeError
        when asyncio.get_event_loop() is called in such a thread, which breaks every
        ib_insync synchronous call (reqTickers, placeOrder, etc.).

        Fix: explicitly set IB.loop as the current loop for this thread so that
        asyncio.get_event_loop() inside ib_insync always finds the right loop.
        """
        ib_loop = getattr(self.ib, "loop", None)
        if ib_loop is not None and not ib_loop.is_closed():
            asyncio.set_event_loop(ib_loop)
            return
        # Fallback for before first connect or after loop is closed.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                asyncio.set_event_loop(asyncio.new_event_loop())
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    def start(self) -> None:
        mode = "paper mode" if self.cfg.paper_trading else "live mode"
        self.logger.info(f"starting {self.cfg.underlying_symbol} multi-strategy bot ({mode})")

        # One-shot connect attempt at startup.  _ensure_connected() won't retry
        # because self._running is still False; calling connect() directly here
        # lets reconcile run with a live connection when the gateway is ready.
        try:
            self.market.connect()
            try:
                self.ib.reqAccountUpdates(True)
            except Exception:  # noqa: BLE001
                pass
            self.logger.info("startup connect succeeded")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"startup connect attempt failed (will retry in main loop): {exc}")

        self._ensure_connected()
        self._rollover_state_if_new_day(self._now())

        try:
            self.market.macro.refresh()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"macro refresh failed at startup: {exc}")

        self._reconcile_state_with_broker()

        # ── Fast-path: if we have open positions skip the heavy prewarm ──────
        # prewarm_option_contracts + smoke-test take ~60s; during that window
        # the monitor loop is not running so stop-loss / profit-target can't
        # fire.  When restarting mid-trade we skip straight to the main loop;
        # the prewarm runs lazily on the first entry attempt.
        if self.state.open_positions:
            self.logger.info(
                f"open positions detected on startup — skipping prewarm, "
                f"entering monitor loop immediately"
            )
            self._running = True
            self._write_status()

            def _stop_handler_fast(signum, _frame):
                self.logger.info(f"received signal {signum}; shutting down")
                self.stop()

            signal.signal(signal.SIGTERM, _stop_handler_fast)
            signal.signal(signal.SIGINT, _stop_handler_fast)
            self._main_loop()
            return

        # Pre-warm reference contracts and option chain in the main thread.
        # market_data caches these after the first successful call, so every
        # subsequent APScheduler worker call hits the in-memory cache without
        # ever calling qualifyContracts() or reqSecDefOptParams() from a thread.
        try:
            self.market.ensure_reference_contracts()
            self.market.load_option_chain()
            self.logger.info("reference contracts and option chain pre-warmed at startup")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"option chain pre-warm failed (will retry on first signal): {exc}")

        # Pre-qualify individual option contracts for likely strike range.
        # qualifyContracts works correctly from the main thread (0.1s response)
        # but reliably times out when called from APScheduler background threads
        # due to the asyncio event-loop not being pumped to receive IBKR responses.
        # Pre-qualifying here populates _option_contract_cache so that all
        # APScheduler calls hit the in-memory cache and never call qualifyContracts.
        try:
            import yfinance as _yf_startup
            _info = _yf_startup.Ticker("^GSPC").fast_info
            _raw_spx = float(
                getattr(_info, "last_price", None)
                or getattr(_info, "regularMarketPrice", None)
                or 0.0
            )
            if _raw_spx > 0:
                _underlying_price = (
                    _raw_spx / 10.0
                    if self.cfg.underlying_symbol.upper() == "XSP"
                    else _raw_spx
                )
                # Determine relevant expiries (0DTE and next 0DTE/2DTE).
                _chain = self.market.load_option_chain()
                from datetime import date as _date
                _today = _date.today().isoformat().replace("-", "")
                _expiries = sorted(
                    e for e in _chain.expirations if e >= _today
                )[:3]  # at most 3 nearest expiries
                self.market.prewarm_option_contracts(
                    _underlying_price, _expiries,
                    rights=("P", "C"),
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"option contract pre-warm failed: {exc}")

        # ── Smoke-test reqTickers before APScheduler starts ──────────────────
        try:
            import yfinance as _yf2
            import time as _t2
            _sp = float(getattr(_yf2.Ticker("^GSPC").fast_info, "last_price", 0) or 0)
            _up2 = _sp / 10.0 if self.cfg.underlying_symbol.upper() == "XSP" else _sp
            if _up2 > 0:
                _chain2 = self.market.load_option_chain()
                _today2 = __import__("datetime").date.today().isoformat().replace("-", "")
                _exp2 = min(e for e in _chain2.expirations if e >= _today2)
                _sw = float(self.cfg.spread_width)
                _short = min(_chain2.strikes, key=lambda s: abs(s - _up2 * 0.985))
                _long = min(_chain2.strikes, key=lambda s: abs(s - (_short - _sw)))
                _c_short = self.market.build_option_contract(_exp2, _short, "P")
                _c_long  = self.market.build_option_contract(_exp2, _long,  "P")
                # 1-contract test
                _t0 = _t2.time()
                _tk1 = self.ib.reqTickers(_c_short)
                self.logger.info(
                    f"reqTickers 1-leg OK: {_short}P bid={_tk1[0].bid} ask={_tk1[0].ask} in {round(_t2.time()-_t0,2)}s"
                )
                # 2-contract test (spread)
                _t0 = _t2.time()
                _tk2 = self.ib.reqTickers(_c_short, _c_long)
                self.logger.info(
                    f"reqTickers 2-leg OK: {_short}P/{_long}P in {round(_t2.time()-_t0,2)}s"
                )
        except Exception as exc:  # noqa: BLE001
            import traceback as _tb2
            self.logger.warning(
                f"reqTickers smoke-test FAILED: {exc!r}\n{''.join(_tb2.format_exception(type(exc), exc, exc.__traceback__))}"
            )

        # Disabled: APScheduler background threads were found to interfere with
        # the asyncio event loop even for non-IB jobs (yfinance HTTP, status writes).
        # All periodic jobs now run in the main loop below.
        # self._schedule_jobs()
        # self.scheduler.start()

        self._running = True
        self._write_status()

        def _stop_handler(signum, _frame):
            self.logger.info(f"received signal {signum}; shutting down")
            self.stop()

        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGINT, _stop_handler)

        self._main_loop()

    def _main_loop(self) -> None:
        """Main event-loop / trading loop.

        _entry_job and _monitor_job make blocking ib_insync calls
        (reqTickers, placeOrder, qualifyContracts). These calls use
        loop.run_until_complete() internally. When called from APScheduler
        background threads the loop cannot receive socket data → TimeoutError.
        Running them here — in the main thread — keeps the event loop
        pumped via ib.sleep() so all I/O callbacks fire correctly.

        Non-IB periodic jobs (_price_job, _status_job, _net_liq_job) are also
        run here rather than APScheduler to avoid thread/event-loop conflicts.
        """
        _last_entry: float = 0.0
        _last_monitor: float = 0.0
        _last_price: float = 0.0
        _last_status: float = 0.0
        _last_net_liq: float = 0.0
        _ENTRY_INTERVAL: float = 5.0
        _MONITOR_INTERVAL: float = max(self.cfg.monitor_interval_seconds, 1)
        _PRICE_INTERVAL: float = 5.0
        _STATUS_INTERVAL: float = 2.0
        _NET_LIQ_INTERVAL: float = 60.0

        while self._running:
            # ib.sleep(0.5) runs the asyncio event loop for 0.5s, processing
            # all pending socket I/O and callbacks. This is the canonical
            # ib_insync pattern: the event loop must be kept pumped so that
            # Connection.data_received() fires for incoming IBKR messages.
            self.ib.sleep(0.5)

            now_ts = time.monotonic()
            now = self._now()

            if now_ts - _last_monitor >= _MONITOR_INTERVAL:
                _last_monitor = now_ts
                try:
                    self._monitor_job()
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"monitor error: {exc}")

            if now_ts - _last_entry >= _ENTRY_INTERVAL:
                _last_entry = now_ts
                try:
                    self._entry_job()
                except Exception as exc:  # noqa: BLE001
                    import traceback as _tb
                    self.logger.warning(
                        f"entry error: {exc!r}\n{''.join(_tb.format_exception(type(exc), exc, exc.__traceback__))}"
                    )

            # Non-IB periodic jobs — safe to run in main thread
            if now_ts - _last_price >= _PRICE_INTERVAL:
                _last_price = now_ts
                try:
                    self._price_job()
                except Exception:  # noqa: BLE001
                    pass

            if now_ts - _last_status >= _STATUS_INTERVAL:
                _last_status = now_ts
                try:
                    self._status_job()
                except Exception:  # noqa: BLE001
                    pass

            if now_ts - _last_net_liq >= _NET_LIQ_INTERVAL:
                _last_net_liq = now_ts
                try:
                    self._net_liq_job()
                except Exception:  # noqa: BLE001
                    pass

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            pass

        try:
            self.market.cancel_tick_streams()
            self.market.disconnect()
        except Exception:  # noqa: BLE001
            pass

        # Guard against overwriting a healthy disk state with degraded in-memory
        # state.  If the bot crashed during startup (positions wiped from memory
        # before the monitor loop ran) and shutdown fires, in-memory will have 0
        # positions while disk may have been manually restored.  Compare counts
        # so we never silently drop open positions on a graceful shutdown.
        _disk_pos_count = 0
        try:
            _disk_pos_count = len(self.state_store.load().open_positions)
        except Exception:  # noqa: BLE001
            pass
        if len(self.state.open_positions) >= _disk_pos_count:
            self.state_store.save(self.state)
        else:
            self.logger.warning(
                f"shutdown: in-memory has {len(self.state.open_positions)} open "
                f"positions but disk has {_disk_pos_count}; skipping state overwrite "
                f"to preserve disk state"
            )
        self._write_status()
        self.logger.info("shutdown complete")

    def _schedule_jobs(self) -> None:
        # NOTE: _entry_job and _monitor_job are run in the main thread
        # (see the main loop in start()) so they are NOT scheduled here.
        # APScheduler only handles jobs that do NOT make blocking ib_insync
        # calls (yfinance price, cache reads, non-IB periodic tasks).
        self.scheduler.add_job(self._status_job, "interval", seconds=2, id="status")
        self.scheduler.add_job(self._net_liq_job, "interval", seconds=60, id="net_liq")
        self.scheduler.add_job(self._price_job, "interval", seconds=5, id="price")
        self.scheduler.add_job(self._macro_refresh_job, "cron", hour=self.cfg.macro_refresh_hour_et, minute=0, id="macro")
        self.scheduler.add_job(self._daily_summary_job, "cron", hour=16, minute=1, id="summary")

    def _ensure_connected(self) -> bool:
        # ib_insync updates isConnected() automatically when the TCP connection
        # drops.  A separate reqCurrentTime() healthcheck is not needed and is
        # harmful: called from APScheduler threads it races on loop.run_until_complete()
        # causing spurious TimeoutErrors that trigger unnecessary reconnects.
        if self.ib.isConnected():
            return True

        with self._connect_lock:
            if self.ib.isConnected():
                return True

            # APScheduler runs jobs in worker threads, which may not have an event loop.
            # ib_insync needs one available for connect/reconnect calls.
            try:
                asyncio.get_event_loop()
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())

            # Retry indefinitely with capped exponential backoff.
            # We never give up: a gateway restart takes 60-90s so 10 × 3s
            # could exhaust before the gateway is ready.  The loop exits only
            # when the bot is stopped (self._running = False).
            attempt = 0
            while self._running:
                attempt += 1
                try:
                    self.market.connect()
                    try:
                        self.ib.reqAccountUpdates(True)
                    except Exception:  # noqa: BLE001
                        pass
                    return True
                except Exception as exc:  # noqa: BLE001
                    err = str(exc).strip() or repr(exc)
                    wait = min(5 * attempt, 60)  # backoff: 5s, 10s, … capped at 60s
                    self.logger.warning(
                        f"IBKR reconnect attempt {attempt} failed: {err}; retrying in {wait}s"
                    )
                    time.sleep(wait)

        return False

    def _entry_job(self) -> None:
        now = self._now()
        self._rollover_state_if_new_day(now)

        if not self._in_entry_window(now):
            return

        enabled = {s.upper() for s in self.cfg.enabled_strategies}
        open_strategies = self._open_strategy_set()
        attempted = {s.upper() for s in self.state.attempted_strategies_today}
        outstanding = [s for s in enabled if s not in open_strategies and s not in attempted]
        if not outstanding:
            return

        if not self._ensure_connected():
            return

        # Evaluate with trade_taken_today=False because we manage per-strategy
        # one-attempt-per-day flow via attempted_strategies_today.
        signal_result = self.signal_engine.evaluate(now, trade_taken_today=False)

        signal_payload = {
            "decision": signal_result.decision.value,
            "reason": signal_result.reason,
            "spx": signal_result.spx_price,
            "vix": signal_result.vix_price,
            "contracts": signal_result.contracts,
            "estimated_margin": signal_result.estimated_margin,
            "auto_place": self.cfg.auto_place_on_signal,
            "paper_trading": self.cfg.paper_trading,
        }

        for cand in signal_result.candidates:
            if not cand.quote:
                continue
            self.logger.signal_event(
                "SIGNAL_CANDIDATE",
                {
                    "strategy": cand.strategy.value,
                    "expiry": cand.expiry,
                    "dte": cand.dte,
                    "short_put_strike": cand.short_put_strike,
                    "short_call_strike": cand.short_call_strike,
                    "long_put_strike": cand.long_put_strike,
                    "long_call_strike": cand.long_call_strike,
                    "quote_mid": cand.quote.mid,
                    "quote_bid": cand.quote.bid,
                    "quote_ask": cand.quote.ask,
                    "max_loss_per_contract": cand.max_loss_per_contract,
                    "notes": cand.notes,
                },
            )

        if signal_result.candidate:
            signal_payload.update(
                {
                    "strategy": signal_result.candidate.strategy.value,
                    "expiry": signal_result.candidate.expiry,
                    "dte": signal_result.candidate.dte,
                    "short_put_strike": signal_result.candidate.short_put_strike,
                    "long_put_strike": signal_result.candidate.long_put_strike,
                    "short_call_strike": signal_result.candidate.short_call_strike,
                    "long_call_strike": signal_result.candidate.long_call_strike,
                    "otm_pct": signal_result.candidate.otm_pct,
                    "target_level": signal_result.candidate.target_level,
                    "quote_mid": signal_result.candidate.quote.mid if signal_result.candidate.quote else 0.0,
                    "max_loss_per_contract": signal_result.candidate.max_loss_per_contract,
                    "legs": self._format_legs(signal_result.candidate.legs),
                }
            )

        self.logger.signal_event("SIGNAL_GENERATED", signal_payload)

        if signal_result.decision == Decision.SKIP:
            self.state.skip_reason_today = signal_result.reason
            self.state_store.save(self.state)
            self._write_status(last_signal=signal_payload)
            return

        ordered_candidates = [
            c
            for c in self.signal_engine.ordered_candidates(signal_result.candidates)
            if c.strategy.value.upper() in outstanding
        ]
        if not ordered_candidates:
            self.state.skip_reason_today = "no new strategy candidate to place"
            self.state_store.save(self.state)
            self._write_status(last_signal=signal_payload)
            return

        self.logger.signal_event("SIGNAL_PUBLISHED", signal_payload)

        if not self.cfg.auto_place_on_signal:
            self.state.skip_reason_today = "pending manual confirmation"
            self.state_store.save(self.state)
            self._write_status(last_signal=signal_payload)
            return

        used_margin = self._used_margin_dollars()
        margin_cap_remaining = max(self.cfg.max_margin_dollars() - used_margin, 0.0)
        available_margin = self.signal_engine.available_margin()
        available_remaining = max(available_margin, 0.0) if available_margin is not None else None

        any_filled = False
        for cand in ordered_candidates:
            strategy_key = cand.strategy.value.upper()
            if strategy_key in self._open_strategy_set():
                continue
            if strategy_key in {s.upper() for s in self.state.attempted_strategies_today}:
                continue

            per_contract_risk = max(cand.max_loss_per_contract, 1.0)
            by_cap = int(margin_cap_remaining // per_contract_risk)
            by_avail = int(available_remaining // per_contract_risk) if available_remaining is not None else self.cfg.max_contracts
            contracts = max(0, min(self.cfg.max_contracts, by_cap, by_avail))
            if contracts <= 0:
                self.state.attempted_strategies_today.append(strategy_key)
                self.logger.order_event(
                    "ENTRY_SKIPPED",
                    {
                        "strategy": strategy_key,
                        "reason": "insufficient remaining margin for strategy",
                        "margin_cap_remaining": margin_cap_remaining,
                        "available_remaining": available_remaining,
                        "max_loss_per_contract": per_contract_risk,
                    },
                )
                continue

            pos, reason = self.execution.place_entry_with_retries(
                cand,
                contracts,
                signal_result.spx_price,
                signal_result.vix_price,
            )
            self.state.attempted_strategies_today.append(strategy_key)

            if pos is None:
                self.logger.order_event("ENTRY_SKIPPED", {"strategy": strategy_key, "reason": reason})
                continue

            pos.max_loss_per_contract = cand.max_loss_per_contract
            self.state.open_positions.append(pos)
            any_filled = True

            consumed = cand.max_loss_per_contract * contracts
            margin_cap_remaining = max(margin_cap_remaining - consumed, 0.0)
            if available_remaining is not None:
                available_remaining = max(available_remaining - consumed, 0.0)

            self.logger.order_event(
                "ENTRY_FILLED",
                {
                    "strategy": pos.strategy,
                    "combo_order_id": pos.combo_order_id,
                    "entry_credit": pos.entry_credit,
                    "contracts": pos.contracts,
                    "legs": self._format_legs(pos.legs),
                    "stop_price": pos.stop_price,
                    "profit_target_price": pos.profit_target_price,
                },
            )

        if any_filled:
            self.state.trade_taken_today = True
            self.state.skip_reason_today = ""
        else:
            self.state.skip_reason_today = "entry attempts did not fill"

        self._sync_state_aliases()
        self.state_store.save(self.state)
        self._refresh_tick_streams()
        self._write_status(last_signal=signal_payload)

    def _monitor_job(self) -> None:
        if not self.state.open_positions:
            return

        if not self._ensure_connected():
            return

        now = self._now()
        survivors = []
        closed_any = False
        for pos in list(self.state.open_positions):
            outcome = self.monitor.evaluate(now, pos)
            if not outcome.closed:
                if outcome.detail:
                    self.logger.warning(f"{pos.strategy}: {outcome.detail}")
                survivors.append(pos)
                continue

            closed_any = True
            self.execution.cancel_protective_orders(pos)

            pnl_per_contract = self._pnl_per_contract(pos.entry_credit, outcome.exit_price, outcome.reason)
            total_pnl = pnl_per_contract * pos.contracts

            trade_record = TradeRecord(
                date=now.date().isoformat(),
                strategy=pos.strategy,
                legs=self._format_legs(pos.legs),
                entry_time=pos.entry_ts.astimezone(self.tz).strftime("%H:%M:%S"),
                spx_price_at_entry=pos.entry_spx,
                vix_at_entry=pos.entry_vix,
                short_put_strike=pos.short_put_strike,
                long_put_strike=self._long_put_hedge(pos),
                short_call_strike=pos.short_call_strike,
                long_call_strike=self._long_call_hedge(pos),
                credit_received=pos.entry_credit,
                contracts=pos.contracts,
                exit_time=now.strftime("%H:%M:%S"),
                exit_price=outcome.exit_price,
                pnl_per_contract=pnl_per_contract,
                total_pnl=total_pnl,
                win_loss="Win" if total_pnl >= 0 else "Loss",
                exit_reason=(outcome.reason.value if outcome.reason else "unknown"),
                notes=outcome.detail,
            )

            self.logger.append_trade(trade_record)
            self.logger.order_event(
                "POSITION_CLOSED",
                {
                    "strategy": pos.strategy,
                    "legs": self._format_legs(pos.legs),
                    "reason": trade_record.exit_reason,
                    "exit_price": trade_record.exit_price,
                    "pnl": total_pnl,
                },
            )

            self._apply_trade_stats(total_pnl)

        if not closed_any:
            return

        self.state.open_positions = survivors
        self._sync_state_aliases()
        self.state_store.save(self.state)
        self._refresh_tick_streams()
        self._write_status()

    def _macro_refresh_job(self) -> None:
        try:
            self.market.macro.refresh()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"macro refresh failed: {exc}")

    def _daily_summary_job(self) -> None:
        today = self._now().date().isoformat()
        daily_pnl = self._sum_trade_pnl_for_date(today)
        win_rate = (self.state.wins / self.state.total_trades) if self.state.total_trades else 0.0

        payload = {
            "date": today,
            "trade_taken": self.state.trade_taken_today,
            "skip_reason": self.state.skip_reason_today,
            "open_position": bool(self.state.open_positions),
            "open_positions_count": len(self.state.open_positions),
            "daily_pnl": daily_pnl,
            "weekly_pnl": self.state.weekly_pnl,
            "monthly_pnl": self.state.monthly_pnl,
            "win_rate": round(win_rate, 4),
            "total_trades": self.state.total_trades,
        }
        self.logger.daily_summary(payload)

    def _on_account_value(self, av) -> None:
        if av.tag == "NetLiquidation" and av.currency == "USD":
            try:
                self._net_liq = float(av.value)
            except (ValueError, TypeError):
                pass

    def _price_job(self) -> None:
        # Fetch the underlying price via yfinance (pure HTTP, no ib_insync call).
        # This avoids the event-loop race condition that causes TimeoutError when
        # multiple APScheduler threads call loop.run_until_complete() simultaneously.
        # SPX uses ^GSPC; XSP (mini-SPX = SPX/10) divides by 10.
        try:
            import yfinance as yf
            symbol = "^GSPC"
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = float(
                getattr(info, "last_price", None)
                or getattr(info, "regularMarketPrice", None)
                or 0.0
            )
            if price <= 0:
                hist = ticker.history(period="2d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
            if self.cfg.underlying_symbol.upper() == "XSP":
                price = price / 10.0
            if price > 0:
                self._last_price = round(price, 2)
                self._last_price_ts = datetime.now(UTC).isoformat()
        except Exception as exc:  # noqa: BLE001
            pass  # retain last known price

    def _status_job(self) -> None:
        self._write_status()

    def _net_liq_job(self) -> None:
        # _on_account_value handles live push updates from IB.  Here we do a
        # lightweight read from ib_insync's in-process account-value cache — no
        # network call, no event-loop touch — so it is safe from any thread.
        if not self.ib.isConnected():
            return
        try:
            for av in self.ib.accountValues():
                if av.tag == "NetLiquidation" and av.currency == "USD":
                    self._net_liq = float(av.value)
                    break
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"net_liq read failed: {exc}")

    def _write_status(self, last_signal: dict | None = None) -> None:
        self._sync_state_aliases()
        # Read latest spread mark from order_events (written by monitor every ~2s)
        spread_marks: dict[str, float] = {}
        try:
            order_events_path = Path(self.cfg.order_events_file)
            if order_events_path.exists():
                lines = order_events_path.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines[-200:]):
                    try:
                        ev = json.loads(line)
                        if ev.get("event") == "SPREAD_MARK":
                            strat = ev.get("strategy", "")
                            if strat and strat not in spread_marks:
                                spread_marks[strat] = float(ev.get("mark", 0.0))
                        if len(spread_marks) >= 4:
                            break
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "paper_trading": self.cfg.paper_trading,
            "auto_place_on_signal": self.cfg.auto_place_on_signal,
            "live_mode_enabled": self.cfg.live_mode_enabled,
            "connected": self.ib.isConnected(),
            "net_liquidation": self._net_liq,
            "underlying_price": self._last_price,
            "underlying_price_ts": self._last_price_ts,
            "spread_marks": spread_marks,
            "state": asdict(self.state),
            "last_signal": last_signal,
        }
        for idx, pos in enumerate(self.state.open_positions):
            if isinstance(pos.entry_ts, datetime):
                payload["state"]["open_positions"][idx]["entry_ts"] = pos.entry_ts.isoformat()
        if self.state.open_position and isinstance(self.state.open_position.entry_ts, datetime):
            payload["state"]["open_position"]["entry_ts"] = self.state.open_position.entry_ts.isoformat()
        self.status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _reconcile_state_with_broker(self) -> None:
        if not self.state.open_positions:
            return

        ib_positions = self.ib.positions()
        conid_qty = {p.contract.conId: p.position for p in ib_positions}

        # Guard: if the broker returned zero positions, it likely means the
        # connection isn't fully ready yet (gateway still starting up, or a
        # brief TCP dropout).  Clearing our state based on empty broker data
        # would wipe live positions incorrectly.  Skip reconcile and let the
        # next startup cycle try again once the connection is stable.
        if not conid_qty and self.state.open_positions:
            self.logger.warning(
                "broker returned no positions during reconcile — "
                "connection may not be ready; skipping to preserve local state"
            )
            return

        open_trades = list(self.ib.openTrades())
        open_order_ids = {t.order.orderId for t in open_trades}

        survivors = []
        changed = False
        for pos in list(self.state.open_positions):
            mismatch = False
            for leg in pos.legs:
                broker_qty = float(conid_qty.get(leg.con_id, 0.0))
                if leg.direction == LegDirection.SHORT and broker_qty >= 0:
                    mismatch = True
                    break
                if leg.direction == LegDirection.LONG and broker_qty <= 0:
                    mismatch = True
                    break
            if mismatch:
                self.logger.warning(f"state position missing at broker; clearing local position strategy={pos.strategy}")
                changed = True
                continue

            # Ensure protective orders are present after restart/reconnect.
            if pos.profit_order_id and pos.profit_order_id in open_order_ids:
                survivors.append(pos)
                continue

            # Recover an already-working profit target order even if its orderId
            # was lost in local state (e.g. restart between placement and persistence).
            expected_leg_ids = {leg.con_id for leg in pos.legs}
            found_existing = False
            for trade in open_trades:
                order = trade.order
                contract = trade.contract

                if getattr(contract, "secType", "") != "BAG":
                    continue
                if (getattr(order, "action", "") or "").upper() != "BUY":
                    continue
                if int(float(getattr(order, "totalQuantity", 0) or 0)) != pos.contracts:
                    continue
                if abs(float(getattr(order, "lmtPrice", 0.0) or 0.0) - pos.profit_target_price) > 0.011:
                    continue

                leg_ids = {leg.conId for leg in (getattr(contract, "comboLegs", None) or [])}
                if expected_leg_ids.issubset(leg_ids):
                    pos.profit_order_id = order.orderId
                    self.logger.info(f"reconciled existing profit target order id={order.orderId} strategy={pos.strategy}")
                    found_existing = True
                    changed = True
                    break
            if found_existing:
                survivors.append(pos)
                continue

            ok, reason = self.execution.place_protective_orders(pos)
            if not ok:
                self.logger.error(f"failed to re-place protection during reconcile strategy={pos.strategy}: {reason}")
            survivors.append(pos)

        self.state.open_positions = survivors
        self._sync_state_aliases()
        if changed:
            self.state_store.save(self.state)
        self._refresh_tick_streams()

    def _rollover_state_if_new_day(self, now: datetime) -> None:
        today_key = now.date().isoformat()
        week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        month_key = now.strftime("%Y-%m")

        if self.state.current_week_key != week_key:
            self.state.current_week_key = week_key
            self.state.weekly_pnl = 0.0

        if self.state.current_month_key != month_key:
            self.state.current_month_key = month_key
            self.state.monthly_pnl = 0.0

        if self.state.current_trading_date != today_key:
            self.state.current_trading_date = today_key
            self.state.trade_taken_today = False
            self.state.attempted_strategies_today = []
            self.state.skip_reason_today = ""

        self._sync_state_aliases()
        self.state_store.save(self.state)

    def _sum_trade_pnl_for_date(self, date_str: str) -> float:
        path = Path(self.cfg.trade_csv_file)
        if not path.exists():
            return 0.0

        import csv

        pnl = 0.0
        with path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("Date") != date_str:
                    continue
                try:
                    pnl += float(row.get("Total PnL") or 0.0)
                except ValueError:
                    continue
        return round(pnl, 2)

    def _apply_trade_stats(self, total_pnl: float) -> None:
        self.state.total_trades += 1
        if total_pnl >= 0:
            self.state.wins += 1
        else:
            self.state.losses += 1
        self.state.weekly_pnl = round(self.state.weekly_pnl + total_pnl, 2)
        self.state.monthly_pnl = round(self.state.monthly_pnl + total_pnl, 2)

    def _sync_state_aliases(self) -> None:
        # Deduplicate attempted strategy keys while preserving insertion order.
        seen = set()
        deduped = []
        for value in self.state.attempted_strategies_today:
            key = str(value).upper()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        self.state.attempted_strategies_today = deduped
        self.state.open_position = self.state.open_positions[0] if self.state.open_positions else None

    def _open_strategy_set(self) -> set[str]:
        return {str(pos.strategy).upper() for pos in self.state.open_positions}

    def _used_margin_dollars(self) -> float:
        total = 0.0
        for pos in self.state.open_positions:
            total += max(float(pos.max_loss_per_contract), 0.0) * max(int(pos.contracts), 0)
        return total

    def _refresh_tick_streams(self) -> None:
        if not self.state.open_positions:
            self.market.cancel_tick_streams()
            return

        contracts = []
        seen_conids = set()
        for pos in self.state.open_positions:
            for contract in self.execution.leg_contracts(pos):
                conid = int(getattr(contract, "conId", 0) or 0)
                if conid and conid in seen_conids:
                    continue
                if conid:
                    seen_conids.add(conid)
                contracts.append(contract)

        if not contracts:
            self.market.cancel_tick_streams()
            return
        self.market.start_tick_streams(contracts)

    @staticmethod
    def _pnl_per_contract(entry_credit: float, exit_price: float, reason: ExitReason | None) -> float:
        if reason == ExitReason.EOD_DISTANCE_SAFE:
            return round(entry_credit * 100.0, 2)
        return round((entry_credit - exit_price) * 100.0, 2)

    @staticmethod
    def _format_legs(legs) -> str:
        tokens = []
        for leg in legs:
            side = "S" if leg.direction == LegDirection.SHORT else "L"
            qty = f"{leg.quantity}x" if int(leg.quantity) > 1 else ""
            strike = float(leg.strike)
            strike_txt = str(int(strike)) if strike.is_integer() else str(strike)
            tokens.append(f"{side}{qty}{leg.right}{strike_txt}")
        return " | ".join(tokens)

    @staticmethod
    def _long_put_hedge(pos) -> float:
        values = [leg.strike for leg in pos.legs if leg.direction == LegDirection.LONG and leg.right == "P"]
        if not values:
            return 0.0
        return min(values)

    @staticmethod
    def _long_call_hedge(pos) -> float:
        values = [leg.strike for leg in pos.legs if leg.direction == LegDirection.LONG and leg.right == "C"]
        if not values:
            return 0.0
        return max(values)

    def _in_entry_window(self, now: datetime) -> bool:
        if not self.cfg.is_trade_day(now.date()):
            return False
        t = now.time()
        return self.cfg.entry_start_time() <= t <= self.cfg.entry_end_time()

    def _now(self) -> datetime:
        return datetime.now(self.tz)


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    app = SPXSpreadBotApp(config_path)
    app.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
