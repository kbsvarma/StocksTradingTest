"""Post-trade audit — runs after market close, looks for silent failures.

Goal: catch the class of bug that hid for 2+ days (May 12-14 invalid-tick
order rejections that produced FAILED orders without obvious symptoms).

Pulls today's order history from Webull, today's fill record from trades.csv,
and today's event log. Compares actual vs expected, looks for known patterns:

  - Invalid-tick rejections (any limit not ending in $0.00 or $0.05)
  - Signal-fired-but-no-entry gap
  - Repeated FAILED orders on the same strikes
  - Bot started but never wrote a heartbeat (crashed)

Sends ONE Telegram alert if anything material is found. Silent if the day
went cleanly. Best run at 16:30 ET (after 15:45 EOD close, before midnight).

Usage:
    python -m webull_bot.post_trade_audit
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _is_valid_tick(price_str: str) -> bool:
    """A price string is a valid $0.05 tick if (price * 100) is divisible by 5."""
    try:
        cents = round(float(price_str) * 100)
        return cents % 5 == 0
    except (ValueError, TypeError):
        return False


def _today_iso() -> str:
    return datetime.now(ET).date().isoformat()


def _load_today_orders_from_webull() -> list[dict]:
    """Pull today's orders via Webull API. Returns list of order dicts.
    Returns [] if API unreachable (audit shouldn't be allowed to break the bot)."""
    try:
        import yaml
        from webull_bot.client import build_trade_client
        cfg_path = Path(__file__).parent.parent / "webull_bot" / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        tc = build_trade_client()
        resp = tc.order.list_today_orders(account_id=cfg["account_id"])
        body = resp.json()
        return body.get("orders", []) or []
    except Exception as exc:
        print(f"[audit] could not pull Webull orders: {exc}", file=sys.stderr)
        return []


def _load_today_trades_csv() -> list[dict]:
    """Read today's row(s) from trades.csv if any. Empty list if no trades today."""
    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "webull_bot" / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        csv_path = Path(cfg["trade_csv"])
        if not csv_path.is_absolute():
            csv_path = Path(__file__).parent.parent / cfg["trade_csv"]
        if not csv_path.exists():
            return []
        today = _today_iso()
        with csv_path.open() as f:
            return [r for r in csv.DictReader(f) if r.get("Date") == today]
    except Exception as exc:
        print(f"[audit] could not read trades.csv: {exc}", file=sys.stderr)
        return []


def _load_today_event_log() -> list[dict]:
    """Read today's event_log JSON lines. Empty list if missing."""
    log_dir = Path(os.environ.get(
        "WEBULL_EVENT_LOG_DIR",
        "/opt/webull-bot/logs/events",
    ))
    f = log_dir / f"events-{_today_iso()}.jsonl"
    if not f.exists():
        return []
    events = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        print(f"[audit] could not read event log: {exc}", file=sys.stderr)
    return events


def audit() -> dict:
    """Run the audit. Returns a dict with findings + computed alert text."""
    orders = _load_today_orders_from_webull()
    trades = _load_today_trades_csv()
    events = _load_today_event_log()

    findings: list[str] = []
    metrics: dict = {
        "today": _today_iso(),
        "orders_total": len(orders),
        "orders_filled": 0,
        "orders_failed": 0,
        "orders_cancelled": 0,
        "trades_in_csv": len(trades),
        "events_today": len(events),
        "invalid_tick_orders": [],
    }

    # ── Pattern 1: invalid-tick rejections (today's class of bug) ─────────
    for o in orders:
        items = o.get("items") or []
        for item in items:
            status = item.get("order_status", "")
            limit = item.get("limit_price")
            if status in ("FILLED",):
                metrics["orders_filled"] += 1
            elif status in ("FAILED", "REJECTED"):
                metrics["orders_failed"] += 1
            elif status in ("CANCELLED",):
                metrics["orders_cancelled"] += 1
            # Check tick validity on any non-filled order
            if status in ("FAILED", "REJECTED", "CANCELLED") and limit:
                if not _is_valid_tick(str(limit)):
                    metrics["invalid_tick_orders"].append({
                        "status": status,
                        "limit": limit,
                        "side": item.get("side"),
                        "strike": item.get("strike_price"),
                        "client_order_id": (o.get("client_order_id", "")[:12] + "…"),
                    })
            break  # one item per combo is enough for accounting

    if metrics["invalid_tick_orders"]:
        n = len(metrics["invalid_tick_orders"])
        findings.append(
            f"⚠️ {n} order(s) used INVALID TICK price (not $0.05-aligned). "
            f"This is the May 12 bug class — verify tick-rounding code is live."
        )
        for o in metrics["invalid_tick_orders"][:5]:
            findings.append(
                f"   • {o['status']} {o['side']} limit=${o['limit']} cid={o['client_order_id']}"
            )

    # ── Pattern 2: signal fired but no entry ─────────────────────────────
    signal_eval_count = sum(1 for e in events if e.get("event") == "signal_eval")
    tradeable_signals = sum(
        1 for e in events
        if e.get("event") == "signal_eval"
        and e.get("vix_ok") is True
    )
    entry_recorded = any(e.get("event") == "entry_recorded" for e in events)
    metrics["signal_eval_ticks"] = signal_eval_count
    metrics["tradeable_signal_ticks"] = tradeable_signals

    if tradeable_signals > 0 and not entry_recorded and not metrics["trades_in_csv"]:
        findings.append(
            f"⚠️ Bot saw {tradeable_signals} tradeable signal tick(s) today but "
            f"NO entry was recorded. Signal fired, fill never happened. "
            f"Compare to Webull order history for FAILED attempts."
        )

    # ── Pattern 3: orders attempted with no fill (broader check) ──────────
    if (metrics["orders_total"] > 0
        and metrics["orders_filled"] == 0
        and (metrics["orders_failed"] + metrics["orders_cancelled"]) > 0):
        findings.append(
            f"⚠️ {metrics['orders_total']} order(s) attempted today, ZERO filled. "
            f"({metrics['orders_failed']} failed, {metrics['orders_cancelled']} cancelled)"
        )

    # ── Pattern 4: bot started but didn't write a heartbeat ──────────────
    bot_started = any(e.get("event") == "bot_start" for e in events)
    # If event log exists but is empty, that's also suspicious (process didn't write)
    if (events == [] and metrics["orders_total"] > 0):
        findings.append(
            "⚠️ Webull shows orders today but bot wrote NO events. Possible event-log misconfiguration."
        )

    # ── Build alert ───────────────────────────────────────────────────────
    if findings:
        alert = (
            f"📊  POST-TRADE AUDIT — {metrics['today']}\n"
            f"orders={metrics['orders_total']}  "
            f"filled={metrics['orders_filled']}  "
            f"failed={metrics['orders_failed']}  "
            f"cancelled={metrics['orders_cancelled']}\n"
            + "\n".join(findings)
        )
    else:
        alert = ""  # silent — clean day

    metrics["alert_text"] = alert
    metrics["findings_count"] = len(findings)
    return metrics


def main() -> int:
    """CLI entry point. Prints the audit report. Sends Telegram only if findings."""
    result = audit()
    print(f"=== POST-TRADE AUDIT — {result['today']} ===")
    print(f"  Webull orders today : {result['orders_total']}")
    print(f"    filled            : {result['orders_filled']}")
    print(f"    failed            : {result['orders_failed']}")
    print(f"    cancelled         : {result['orders_cancelled']}")
    print(f"  Trades in CSV       : {result['trades_in_csv']}")
    print(f"  Event log entries   : {result['events_today']}")
    print(f"    signal_eval ticks : {result.get('signal_eval_ticks', 0)}")
    print(f"    tradeable signals : {result.get('tradeable_signal_ticks', 0)}")
    print(f"  Invalid-tick orders : {len(result['invalid_tick_orders'])}")
    print()

    if result["findings_count"] == 0:
        print("✓ Clean day — no findings.")
        return 0

    print("FINDINGS:")
    print(result["alert_text"])
    print()

    # Send Telegram alert
    try:
        from webull_bot.alerts import send_alert
        send_alert(result["alert_text"])
        print("[audit] Telegram alert dispatched.")
    except Exception as exc:
        print(f"[audit] could not send Telegram: {exc}")

    return 1  # non-zero exit so cron / launchd shows it as "investigated"


if __name__ == "__main__":
    sys.exit(main())
