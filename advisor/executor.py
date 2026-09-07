"""Tier 1 executor — places ONE approved proposal through the proven
webull_bot execution layer, then arms the SL monitor inline.

Modeled directly on webull_bot.main.force_entry_now() (the battle-tested
ad-hoc path), with the interactive confirms replaced by the Telegram
approval that already happened, plus hard rails re-checked at execution time.

Execution uses LIMIT walk-down (ExecutionEngine.place_spread) — NEVER combo
MARKET (77% slippage, verified 2026-05-15/20). SL monitor starts inline
within ~2s of fill (sl_monitor_invariant_2026_05_20).

DRY-RUN SAFETY: when WEBULL_DRY_RUN=1, state is written to a separate
advisor/data/dryrun_state.json — the live bot's state.json is NEVER touched
by a dry run, so the daemon can't pick up a phantom position.

Usage:
    python -m advisor.executor --proposal AB12 [--no-monitor]
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from advisor import proposals as P
from advisor import telegram_io
from advisor.journal import add as journal_add

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = "/tmp/advisor-executor.lock"


def _dry_run() -> bool:
    return os.environ.get("WEBULL_DRY_RUN") == "1"


def _now() -> datetime:
    return datetime.now(ET)


def _fail(p, detail: str, notify: bool = True) -> int:
    print(f"[executor] REFUSED/FAILED: {detail}")
    try:
        if p.status == "EXECUTING":
            P.transition(p.id, "FAILED", detail)
    except ValueError:
        pass
    if notify:
        telegram_io.send(f"🔴 PROPOSAL {p.id} NOT EXECUTED\n{detail}")
    return 1


def _clock_ok(lim: dict) -> tuple[bool, str]:
    """RTH-entry window check. Bypass only in dry-run via env flag (tests)."""
    if _dry_run() and os.environ.get("ADVISOR_TEST_BYPASS_CLOCK") == "1":
        return True, "clock bypassed (dry-run test)"
    now = _now()
    if now.weekday() > 4:
        return False, f"not a trading day ({now.strftime('%A')})"
    try:
        import exchange_calendars as xc
        import pandas as pd
        calendar = xc.get_calendar("XNYS")
        session = pd.Timestamp(now.date())
        if not calendar.is_session(session):
            return False, "exchange holiday — no entry session"
        if pd.Timestamp(now) >= calendar.session_close(session):
            return False, "exchange session has closed"
    except Exception:
        return False, "exchange calendar unavailable"
    hm = now.strftime("%H:%M")
    if not (lim["rth_entry_start"] <= hm <= lim["rth_entry_end"]):
        return False, (f"outside entry window "
                       f"{lim['rth_entry_start']}-{lim['rth_entry_end']} ET (now {hm})")
    return True, "in window"


def _realized_today_usd(webull_cfg: dict) -> float:
    import csv
    path = REPO_ROOT / webull_cfg["trade_csv"]
    if not path.exists():
        return 0.0
    today = date.today().isoformat()
    total = 0.0
    for r in csv.DictReader(path.open()):
        if r.get("Date") == today:
            try:
                total += float(r.get("PnL USD", 0) or 0)
            except ValueError:
                pass
    return total


def execute(pid: str, run_monitor: bool = True) -> int:
    # ── Exclusive lock — one executor at a time, ever ────────────────────
    lock = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[executor] another executor is running — aborting")
        return 1

    p = P.load(pid)
    if p is None:
        print(f"[executor] unknown proposal {pid}")
        return 1

    # Live trading is a separately authorized product capability, never an
    # implied consequence of running the research terminal.  Both config and
    # environment must opt in, so a copied config or inherited environment is
    # insufficient on its own.
    acfg = P.load_advisor_cfg()
    execution_cfg = acfg.get("execution", {})
    execution_enabled = (
        execution_cfg.get("enabled") is True
        and os.environ.get("ADVISOR_EXECUTION_ENABLED") == "1"
    )
    if not execution_enabled:
        return _fail(p, "live execution is disabled; research-only mode", notify=False)

    # ── Gate 1: status + TTL ─────────────────────────────────────────────
    if p.status != "APPROVED":
        return _fail(p, f"status is {p.status}, not APPROVED — refusing "
                        f"(idempotency guard)", notify=(p.status != "EXECUTED"))
    if p.expired():
        try:
            P.transition(p.id, "EXPIRED", "expired before execution")
        except ValueError:
            pass
        return _fail(p, f"approval expired at {p.expires_ts} — propose again if still valid")

    lim = acfg["limits"]

    # ── Gate 2: clock ────────────────────────────────────────────────────
    ok, why = _clock_ok(lim)
    if not ok:
        return _fail(p, why)

    # ── Gate 3: daily caps ───────────────────────────────────────────────
    executed_today = P.created_today({"EXECUTED", "EXECUTING"}, as_of=_now())
    if len(executed_today) >= lim["max_executions_per_day"]:
        return _fail(p, f"daily execution cap reached "
                        f"({len(executed_today)}/{lim['max_executions_per_day']})")

    # ── Gate 4: re-validate rails (config may have tightened since creation)
    errs = P.validate(p.symbol, p.expiry, p.short_strike, p.long_strike,
                      p.qty, p.limit_price, 0, acfg)
    errs = [e for e in errs if "daily proposal cap" not in e and "ttl" not in e]
    if errs:
        return _fail(p, "rail re-check failed: " + "; ".join(errs))

    # Broker configuration is an external capability. Missing configuration
    # is a clean refusal, never an exception and never a partial transition.
    webull_cfg_path = REPO_ROOT / acfg["paths"]["webull_bot_config"]
    if not webull_cfg_path.is_file():
        return _fail(p, "broker execution capability is not installed", notify=False)
    try:
        webull_cfg = yaml.safe_load(webull_cfg_path.read_text())
    except Exception as exc:
        return _fail(p, f"broker configuration unreadable: {type(exc).__name__}",
                     notify=False)
    realized = _realized_today_usd(webull_cfg)
    if realized <= -lim["daily_realized_loss_cap_usd"]:
        return _fail(p, f"daily loss cap hit (realized today ${realized:.0f}, "
                        f"cap -${lim['daily_realized_loss_cap_usd']}) — no new entries")

    if not run_monitor and not _dry_run():
        return _fail(p, "live execution requires an inline position monitor", notify=False)

    # ── Gate 5: broker setup (imports deferred so Tier 0 never loads these) ─
    from webull_bot.client import build_trade_client
    from webull_bot.execution import ExecutionEngine
    from webull_bot.logger import BotLogger
    from webull_bot.state import OpenPosition, StateStore

    trade_client = build_trade_client()
    execution = ExecutionEngine(trade_client, webull_cfg["account_id"])

    # ── Gate 6: existing position / open order guard (unconditional) ─────
    blocked, reason = execution.has_live_position_or_order()
    if blocked:
        return _fail(p, f"existing position/order guard: {reason}")

    # ── State path: live state.json, or isolated file in dry-run ─────────
    state_path = str(REPO_ROOT / webull_cfg["state_file"])
    if _dry_run():
        state_path = str(REPO_ROOT / "advisor" / "data" / "dryrun_state.json")
        print(f"[executor] DRY RUN — state isolated to {state_path}")
    store = StateStore(state_path)
    state = store.load()
    prior_taken = state.trade_taken_today

    logger = BotLogger(logs_dir=webull_cfg["logs_dir"], trade_csv=webull_cfg["trade_csv"])

    # ── Commit point ─────────────────────────────────────────────────────
    P.transition(p.id, "EXECUTING", "executor started")
    state.trade_taken_today = True
    store.save(state)

    # Validation already enforced tick alignment; round-to-NEAREST tick here is
    # purely float hygiene. (round_down_to_tick floors — 1.90 → 1.85 via float
    # artifact — which would silently give away a tick of credit.)
    tick = lim["tick_size"]
    limit_price = round(round(p.limit_price / tick) * tick, 2)
    spread_s = f"{int(p.short_strike)}/{int(p.long_strike)}P"
    print(f"[executor] placing LIMIT {p.symbol} {p.expiry} {spread_s} "
          f"qty={p.qty} credit≥{limit_price:.2f}")

    fill = execution.place_spread(
        symbol=p.symbol, expiry=p.expiry,
        short_strike=p.short_strike, long_strike=p.long_strike,
        quantity=p.qty, limit_price=limit_price,
        retry_wait_seconds=60,
    )

    if not fill.filled:
        # Roll back the day-lock only if we set it and nothing is open
        if not prior_taken and state.open_position is None:
            state.trade_taken_today = False
            store.save(state)
        return _fail(p, f"order did not fill: [{fill.status}] {fill.detail}")

    # ── Record position + arm SL ─────────────────────────────────────────
    stop_mult = float(webull_cfg["stop_multiplier"])
    stop_price = round(fill.fill_price * stop_mult, 2)
    pos = OpenPosition(
        symbol=p.symbol, expiry=p.expiry,
        short_strike=p.short_strike, long_strike=p.long_strike,
        quantity=p.qty, entry_credit=fill.fill_price, stop_price=stop_price,
        entry_spx=0.0, entry_vix=0.0,           # optional enrichment omitted before protection
        entry_ts=_now().isoformat(),
        client_order_id=fill.client_order_id,
        yf_options_symbol=webull_cfg.get("yf_options_symbol", "^SPX"),
        short_iid=fill.short_iid or "", long_iid=fill.long_iid or "",
    )
    state.open_position = pos
    state.trade_taken_today = True
    store.save(state)

    try:
        p = P.transition(p.id, "EXECUTED", f"filled @ {fill.fill_price:.2f}")
        p.fill_price = fill.fill_price
        p.executed_ts = _now().isoformat()
        P._save(p)
    except Exception as exc:
        print(f"[executor] fill audit failed; position state saved, monitoring required: {exc}")

    def report_fill():
        # Noncritical bookkeeping never delays position monitoring.
        try:
            journal_add({
                "type": "execution", "instrument": p.symbol, "direction": "short_put_spread",
                "conviction": p.conviction,
                "thesis": p.rationale,
                "entry": f"{spread_s} credit {fill.fill_price:.2f}",
                "stop": f"mark {stop_price:.2f} ({stop_mult}x)",
                "target": "expire worthless (0DTE, no PT)",
                "note": f"proposal {p.id}",
            })
            telegram_io.send(
                f"🟢 EXECUTED [{p.id}]  {p.symbol} {spread_s} qty={p.qty}\n"
                f"fill credit ${fill.fill_price:.2f}   stop @ ${stop_price:.2f} ({stop_mult}x)\n"
                f"SL monitor arming now"
            )
            print(f"[executor] ✓ FILLED credit={fill.fill_price:.2f} stop={stop_price:.2f}")

        except Exception as exc:
            print(f"[executor] fill notification/journal failed: {exc}")

    # ── Inline SL monitor (2s invariant) ─────────────────────────────────
    if _dry_run():
        print("[executor] DRY RUN — skipping live SL monitor (no real position)")
        return 0
    if not run_monitor:
        print("[executor] --no-monitor: daemon/watchdog must cover this position")
        return 0
    from webull_bot.monitor import PositionMonitor
    try:
        monitor = PositionMonitor(
            execution=execution, store=store, logger=logger,
            monitor_interval_seconds=webull_cfg.get("monitor_interval_seconds", 2),
            eod_close_time=webull_cfg.get("eod_close_time", "15:45"),
        )
        import threading
        threading.Thread(target=report_fill, name="advisor-fill-report", daemon=True).start()
        outcome = monitor.run_until_closed(state)
        print(f"[executor] monitor exited: {outcome.reason} pnl_usd=${outcome.pnl_usd:.0f}")
        telegram_io.send(f"📕 CLOSED [{p.id}] {spread_s} — {outcome.reason}  "
                         f"P&L ${outcome.pnl_usd:.0f}")
    except Exception as exc:
        msg = (f"🚨 ADVISOR MONITOR CRASHED — {type(exc).__name__}: {exc}\n"
               f"{p.symbol} {spread_s} qty={p.qty} STILL OPEN. "
               f"Reconcile watchdog should catch it; VERIFY MANUALLY NOW.")
        telegram_io.send(msg)
        raise
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--no-monitor", action="store_true")
    a = ap.parse_args()
    return execute(a.proposal.upper(), run_monitor=not a.no_monitor)


if __name__ == "__main__":
    sys.exit(main())
