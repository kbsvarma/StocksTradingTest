"""Webull Bull Put Spread Bot — SPX 0DTE orchestrator.

Run:
    cd /path/to/StocksTradingTest
    WEBULL_APP_KEY=... WEBULL_APP_SECRET=... python -m webull_bot.main

Or with a virtual env:
    conda activate llms && python -m webull_bot.main
"""
from __future__ import annotations

# Install the secret-scrub log filter BEFORE any SDK import. Side-effect import
# registers a logging.Filter on root + webullsdkcore loggers that redacts
# APP_KEY/APP_SECRET/Telegram-token substrings from any log record.
from webull_bot import log_redact  # noqa: F401

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
    find_top_spreads,
    get_spx_open,
    get_spx_price,
    get_vix_open,
    get_vix_price,
)
from webull_bot.monitor import PositionMonitor
from webull_bot.ibkr_market_data import disconnect as ibkr_disconnect
from webull_bot.state import BotState, OpenPosition, StateStore
from webull_bot.alerts import (
    alert_force_entry, alert_close_all, alert_order_event, alert_entry,
)

ET = ZoneInfo("America/New_York")

_RUNNING = True


def _handle_sigterm(sig, frame) -> None:
    global _RUNNING
    _RUNNING = False
    print("\n[main] SIGTERM received — shutting down after current cycle")


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


_SFTP_KEY  = Path.home() / ".ssh" / "lightsail.pem"
_SFTP_HOST = "54.225.195.11"
_SFTP_USER = "ubuntu"
_SFTP_REMOTE_DIR = "/home/ubuntu/StocksTradingTest/spx_spread_bot/data/webull_live/vG_spx_w50_vix25"


def _sync_to_remote(data_dir: Path) -> None:
    import threading, subprocess
    def _do_sync():
        try:
            r = subprocess.run(
                [
                    "rsync", "-az", "--timeout=15",
                    # accept-new: trust the host key on first connect, then verify on subsequent
                    # (vs StrictHostKeyChecking=no which trusts every connection — MITM-able)
                    "-e", f"ssh -i {_SFTP_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10",
                    f"{data_dir}/",
                    f"{_SFTP_USER}@{_SFTP_HOST}:{_SFTP_REMOTE_DIR}/",
                ],
                timeout=30,
                capture_output=True,
            )
            if r.returncode != 0:
                print(f"[sync] rsync failed (rc={r.returncode}): {r.stderr.decode().strip()}", flush=True)
        except Exception as e:
            print(f"[sync] exception: {e}", flush=True)
    t = threading.Thread(target=_do_sync, daemon=True)
    t.start()


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
    _sync_to_remote(Path(cfg["data_dir"]))


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

    import os as _os
    from webull_bot.event_log import log_event as _le
    _le("bot_start",
        symbol=cfg["symbol"],
        spread_width=cfg["spread_width"], otm_pct=cfg["otm_pct"],
        min_credit=cfg["min_credit"], stop_multiplier=cfg["stop_multiplier"],
        vix_min=cfg["vix_min"], vix_max=cfg["vix_max"],
        entry_start=cfg["entry_start"], entry_end=cfg["entry_end"],
        dry_run=(_os.environ.get("WEBULL_DRY_RUN") == "1"))

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
        from webull_bot.event_log import log_event as _le
        _le("signal_eval", spx=spx_price, vix=vix_price, vix_ok=vix_ok, vix_reason=vix_reason)
        if not vix_ok:
            logger.info(f"[main] VIX gate: SKIP — {vix_reason}")
            logger.signal_event("SKIP", {"reason": vix_reason, "vix": vix_price, "spx": spx_price})
            _le("vix_skip", spx=spx_price, vix=vix_price, reason=vix_reason)
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        dir_ok, dir_reason = _check_direction_filter(cfg, spx_price, today)
        if not dir_ok:
            logger.info(f"[main] direction filter: SKIP — {dir_reason}")
            _le("direction_skip", spx=spx_price, vix=vix_price, reason=dir_reason)
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
            _le("no_spread", spx=spx_price, vix=vix_price, otm_pct=cfg["otm_pct"], min_credit=cfg["min_credit"])
            time.sleep(cfg.get("scan_interval_seconds", 30))
            continue

        _le("picked_spread",
            spx=spx_price, vix=vix_price,
            short_strike=spread.short_strike, long_strike=spread.long_strike,
            mid=spread.mid, bid=spread.bid, ask=spread.ask)

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

        # Lock trade_taken_today BEFORE placing — prevents duplicate orders
        # if the bot is killed/restarted mid-fill
        state.trade_taken_today = True
        store.save(state)

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
        _le("entry_attempt",
            symbol=cfg["symbol"], expiry=spread.expiry,
            short_strike=spread.short_strike, long_strike=spread.long_strike,
            limit_price=limit_price, qty=cfg.get("quantity", 1),
            fill_status=fill.status, fill_price=fill.fill_price, filled=fill.filled,
            client_order_id=fill.client_order_id, detail=fill.detail)

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

        alert_entry(
            spread=f"{int(spread.short_strike)}/{int(spread.long_strike)}P",
            qty=cfg.get("quantity", 1),
            credit=fill.fill_price,
            width=int(spread.short_strike - spread.long_strike),
            spx=spx_price,
            vix=vix_price,
            stop_price=stop_price,
        )

        # ── Monitor until close ──────────────────────────────────────────
        outcome = monitor.run_until_closed(state)
        logger.info(
            f"[main] position closed: {outcome.reason}  "
            f"pnl_pts={outcome.pnl_pts:.2f}  pnl_usd=${outcome.pnl_usd:.0f}"
        )

        # Loop continues (trade_once_per_day will gate the next entry)

    ibkr_disconnect()
    logger.info("[main] bot stopped")


