"""Telegram service — drains event queue, sends to Telegram, logs every attempt.

Lifecycle: started by launchd, KeepAlive=true. Runs forever until killed.
Polls /tmp/event_queue.jsonl every 5s. For each unsent event:
  - Send to Telegram via existing alerts.py API
  - Log result to /tmp/telegram_sent.log (id, ts, http_status, response)
  - On send failure: backoff (1s, 2s, 4s, give up)
  - Dedup: same dedup_key within 30 min → suppress

Also sends 30-min heartbeat during market hours with system status.
Also detects component-dead conditions (bot/watchdog/monitor stale).

This is the ONLY process that talks to Telegram. Bot/watchdog don't have
credentials. Eliminates silent send failures.
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

EVENT_QUEUE = Path("/tmp/event_queue.jsonl")
SENT_LOG = Path("/tmp/telegram_sent.log")
DEDUP_FILE = Path("/tmp/telegram_dedup.json")
HANDLED_LOG = Path("/tmp/bot_queue_handled.log")
ROTATE_DIR = Path("/tmp/webull_log_archive")
STATE_PATH = None  # set from config at startup
ROTATE_HOUR_ET = 23  # rotate at 23:00 ET (after market close, before next day)
_last_rotation_date: str = ""  # tracks last YYYY-MM-DD we rotated

POLL_INTERVAL_S = 5
DEDUP_WINDOW_S = 1800        # 30 min — suppress same dedup_key within this window
HEARTBEAT_INTERVAL_S = 1800  # 30 min during market hours
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

# Component staleness thresholds (during market hours)
BOT_HEARTBEAT_STALE_S = 420         # bot main loop heartbeat (covers 5-min slow-poll + margin)
WATCHDOG_STALE_S = 300              # watchdog status file
MONITOR_TICK_STALE_S = 30           # monitor's last tick (only when position open)

# Telegram credentials
_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_API_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage" if _TOKEN else ""

_MD_ESCAPE = re.compile(r"([_*\[\]()~`>#+=|{}.!\-])")


def _log(msg: str) -> None:
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] [telegram] {msg}", flush=True)


def _md_escape(text: str) -> str:
    return _MD_ESCAPE.sub(r"\\\1", text)


def _load_sent_ids() -> set[str]:
    """Read sent.log, return set of already-sent event ids."""
    if not SENT_LOG.exists():
        return set()
    ids = set()
    try:
        for line in SENT_LOG.open():
            try:
                ids.add(json.loads(line).get("id"))
            except Exception:
                continue
    except Exception:
        pass
    return {i for i in ids if i}


def _log_sent(event_id: str, http_status: int, response: str, error: str = "") -> None:
    """Append send result to sent.log. Never raises."""
    try:
        with SENT_LOG.open("a") as f:
            f.write(json.dumps({
                "id": event_id,
                "ts": datetime.now(ET).isoformat(),
                "http_status": http_status,
                "response": response[:200],
                "error": error[:200],
            }) + "\n")
    except Exception:
        pass


def _load_dedup() -> dict:
    if not DEDUP_FILE.exists():
        return {}
    try:
        return json.loads(DEDUP_FILE.read_text())
    except Exception:
        return {}


def _save_dedup(d: dict) -> None:
    try:
        tmp = DEDUP_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d))
        tmp.rename(DEDUP_FILE)
    except Exception:
        pass


def _should_suppress_by_dedup(dedup_key: str, dedup: dict) -> bool:
    if not dedup_key:
        return False
    last = dedup.get(dedup_key)
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last)
        age = (datetime.now(ET) - last_ts).total_seconds()
        return age < DEDUP_WINDOW_S
    except Exception:
        return False


def _send_telegram(text: str, parse_mode: str = "") -> tuple[int, str, str]:
    """Single send attempt. Returns (http_status, response, error)."""
    if not _TOKEN or not _CHAT_ID:
        return (0, "", "credentials missing")
    if not text:
        return (0, "", "empty text")
    try:
        params = {"chat_id": _CHAT_ID, "text": text}
        if parse_mode:
            params["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(_API_URL, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return (r.status, r.read().decode()[:300], "")
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode()[:300] if e.fp else "", str(e))
    except Exception as e:
        return (0, "", str(e))


def _send_with_backoff(event: dict) -> bool:
    """Send with 3-attempt backoff. Returns True on success."""
    subject = event.get("subject", "")
    body = event.get("body", "")
    severity = event.get("severity", "info")

    # Build text — emoji prefix by severity
    prefix = {"critical": "🚨", "warn": "⚠️", "info": "ℹ️"}.get(severity, "•")
    text = f"{prefix} {subject}\n{body}" if body and body != subject else f"{prefix} {subject}"

    backoffs = [0, 2, 5]  # immediate, 2s, 5s
    for attempt, wait in enumerate(backoffs):
        if wait:
            time.sleep(wait)
        http, resp, err = _send_telegram(text)
        if http == 200:
            _log_sent(event["id"], http, resp)
            _log(f"sent {event['id'][:8]} [{severity}] {subject[:60]} (attempt {attempt+1})")
            return True
        else:
            _log(f"send failed attempt {attempt+1}: http={http} err={err[:100]}")
            if http == 0 and "credentials" in err.lower():
                _log_sent(event["id"], http, resp, err)
                return False  # don't retry — fundamental config issue
    _log_sent(event["id"], 0, "", "exhausted 3 attempts")
    return False


def _read_state_json() -> dict:
    """Read bot's state.json. Returns {} on any error."""
    if not STATE_PATH or not Path(STATE_PATH).exists():
        return {}
    try:
        return json.loads(Path(STATE_PATH).read_text())
    except Exception:
        return {}


