"""Webull Bull Put Spread Bot — SPX 0DTE orchestrator.

Run:
    cd /path/to/StocksTradingTest
    WEBULL_APP_KEY=... WEBULL_APP_SECRET=... python -m webull_bot.main

Or with a virtual env:
    conda activate llms && python -m webull_bot.main
"""
from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from webull_bot.client import build_trade_client
from webull_bot.execution import ExecutionEngine
from webull_bot.logger import BotLogger
from webull_bot.market_data import (
    find_best_spread,
    get_spx_open,
    get_spx_price,
    get_vix_open,
    get_vix_price,
)
from webull_bot.monitor import PositionMonitor
from webull_bot.state import BotState, OpenPosition, StateStore

ET = ZoneInfo("America/New_York")

_RUNNING = True


def _handle_sigterm(sig, frame) -> None:
    global _RUNNING
    _RUNNING = False
    print("\n[main] SIGTERM received — shutting down after current cycle")


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def _write_heartbeat(state: "BotState", cfg: dict) -> None:
    import json as _json
    hb = {
        "ts": datetime.now(ET).isoformat(),
        "trading_date": state.trading_date,
        "trade_taken_today": state.trade_taken_today,
        "total_trades": state.total_trades,
        "wins": state.wins,
        "losses": state.losses,
        "total_pnl": round(state.total_pnl, 2),
        "has_open_position": state.open_position is not None,
    }
    hb_path = Path(cfg["data_dir"]) / "heartbeat.json"
    hb_path.write_text(_json.dumps(hb), encoding="utf-8")


def load_config(path: str = "webull_bot/config.yaml") -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_time(t: str):
    h, m = t.split(":")
    return (int(h), int(m))


def _in_entry_window(now_et: datetime, entry_start: str, entry_end: str) -> bool:
    sh, sm = _parse_time(entry_start)
    eh, em = _parse_time(entry_end)
    t = now_et.time()
    from datetime import time as dtime
    return dtime(sh, sm) <= t <= dtime(eh, em)


def _is_run_day(now_et: datetime, run_days: list[int]) -> bool:
    return now_et.weekday() in run_days


def _reset_daily_state(state: BotState, today: str, store: StateStore) -> None:
    if state.trading_date != today:
        state.trading_date = today
        state.trade_taken_today = False
        store.save(state)


def _check_vix_gate(cfg: dict, logger: BotLogger, today: date) -> tuple[bool, str, float]:
    """Returns (ok, reason, vix_price)."""
    vix = get_vix_price()
    if vix < cfg["vix_min"] or vix > cfg["vix_max"]:
        return False, f"VIX {vix:.2f} outside [{cfg['vix_min']}, {cfg['vix_max']}]", vix

    if cfg.get("vix_max_daily_rise", 0) > 0:
        vix_open = get_vix_open(today)
        if vix_open > 0:
            rise = vix - vix_open
            if rise > cfg["vix_max_daily_rise"]:
                return False, f"VIX rose {rise:.2f}pts from open ({vix_open:.2f}→{vix:.2f})", vix

    return True, "", vix


def _check_direction_filter(cfg: dict, spx_price: float, today: date) -> tuple[bool, str]:
    if not cfg.get("direction_filter_enabled", True):
        return True, ""
    spx_open = get_spx_open(today, cfg.get("yf_price_symbol", "^GSPC"))
    if spx_open > 0 and spx_price < spx_open:
        return False, f"SPX {spx_price:.2f} < open {spx_open:.2f}"
    return True, ""


def _sleep_with_heartbeat(total_seconds: int, state: "BotState", cfg: dict, chunk: int = 30) -> None:
    """Sleep in small chunks, writing heartbeat each cycle so dashboard stays ALIVE."""
    remaining = total_seconds
    while remaining > 0 and _RUNNING:
        time.sleep(min(chunk, remaining))
        remaining -= chunk
        _write_heartbeat(state, cfg)