def close_all_now() -> None:
    """Emergency close: wipe all open SPXW positions for today's expiry.

    Usage:
        python -m webull_bot.main --close-all

    Always shows a preview and requires explicit confirmation before any order is placed.
    """
    from datetime import date as _date, datetime as _dt
    cfg = load_config()
    trade_client = build_trade_client()
    execution = ExecutionEngine(trade_client, cfg["account_id"])
    expiry = _date.today().isoformat()

    def _t(msg: str) -> None:
        print(f"[{_dt.now().strftime('%H:%M:%S')}] [CLOSE-ALL] {msg}", flush=True)

    # ── Pull known iid → strike from state (authoritative source for legs) ──
    store = StateStore(cfg["state_file"])
    state = store.load()
    known: dict[str, float] = {}
    side: list[dict] = []
    if state.open_position:
        op = state.open_position
        if op.short_iid:
            known[op.short_iid] = float(op.short_strike)
        if op.long_iid:
            known[op.long_iid] = float(op.long_strike)
        # qty/sign fallback if iids were never captured at placement
        side.append({
            "qty": int(op.quantity),
            "short_strike": float(op.short_strike),
            "long_strike":  float(op.long_strike),
        })
    if known:
        _t(f"loaded {len(known)} leg iid→strike entries from state.json")
    elif side:
        _t(f"no leg iids in state — qty-match fallback ready ({side[0]['short_strike']:.0f}/{side[0]['long_strike']:.0f} qty={side[0]['qty']})")
    else:
        _t("no state data — relying on live position scan only")

    # ── Step 1: Preview ──────────────────────────────────────────────────
    _t(f"fetching open SPXW positions for {expiry} ...")
    preview = execution.preview_close_all_today(
        expiry=expiry, symbol=cfg["symbol"], known_iid_strikes=known, side_strikes=side,
    )

    if not preview or all("error" in p or "info" in p for p in preview):
        for p in preview:
            print(f"  {p}")
        print("[CLOSE-ALL] nothing to close.")
        return

    print(f"\n{'='*50}")
    print(f"  POSITIONS TO CLOSE")
    print(f"{'='*50}")
    for p in preview:
        if "error" in p or "info" in p:
            print(f"  {p}")
            continue
        print(
            f"  {p['spread']:>20s}  qty={p['qty']}  "
            f"current_mark={p['current_mark']:.2f}  "
            f"close_limit={p['limit_to_close']:.2f}"
        )
    print(f"{'='*50}")

    # ── Step 2: Confirm ──────────────────────────────────────────────────
    confirm = input("\nConfirm close ALL positions above? [yes/no]: ").strip().lower()
    if confirm != "yes":
        print("[CLOSE-ALL] cancelled — no orders placed.")
        return

    # ── Step 3: Execute ──────────────────────────────────────────────────
    _t("placing close orders NOW (parallel) ...")
    results = execution.close_all_today(
        expiry=expiry, symbol=cfg["symbol"], known_iid_strikes=known, side_strikes=side,
    )
    _t("all close threads returned — collecting results")
    print(f"\n{'='*50}")
    print(f"  CLOSE RESULTS")
    print(f"{'='*50}")
    for r in results:
        if "error" in r or "info" in r:
            print(f"  {r}")
        else:
            status = r.get("fill_status", "?")
            spread = r.get("spread", "?")
            qty    = r.get("qty", "?")
            fp     = r.get("fill_price")
            fp_str = f"  fill_price={fp:.2f}" if fp else ""
            print(f"  {spread}  qty={qty}  [{status}]{fp_str}")
    print("[CLOSE-ALL] done.")
    alert_close_all([r for r in results if isinstance(r, dict) and "spread" in r])