def _read_heartbeat() -> dict:
    """Read bot's heartbeat.json. Returns {} on any error."""
    if not STATE_PATH:
        return {}
    hb_path = Path(STATE_PATH).parent / "heartbeat.json"
    if not hb_path.exists():
        return {}
    try:
        return json.loads(hb_path.read_text())
    except Exception:
        return {}


def _read_watchdog_status() -> dict:
    try:
        return json.loads(Path("/tmp/reconcile_watchdog_status.json").read_text())
    except Exception:
        return {}


def _read_monitor_tick() -> dict:
    """Read monitor's last-tick file (written by monitor.py)."""
    try:
        return json.loads(Path("/tmp/monitor_tick.json").read_text())
    except Exception:
        return {}


def _age_seconds(ts_str: str) -> float:
    try:
        ts = datetime.fromisoformat(ts_str)
        return (datetime.now(ET) - ts).total_seconds()
    except Exception:
        return float("inf")


def _is_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE


def _maybe_rotate_logs() -> None:
    """Rotate event_queue, sent_log, handled_log daily at 23:00 ET.

    Moves each to ROTATE_DIR/{name}.{YYYY-MM-DD}.archive. Keeps current
    files empty/fresh. Idempotent (only rotates once per date).
    """
    global _last_rotation_date
    now = datetime.now(ET)
    if now.hour < ROTATE_HOUR_ET:
        return
    today_str = now.strftime("%Y-%m-%d")
    if _last_rotation_date == today_str:
        return  # already rotated today

    ROTATE_DIR.mkdir(exist_ok=True)
    for src in (EVENT_QUEUE, SENT_LOG, HANDLED_LOG):
        if not src.exists():
            continue
        try:
            size = src.stat().st_size
            if size == 0:
                continue  # nothing to archive
            dst = ROTATE_DIR / f"{src.name}.{today_str}.archive"
            # If file already exists, append timestamp
            if dst.exists():
                dst = ROTATE_DIR / f"{src.name}.{today_str}.{now.strftime('%H%M%S')}.archive"
            # Move + truncate (create empty)
            src.rename(dst)
            src.touch()
            _log(f"rotated {src.name} → {dst.name} ({size} bytes archived)")
        except Exception as exc:
            _log(f"rotation of {src.name} failed: {exc}")
    _last_rotation_date = today_str