def run() -> None:
    cfg = load_config()

    logger = BotLogger(
        logs_dir=cfg["logs_dir"],
        trade_csv=cfg["trade_csv"],
    )
    store = StateStore(cfg["state_file"])
    state = store.load()

    logger.info("=" * 60)
    logger.info("[main] Webull Bull Put Spread Bot starting")
    logger.info(f"[main] Symbol: {cfg['symbol']}  Width: {cfg['spread_width']}pt  OTM: {cfg['otm_pct']*100:.1f}%")
    logger.info(f"[main] Stop: {cfg['stop_multiplier']}x credit  VIX: {cfg['vix_min']}-{cfg['vix_max']}")

    trade_client = build_trade_client()
    execution = ExecutionEngine(trade_client, cfg["account_id"])
    monitor = PositionMonitor(
        execution=execution,
        store=store,
        logger=logger,
        monitor_interval_seconds=cfg.get("monitor_interval_seconds", 30),
        eod_close_time=cfg.get("eod_close_time", "15:45"),
    )

    # If bot restarted mid-day with open position, resume monitoring immediately
    if state.open_position is not None:
        logger.info("[main] resuming monitoring of existing open position")
        outcome = monitor.run_until_closed(state)
        logger.info(f"[main] position closed: {outcome.reason}  PnL=${outcome.pnl_usd:.0f}")

    while _RUNNING:
        now_et = datetime.now(ET)
        today = now_et.date()
        today_str = today.isoformat()

        _reset_daily_state(state, today_str, store)
        _write_heartbeat(state, cfg)

        if not _is_run_day(now_et, cfg.get("run_days", [0, 1, 2, 3, 4])):
            logger.info(f"[main] {now_et.strftime('%A')} not in run_days — sleeping 10min")
            _sleep_with_heartbeat(600, state, cfg)
            continue

        if state.trade_taken_today and cfg.get("trade_once_per_day", True):
            logger.info("[main] trade already taken today — sleeping until next day")
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        if not _in_entry_window(now_et, cfg["entry_start"], cfg["entry_end"]):
            now_time = now_et.time()
            from datetime import time as dtime
            sh, sm = _parse_time(cfg["entry_start"])
            if now_time < dtime(sh, sm):
                # Before window — check every 30s
                logger.info(f"[main] waiting for entry window {cfg['entry_start']} ET — {now_et.strftime('%H:%M')}")
                time.sleep(cfg.get("scan_interval_seconds", 30))
            else:
                # Past cutoff — sleep until next day (check every 10min)
                logger.info(f"[main] past entry cutoff {cfg['entry_end']} ET — no more trades today, sleeping 10min")
                _sleep_with_heartbeat(600, state, cfg)
            continue

        # ── Signal evaluation ────────────────────────────────────────────
        try:
            spx_price = get_spx_price(cfg.get("yf_price_symbol", "^GSPC"))
        except Exception as exc:
            logger.warning(f"[main] SPX price fetch failed: {exc}")
            time.sleep(30)
            continue

        vix_ok, vix_reason, vix_price = _check_vix_gate(cfg, logger, today)
        if not vix_ok:
            logger.info(f"[main] VIX gate: SKIP — {vix_reason}")
            logger.signal_event("SKIP", {"reason": vix_reason, "vix": vix_price, "spx": spx_price})
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        dir_ok, dir_reason = _check_direction_filter(cfg, spx_price, today)
        if not dir_ok:
            logger.info(f"[main] direction filter: SKIP — {dir_reason}")
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        # ── Strike selection ─────────────────────────────────────────────
        yf_opts_sym = cfg.get("yf_options_symbol", "SPX")
        try:
            spread = find_best_spread(
                spx_price=spx_price,
                otm_pct=cfg["otm_pct"],
                spread_width=cfg["spread_width"],
                min_credit=cfg["min_credit"],
                yf_options_symbol=yf_opts_sym,
            )
        except Exception as exc:
            logger.warning(f"[main] spread scan failed: {exc}")
            time.sleep(30)
            continue

        if spread is None:
            logger.info(
                f"[main] no qualifying spread found (SPX={spx_price:.2f} VIX={vix_price:.2f}) — watching"
            )
            logger.signal_event("SKIP", {"reason": "no qualifying spread", "spx": spx_price, "vix": vix_price})
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        logger.info(
            f"[main] SIGNAL: {cfg['symbol']} {spread.expiry}  "
            f"{int(spread.short_strike)}/{int(spread.long_strike)}P  "
            f"credit={spread.mid:.2f}  SPX={spx_price:.2f}  VIX={vix_price:.2f}"
        )
        logger.signal_event("ENTER", {
            "symbol": cfg["symbol"],
            "expiry": spread.expiry,
            "short_strike": spread.short_strike,
            "long_strike": spread.long_strike,
            "credit_mid": spread.mid,
            "spx": spx_price,
            "vix": vix_price,
        })

        # ── Place order ──────────────────────────────────────────────────
        limit_price = round(spread.mid - cfg.get("limit_price_offset", 0.05), 2)
        limit_price = max(limit_price, cfg["min_credit"])

        logger.info(f"[main] placing order: limit={limit_price:.2f}")

        fill = execution.place_spread(
            symbol=cfg["symbol"],
            expiry=spread.expiry,
            short_strike=spread.short_strike,
            long_strike=spread.long_strike,
            quantity=cfg.get("quantity", 1),
            limit_price=limit_price,
            max_retries=cfg.get("max_retries", 5),
            retry_price_step=cfg.get("retry_price_step", 0.05),
            retry_wait_seconds=cfg.get("retry_wait_seconds", 60),
            fill_timeout_seconds=cfg.get("fill_timeout_seconds", 300),
        )

        logger.order_event("PLACE_ORDER", {
            "symbol": cfg["symbol"],
            "expiry": spread.expiry,
            "short_strike": spread.short_strike,
            "long_strike": spread.long_strike,
            "limit_price": limit_price,
            "filled": fill.filled,
            "fill_price": fill.fill_price,
            "client_order_id": fill.client_order_id,
            "detail": fill.detail,
        })

        if not fill.filled:
            logger.warning(f"[main] order not filled: {fill.detail}")
            time.sleep(60)
            continue

        # ── Record position ──────────────────────────────────────────────
        stop_price = round(fill.fill_price * cfg["stop_multiplier"], 2)
        pos = OpenPosition(
            symbol=cfg["symbol"],
            expiry=spread.expiry,
            short_strike=spread.short_strike,
            long_strike=spread.long_strike,
            quantity=cfg.get("quantity", 1),
            entry_credit=fill.fill_price,
            stop_price=stop_price,
            entry_spx=spx_price,
            entry_vix=vix_price,
            entry_ts=datetime.now(ET).isoformat(),
            client_order_id=fill.client_order_id,
            yf_options_symbol=yf_opts_sym,
        )

        state.open_position = pos
        state.trade_taken_today = True
        store.save(state)

        logger.info(
            f"[main] FILLED: credit={fill.fill_price:.2f}  stop={stop_price:.2f}  "
            f"({int(spread.short_strike)}/{int(spread.long_strike)}P)"
        )

        # ── Monitor until close ──────────────────────────────────────────
        outcome = monitor.run_until_closed(state)
        logger.info(
            f"[main] position closed: {outcome.reason}  "
            f"pnl_pts={outcome.pnl_pts:.2f}  pnl_usd=${outcome.pnl_usd:.0f}"
        )

        # Loop continues (trade_once_per_day will gate the next entry)

    logger.info("[main] bot stopped")


if __name__ == "__main__":
    run()