def force_entry_now() -> None:
    """Force a single market-order entry right now, bypassing all gates.

    Usage:
        python -m webull_bot.main --force-entry

    Flow:
      1. Fetches live SPX price.
      2. Scans and shows the top available spreads with bid/ask/mid — no filter
         applied so you see the full picture.
      3. You pick a number (or 'no' to abort).
      4. Shows a final confirmation with exactly what will be placed.
      5. Places ONE market order. No retries, no loops.

    What is bypassed: VIX gate, direction filter, entry window, position guard.
    What is NOT bypassed: symbol whitelist (SPXW/SPX/NDXP only).

    Concurrency: protected by an exclusive flock on /tmp/webull-force-entry.lock
    so only ONE force-entry CLI can run at a time. A second concurrent
    invocation aborts immediately rather than racing the first.
    """
    from datetime import date as _date, datetime as _dt
    import fcntl as _fcntl

    # ── Exclusive lock — prevent concurrent force-entry invocations ──────
    _lockfile_path = "/tmp/webull-force-entry.lock"
    try:
        _lockfile = open(_lockfile_path, "w")
        _fcntl.flock(_lockfile.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[FORCE-ENTRY] another invocation already running (lock {_lockfile_path}). Aborting.")
        return
    except Exception as exc:
        print(f"[FORCE-ENTRY] could not acquire lock ({exc}). Aborting for safety.")
        return

    cfg = load_config()
    trade_client = build_trade_client()
    execution = ExecutionEngine(trade_client, cfg["account_id"])
    store = StateStore(cfg["state_file"])
    state = store.load()
    logger = BotLogger(logs_dir=cfg["logs_dir"], trade_csv=cfg["trade_csv"])

    expiry = _date.today().isoformat()
    symbol = cfg["symbol"]
    yf_opts_sym = cfg.get("yf_options_symbol", "^SPX")
    qty = cfg.get("quantity", 1)

    print("\n[FORCE-ENTRY] ⚠  BYPASS MODE — VIX / direction / window gates are IGNORED")

    # ── Step 0: existing-position safety gate ────────────────────────────
    # Per user-mandated protocol (memory/feedback_force_order_protocol.md):
    # before placing any new force order, scan ALL option positions, show them,
    # and require explicit 'yes' to proceed. Symbol-agnostic check.
    from webull_bot.execution import ALLOWED_OPTION_SYMBOLS
    print("[FORCE-ENTRY] checking existing option positions ...")
    try:
        all_h = []
        last = None
        for _ in range(5):
            r = (trade_client.account.get_account_position(
                    account_id=cfg["account_id"], last_instrument_id=last)
                 if last else
                 trade_client.account.get_account_position(account_id=cfg["account_id"]))
            d = r.json()
            hs = d.get("holdings", [])
            all_h.extend(hs)
            if not d.get("has_next") or not hs: break
            last = hs[-1]["instrument_id"]
        existing_opts = [
            h for h in all_h
            if h.get("symbol") in ALLOWED_OPTION_SYMBOLS
            and h.get("instrument_type") == "OPTION"
        ]
    except Exception as exc:
        print(f"[FORCE-ENTRY] could not verify existing positions ({exc}) — aborting for safety.")
        return

    if existing_opts:
        print()
        print("="*62)
        print("  ⚠  EXISTING OPTION POSITIONS")
        print("="*62)
        for h in existing_opts:
            sym = h.get("symbol")
            iid = h.get("instrument_id")
            q   = h.get("qty")
            cost = h.get("unit_cost")
            lp   = h.get("last_price")
            upl  = h.get("unrealized_profit_loss")
            print(f"  {sym}  iid={iid}  qty={q}  unit_cost={cost}  last={lp}  upl={upl}")
        print("="*62)
        print(f"  You have {len(existing_opts)} open option leg(s).")
        print("  Placing a new SPX BPS adds correlated equity-index exposure.")
        print()
        gate = input(
            "  Knowing this, type 'yes' to proceed with a NEW SPX entry, "
            "anything else to abort: "
        ).strip().lower()
        if gate != "yes":
            print("[FORCE-ENTRY] aborted — existing-positions gate declined.")
            return
        print(f"[FORCE-ENTRY] gate cleared — proceeding with {len(existing_opts)} existing leg(s).")
    else:
        print("[FORCE-ENTRY] no existing option positions ✓")

    # ── Step 1: fetch SPX price ──────────────────────────────────────────
    print("[FORCE-ENTRY] fetching live SPX price ...")
    try:
        spx_price = get_spx_price(cfg.get("yf_price_symbol", "^GSPC"))
    except Exception as exc:
        print(f"[FORCE-ENTRY] ERROR: could not get SPX price: {exc}")
        return

    try:
        vix_price = get_vix_price()
    except Exception:
        vix_price = 0.0

    try:
        vix_open = get_vix_open(_date.today())
    except Exception:
        vix_open = 0.0

    try:
        spx_open = get_spx_open(_date.today(), cfg.get("yf_price_symbol", "^GSPC"))
    except Exception:
        spx_open = 0.0

    # ── Step 2: scan top spreads ─────────────────────────────────────────
    print(f"[FORCE-ENTRY] scanning strikes  (SPX={spx_price:.2f}  VIX={vix_price:.1f}) ...")
    try:
        options = find_top_spreads(
            spx_price=spx_price,
            otm_pct=cfg["otm_pct"],
            spread_width=cfg["spread_width"],
            yf_options_symbol=yf_opts_sym,
            top_n=4,
        )
    except Exception as exc:
        print(f"[FORCE-ENTRY] ERROR: spread scan failed: {exc}")
        return

    if not options:
        print(
            f"[FORCE-ENTRY] No spreads found at current prices "
            f"(SPX={spx_price:.2f}, OTM={cfg['otm_pct']*100:.1f}%, width={cfg['spread_width']}pt). "
            "Check market hours or chain availability."
        )
        return

    # ── Step 3: show recommendations ────────────────────────────────────
    vix_in_zone = cfg["vix_min"] <= vix_price <= cfg["vix_max"]
    vix_zone_str = (f"✓ in zone ({cfg['vix_min']:.0f}-{cfg['vix_max']:.0f})" if vix_in_zone
                    else f"⚠ OUTSIDE zone ({cfg['vix_min']:.0f}-{cfg['vix_max']:.0f})")
    vix_rise = round(vix_price - vix_open, 1) if vix_open > 0 else None
    if vix_rise is not None:
        rise_str = f"+{vix_rise:.1f} from open" if vix_rise >= 0 else f"{vix_rise:.1f} from open"
        if vix_rise > cfg.get("vix_max_daily_rise", 3.0):
            rise_str += f"  ⚠ exceeds {cfg.get('vix_max_daily_rise', 3.0):.0f}pt gate"
    else:
        rise_str = "open n/a"

    spx_chg_pct = ((spx_price - spx_open) / spx_open * 100) if spx_open > 0 else None
    if spx_chg_pct is not None:
        spx_chg_str = (f"+{spx_chg_pct:.2f}% from open" if spx_chg_pct >= 0
                       else f"{spx_chg_pct:.2f}% from open")
    else:
        spx_chg_str = "open n/a"

    print()
    print(f"  SPX : {spx_price:,.2f}   {spx_chg_str}")
    print(f"  VIX : {vix_price:.1f}   {vix_zone_str}   {rise_str}")
    print()
    print(f"  #   {'Strikes':<14}  {'OTM%':>5}  {'Mid':>6}  {'Bid':>6}  {'Ask':>6}")
    for i, q in enumerate(options, 1):
        otm_pct_actual = (spx_price - q.short_strike) / spx_price * 100
        strikes = f"{int(q.short_strike)}/{int(q.long_strike)}P"
        print(f"  {i}   {strikes:<14}  {otm_pct_actual:>4.1f}%  {q.mid:>6.2f}  {q.bid:>6.2f}  {q.ask:>6.2f}")
    print()

    # ── Short advisory ───────────────────────────────────────────────────
    best = options[0]
    advisory_parts = []
    if best.mid < cfg["min_credit"]:
        advisory_parts.append(f"credits thin (best {best.mid:.2f} vs min {cfg['min_credit']:.2f}) — not worth the risk")
    if not vix_in_zone:
        advisory_parts.append("VIX outside your zone")
    if vix_rise is not None and vix_rise > cfg.get("vix_max_daily_rise", 3.0):
        advisory_parts.append("VIX spiking — elevated risk")
    if spx_chg_pct is not None and spx_chg_pct < -0.5:
        advisory_parts.append(f"SPX already down {abs(spx_chg_pct):.1f}% — puts closer to money than OTM% shows")

    if advisory_parts:
        print(f"  ⚠  {' | '.join(advisory_parts)}.")
    else:
        print(f"  ✓  Conditions look good — credits above min, VIX in zone.")

    # ── Step 4: pick ─────────────────────────────────────────────────────
    raw = input(f"\n  Pick a spread [1-{len(options)}] or 'no' to abort: ").strip().lower()
    if raw == "no" or raw == "":
        print("[FORCE-ENTRY] aborted — no order placed.")
        return
    try:
        pick = int(raw)
        if pick < 1 or pick > len(options):
            raise ValueError()
    except ValueError:
        print(f"[FORCE-ENTRY] invalid input '{raw}' — aborted.")
        return

    spread = options[pick - 1]

    # ── Step 5: final confirmation ───────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  ⚠  CONFIRM MARKET ORDER")
    print(f"{'='*62}")
    print(f"  Symbol      : {symbol}")
    print(f"  Expiry      : {spread.expiry}")
    print(f"  Short put   : {int(spread.short_strike)} (sell to open)")
    print(f"  Long put    : {int(spread.long_strike)} (buy to open)")
    print(f"  Width       : {int(spread.short_strike - spread.long_strike)} pts")
    print(f"  Qty         : {qty} contract(s)")
    print(f"  Order type  : MARKET")
    print(f"  Credit est  : {spread.mid:.2f} mid  |  {spread.bid:.2f} bid  |  {spread.ask:.2f} ask")
    print(f"{'='*62}")
    print(f"  ⚠  ONE order. No retries. Fills immediately at market.")
    confirm = input("\n  Type 'yes' to place, anything else to abort: ").strip().lower()

    if confirm != "yes":
        print("[FORCE-ENTRY] aborted — no order placed.")
        return

    # ── Step 5b: stale-quote guard ─────────────────────────────────────
    # If SPX has moved more than `force_entry_max_drift_pct` between the
    # recommendation scan and the user's confirmation, abort. Protects
    # against placing into a stale spread that's already deep in the red.
    max_drift_pct = float(cfg.get("force_entry_max_drift_pct", 0.30))  # 0.30%
    try:
        spx_now = get_spx_price(cfg.get("yf_price_symbol", "^GSPC"))
    except Exception as exc:
        print(f"[FORCE-ENTRY] aborted — could not re-fetch SPX for stale-quote guard ({exc})")
        return
    drift_pct = abs(spx_now - spx_price) / spx_price * 100
    print(f"[FORCE-ENTRY] stale-quote check  scan={spx_price:.2f}  now={spx_now:.2f}  drift={drift_pct:.2f}%  (max={max_drift_pct:.2f}%)")
    if drift_pct > max_drift_pct:
        print(f"[FORCE-ENTRY] ⚠  ABORTED — SPX moved {drift_pct:.2f}% since scan (limit {max_drift_pct:.2f}%). Re-scan and retry.")
        return

    # ── Step 6: lock state BEFORE sending ───────────────────────────────
    state.trade_taken_today = True
    store.save(state)
    logger.info(
        f"[FORCE-ENTRY] state locked, placing MARKET order: "
        f"{symbol} {spread.expiry} {int(spread.short_strike)}/{int(spread.long_strike)}P  qty={qty}"
    )

    fill = execution.place_spread_market(
        symbol=symbol,
        expiry=spread.expiry,
        short_strike=spread.short_strike,
        long_strike=spread.long_strike,
        quantity=qty,
    )

    logger.order_event("FORCE_MARKET_ORDER", {
        "symbol": symbol,
        "expiry": spread.expiry,
        "short_strike": spread.short_strike,
        "long_strike": spread.long_strike,
        "order_type": "MKT",
        "filled": fill.filled,
        "fill_price": fill.fill_price,
        "client_order_id": fill.client_order_id,
        "detail": fill.detail,
    })

    print(f"\n[FORCE-ENTRY] Result: [{fill.status}]  {fill.detail}")
    alert_force_entry(
        spread=f"{int(spread.short_strike)}/{int(spread.long_strike)}P",
        qty=qty,
        fill_price=fill.fill_price,
        filled=fill.filled,
    )

    if not fill.filled:
        # Roll back lock so the day is not wasted on a rejected/timeout order
        print("[FORCE-ENTRY] Order did not fill — resetting trade_taken_today. Check broker manually.")
        state.trade_taken_today = False
        store.save(state)
        return

    # ── Step 7: record position in state ────────────────────────────────
    stop_price = round(fill.fill_price * cfg["stop_multiplier"], 2)
    pos = OpenPosition(
        symbol=symbol,
        expiry=spread.expiry,
        short_strike=spread.short_strike,
        long_strike=spread.long_strike,
        quantity=qty,
        entry_credit=fill.fill_price,
        stop_price=stop_price,
        entry_spx=spx_price,
        entry_vix=vix_price,
        entry_ts=_dt.now(ET).isoformat(),
        client_order_id=fill.client_order_id,
        yf_options_symbol=yf_opts_sym,
        short_iid=fill.short_iid,
        long_iid=fill.long_iid,
    )
    state.open_position = pos
    state.trade_taken_today = True
    if fill.short_iid and fill.long_iid:
        print(f"[FORCE-ENTRY] captured leg iids  short={fill.short_iid}  long={fill.long_iid}")
    else:
        print("[FORCE-ENTRY] WARNING: could not capture leg iids — close-all will need to fall back.")
    store.save(state)

    print(f"\n[FORCE-ENTRY] ✓ FILLED  credit={fill.fill_price:.2f}  stop={stop_price:.2f}")
    print(f"[FORCE-ENTRY] Position saved. Run bot normally to monitor.")


if __name__ == "__main__":
    import sys as _sys
    if "--close-all" in _sys.argv:
        close_all_now()
    elif "--force-entry" in _sys.argv:
        force_entry_now()
    else:
        run()