def _build_heartbeat_message() -> str:
    """Build the 30-min status summary with PnL and trade summary."""
    now = datetime.now(ET)
    hb = _read_heartbeat()
    state = _read_state_json()
    wd = _read_watchdog_status()
    mon = _read_monitor_tick()

    bot_age = _age_seconds(hb.get("ts", ""))
    wd_age = _age_seconds(wd.get("ts", ""))
    mon_age = _age_seconds(mon.get("ts", ""))

    # Component staleness only matters during market hours.
    # Outside market hours, watchdog and bot may legitimately be quiet.
    in_market = _is_market_hours()
    bot_ok = (bot_age < BOT_HEARTBEAT_STALE_S) if in_market else True
    wd_ok = (wd_age < WATCHDOG_STALE_S) if in_market else True
    op = state.get("open_position")
    mon_ok = (mon_age < MONITOR_TICK_STALE_S) if (op and in_market) else True

    overall = "🟢" if (bot_ok and wd_ok and mon_ok) else ("🟡" if bot_ok else "🔴")

    def _fmt_age(s):
        if s == float("inf"): return "n/a"
        return f"{int(s)}s"

    lines = [f"{overall} {now.strftime('%H:%M')} ET status"]
    lines.append(f"Bot:      {'alive' if bot_ok else 'STALE'}  (hb {_fmt_age(bot_age)} ago)")
    lines.append(f"Watchdog: {wd.get('status', '?')}  ({_fmt_age(wd_age)} ago)")
    if op:
        lines.append(
            f"IBKR mon: {'streaming' if mon_ok else 'STALE'}  "
            f"(tick {_fmt_age(mon_age)} ago)"
        )
        ss = int(op.get("short_strike", 0))
        ls = int(op.get("long_strike", 0))
        cr = op.get("entry_credit", "?")
        sp = op.get("stop_price", "?")
        mark = mon.get("mark", "?")
        # Unrealized pnl if we have mark
        try:
            upl_pts = float(cr) - float(mark)
            upl_usd = upl_pts * 100 * int(op.get("quantity", 1))
            upl_str = f"  UPL=${upl_usd:+.0f}"
        except (ValueError, TypeError):
            upl_str = ""
        lines.append(f"Position: SPXW {ss}/{ls}P  credit=${cr}  stop=${sp}  mark=${mark}{upl_str}")
    else:
        lines.append("Position: none")

    # Today's trade summary (from bot heartbeat)
    trade_taken = state.get("trade_taken_today", False)
    total_trades = state.get("total_trades", 0)
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    total_pnl = state.get("total_pnl", 0)
    if trade_taken or total_trades > 0:
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        lines.append(
            f"Today:    {'traded ✓' if trade_taken else 'no trade'}  |  "
            f"total: {total_trades}t  {wins}W/{losses}L  ({win_rate:.0f}%)  P&L=${total_pnl:+.0f}"
        )
    return "\n".join(lines)


def _check_component_health() -> list[dict]:
    """Detect dead/stale components, return list of alert events to send.
    Each is a dict ready to pass to _send_with_backoff."""
    if not _is_market_hours():
        return []
    alerts = []
    hb = _read_heartbeat()
    wd = _read_watchdog_status()
    state = _read_state_json()
    mon = _read_monitor_tick()

    bot_age = _age_seconds(hb.get("ts", ""))
    if bot_age > BOT_HEARTBEAT_STALE_S:
        alerts.append({
            "id": f"comp_bot_stale_{int(time.time())//120}",  # 2-min bucket for dedup
            "subject": "BOT MAIN LOOP STALE",
            "body": f"Heartbeat last write {int(bot_age)}s ago (threshold {BOT_HEARTBEAT_STALE_S}s)",
            "severity": "critical",
        })

    wd_age = _age_seconds(wd.get("ts", ""))
    if wd_age > WATCHDOG_STALE_S:
        alerts.append({
            "id": f"comp_wd_stale_{int(time.time())//300}",
            "subject": "WATCHDOG STALE",
            "body": f"Watchdog status last update {int(wd_age)}s ago (threshold {WATCHDOG_STALE_S}s)",
            "severity": "critical",
        })

    if state.get("open_position"):
        mon_age = _age_seconds(mon.get("ts", ""))
        if mon_age > MONITOR_TICK_STALE_S:
            alerts.append({
                "id": f"comp_mon_stale_{int(time.time())//60}",
                "subject": "SL MONITOR STALE",
                "body": f"IBKR streaming tick last received {int(mon_age)}s ago (threshold {MONITOR_TICK_STALE_S}s) — SL may not react in real time",
                "severity": "critical",
            })
    return alerts


def main():
    """Run forever. Polls queue every POLL_INTERVAL_S."""
    global STATE_PATH
    try:
        import yaml
        cfg = yaml.safe_load(open("/Users/varmakammili/Documents/GitHub/StocksTradingTest/webull_bot/config.yaml"))
        sf = cfg.get("state_file", "")
        if sf:
            if not Path(sf).is_absolute():
                STATE_PATH = "/Users/varmakammili/Documents/GitHub/StocksTradingTest/" + sf
            else:
                STATE_PATH = sf
    except Exception as e:
        _log(f"could not load config (state_path will be None): {e}")

    if not _TOKEN or not _CHAT_ID:
        _log("CRITICAL: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — service exiting")
        return 1

    _log("started — polling event queue every 5s")
    sent_ids = _load_sent_ids()
    dedup = _load_dedup()
    last_heartbeat_ts = 0.0

    # Trap signals for clean exit
    stop = {"flag": False}
    def _on_signal(s, f):
        _log(f"signal {s} received, exiting cleanly")
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while not stop["flag"]:
        try:
            # 1. Drain event queue (priority: critical → warn → info)
            events = []
            if EVENT_QUEUE.exists():
                try:
                    for line in EVENT_QUEUE.open():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("id") in sent_ids:
                            continue
                        events.append(ev)
                except Exception as e:
                    _log(f"queue read error: {e}")

            # Sort by severity desc (critical first)
            events.sort(key=lambda e: {"critical": 2, "warn": 1, "info": 0}.get(e.get("severity", "info"), 0), reverse=True)

            for ev in events:
                if stop["flag"]:
                    break
                if _should_suppress_by_dedup(ev.get("dedup_key", ""), dedup):
                    _log(f"dedup-suppress {ev['id'][:8]} key={ev['dedup_key']}")
                    sent_ids.add(ev["id"])
                    _log_sent(ev["id"], -1, "", "suppressed by dedup window")
                    continue
                ok = _send_with_backoff(ev)
                sent_ids.add(ev["id"])
                if ok and ev.get("dedup_key"):
                    dedup[ev["dedup_key"]] = datetime.now(ET).isoformat()
                    _save_dedup(dedup)

            # 2. Component health checks (synthetic events)
            for synth in _check_component_health():
                key = synth["id"]  # use synthetic id as dedup
                if key in sent_ids:
                    continue
                # Make it look like a real event
                ev = {
                    "id": synth["id"],
                    "ts": datetime.now(ET).isoformat(),
                    "severity": synth["severity"],
                    "source": "telegram_service.health",
                    "subject": synth["subject"],
                    "body": synth["body"],
                    "dedup_key": synth["id"],
                }
                _send_with_backoff(ev)
                sent_ids.add(ev["id"])

            # 3. 30-min heartbeat during market hours
            now_ts = time.time()
            if _is_market_hours() and (now_ts - last_heartbeat_ts) >= HEARTBEAT_INTERVAL_S:
                text = _build_heartbeat_message()
                http, resp, err = _send_telegram(text)
                _log(f"heartbeat sent: http={http} err={err[:50]}")
                last_heartbeat_ts = now_ts

            # 4. Daily log rotation (after 23:00 ET, once per date)
            _maybe_rotate_logs()

        except Exception as e:
            _log(f"main loop error (continuing): {e}")

        # Sleep
        for _ in range(POLL_INTERVAL_S):
            if stop["flag"]:
                break
            time.sleep(1)

    _log("exited cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
