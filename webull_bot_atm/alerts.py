"""Telegram push-alert helpers for the Webull bot.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment (loaded from
.webull_env). If either is missing, all alert calls become no-ops so the bot
still runs without alerting — alerts are best-effort, never a hard dependency.

Public surface:
    send_alert(text)            — generic message, MarkdownV2-safe escaping
    alert_order_event(kind, d)  — order placed/filled/rejected
    alert_stop_fired(...)       — SL triggered
    alert_bot_down(age_min)     — heartbeat alarm
    alert_force_entry(...)      — force-entry confirmation
"""
from __future__ import annotations

import os
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_API_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage" if _TOKEN else ""


def _enabled() -> bool:
    return bool(_TOKEN and _CHAT_ID)


# Telegram MarkdownV2 reserved chars that must be escaped
_MD_ESCAPE = re.compile(r"([_*\[\]()~`>#+=|{}.!\-])")


def _md(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return _MD_ESCAPE.sub(r"\\\1", text)


def _post_blocking(text: str, parse_mode: str, timeout: float) -> None:
    """Actual HTTP POST. Swallows all exceptions. Runs on a background thread."""
    try:
        data = urllib.parse.urlencode({
            "chat_id":    _CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(_API_URL, data=data)
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except Exception:
        pass


def send_alert(
    text: str, *, markdown: bool = False, html: bool = False, timeout: float = 30.0,
) -> bool:
    """Fire-and-forget Telegram message.

    2026-05-22 rewrite: now routes through the event queue → telegram service.
    Returns True if successfully appended to queue. Telegram delivery is
    guaranteed-attempted by the service (with logged success/failure) — no
    more silent send failures.

    Legacy direct-send path retained ONLY if event_queue import fails (shouldn't
    happen in production). Kept for absolute compatibility.
    """
    if not text:
        return False
    # Strip MarkdownV2 escape backslashes — queue path uses plain text by default
    # (telegram_service applies its own emoji prefix)
    try:
        from webull_bot_atm.event_queue import queue_event
        # Detect severity from leading emoji (heuristic — preserves urgency hints)
        sev = "info"
        if any(x in text[:40] for x in ("🚨", "CRITICAL", "ORPHAN", "STOP LOSS", "STOP_LOSS", "TRIGGER")):
            sev = "critical"
        elif any(x in text[:40] for x in ("⚠️", "WARN", "DEGRAD", "STALE")):
            sev = "warn"
        # First line as subject, rest as body
        lines = text.split("\n", 1)
        subject = lines[0][:200]
        body = lines[1] if len(lines) > 1 else ""
        queue_event(severity=sev, source="alerts.send_alert", subject=subject, body=body)
        return True
    except Exception:
        # Fallback to legacy direct send (best-effort)
        if not _enabled():
            return False
        parse_mode = "HTML" if html else ("MarkdownV2" if markdown else "")
        try:
            t = threading.Thread(
                target=_post_blocking,
                args=(text, parse_mode, timeout),
                daemon=True,
            )
            t.start()
            return True
        except Exception:
            return False


# ── Domain-specific alerts ───────────────────────────────────────────────────

def _now_et() -> str:
    return datetime.now(ET).strftime("%H:%M:%S ET")


def _fmt_evt(event_ts: Optional[datetime], kind: str = "") -> str:
    """Format a timestamp footer for alerts.

    With event_ts: returns `(evt HH:MM:SS → snt HH:MM:SS  Δ Ns ET)` so the
    user can see how long elapsed between the underlying event being detected
    by the bot and the alert being dispatched. Also emits a stderr line
    `[alert-latency] kind=X evt=... snt=... delta_s=N` for historical analysis.

    Without event_ts: same as `(_now_et())`.
    """
    sent = datetime.now(ET)
    if event_ts is None:
        return f"({sent.strftime('%H:%M:%S ET')})"
    if event_ts.tzinfo is None:
        event_ts = event_ts.replace(tzinfo=ET)
    delta_s = (sent - event_ts).total_seconds()
    try:
        import sys
        sys.stderr.write(
            f"[alert-latency] kind={kind or 'unknown'} "
            f"evt={event_ts.strftime('%H:%M:%S')} snt={sent.strftime('%H:%M:%S')} "
            f"delta_s={delta_s:.1f}\n"
        )
        sys.stderr.flush()
    except Exception:
        pass
    return (
        f"(evt {event_ts.strftime('%H:%M:%S')} → snt {sent.strftime('%H:%M:%S')}"
        f"  Δ {delta_s:.0f}s ET)"
    )


def _safe(fn):
    """Decorator — wrap an alert helper so it can never raise into the bot."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return False
    wrapper.__name__ = fn.__name__
    return wrapper


@_safe
def alert_order_event(kind: str, details: dict, event_ts: Optional[datetime] = None) -> bool:
    """kind: PLACED, FILLED, REJECTED, CANCELLED, TIMEOUT.

    event_ts: optional moment the underlying event was detected (e.g. when
    the broker confirmed a fill). When provided, alert footer shows the
    bot→alert latency so we can debug perceived delivery delay.
    """
    icon = {
        "PLACED":    "📤",
        "FILLED":    "🟢",
        "REJECTED":  "🔴",
        "CANCELLED": "⚪",
        "TIMEOUT":   "⏱",
    }.get(kind, "ℹ️")
    spread = details.get("spread") or (
        f"{int(details['short_strike'])}/{int(details['long_strike'])}P"
        if details.get("short_strike") else "?"
    )
    qty   = details.get("qty", "?")
    price = details.get("fill_price") or details.get("limit_price")
    price_s = f"  @ {price:.2f}" if isinstance(price, (int, float)) else ""
    return send_alert(f"{icon}  {kind}  {spread}  qty={qty}{price_s}  {_fmt_evt(event_ts, kind)}")


@_safe
def alert_entry(
    spread: str, qty: int, credit: float, width: int,
    spx: float, vix: float, stop_price: float,
) -> bool:
    """Fired by the daily scheduler when a BPS is placed (not force-entry)."""
    msg = (
        f"🟢  ENTRY  {spread}\n"
        f"qty={qty}  credit=${credit:.2f}  width={width}pt\n"
        f"SPX={spx:,.0f}  VIX={vix:.1f}\n"
        f"stop @ ${stop_price:.2f}  (2×)\n"
        f"{_now_et()}"
    )
    return send_alert(msg)


@_safe
def alert_stop_fired(spread: str, mark: float, stop: float, filled: bool,
                     event_ts: Optional[datetime] = None) -> bool:
    """event_ts: the moment SL was triggered (mark crossed stop), not the
    moment the close confirmed. Shows the trigger→alert latency so user
    knows whether the close itself was slow."""
    state = "FILLED ✓" if filled else "NOT CONFIRMED ⚠️"
    msg = (
        f"🛑  STOP LOSS triggered  {spread}\n"
        f"mark={mark:.2f}  stop={stop:.2f}\n"
        f"close: {state}  {_fmt_evt(event_ts, 'SL_FIRED')}"
    )
    return send_alert(msg)


@_safe
def alert_close_failed_retry(spread: str, attempt: int, error: str,
                              mark: float, stop: float, next_retry_sec: int,
                              event_ts: Optional[datetime] = None) -> bool:
    """Fires on EVERY failed close attempt during stop-loss retry loop.

    Per memory/sl_close_failure_handling.md: bot must NOT abandon position
    on close failure. User needs to know each attempt failed so they can
    intervene manually if needed.
    """
    msg = (
        f"⚠️  STOP-LOSS CLOSE FAILED — attempt {attempt}\n"
        f"Spread: {spread}\n"
        f"Mark: {mark:.2f}  Stop: {stop:.2f}\n"
        f"Reason: {error[:200]}\n"
        f"POSITION STILL OPEN — bot will retry in {next_retry_sec}s.\n"
        f"{_fmt_evt(event_ts, 'CLOSE_FAIL_RETRY')}"
    )
    return send_alert(msg)


@_safe
def alert_close_failed_eod(spread: str, attempts: int, last_error: str,
                            mark: float, stop: float) -> bool:
    """Fires when EOD reached with stop-loss close still failing.

    This is the loudest alert — user MUST manually close in the Webull
    app or position will run to expiry uncontrolled.
    """
    msg = (
        f"🚨🚨 MANUAL ACTION REQUIRED 🚨🚨\n"
        f"STOP-LOSS CLOSE FAILED {attempts}× — EOD REACHED.\n"
        f"Spread: {spread}\n"
        f"Mark: {mark:.2f}  Stop: {stop:.2f}\n"
        f"Last error: {last_error[:200]}\n"
        f"POSITION STILL OPEN AT WEBULL.\n"
        f"GO TO THE APP AND CLOSE IT NOW or it expires uncontrolled.\n"
        f"({_now_et()})"
    )
    return send_alert(msg)


@_safe
def alert_close_resolved_externally(spread: str, attempts: int,
                                     event_ts: Optional[datetime] = None) -> bool:
    """Fires when bot detects position closed externally during retry loop
    (user manually closed in app, or order from a previous attempt eventually
    filled). Bot stops retrying."""
    msg = (
        f"✅  Position closed externally — bot stops retrying close.\n"
        f"Spread: {spread}\n"
        f"Attempts before detection: {attempts}\n"
        f"{_fmt_evt(event_ts, 'CLOSE_EXTERNAL')}"
    )
    return send_alert(msg)


@_safe
def alert_position_closed(
    spread: str, reason: str, entry_credit: float, exit_price: float,
    pnl_usd: float, wins: int, losses: int, total_pnl: float,
    event_ts: Optional[datetime] = None,
) -> bool:
    """Fires for every exit path: STOP_LOSS, EXPIRED, EOD."""
    icon = {
        "EXPIRED":   "✅",
        "STOP_LOSS": "🛑",
        "EOD":       "🔔",
    }.get(reason, "ℹ️")
    pnl_sign = "+" if pnl_usd >= 0 else ""
    total_sign = "+" if total_pnl >= 0 else ""
    msg = (
        f"{icon}  CLOSED  {spread}  [{reason}]\n"
        f"entry=${entry_credit:.2f}  exit=${exit_price:.2f}\n"
        f"P&L: {pnl_sign}${pnl_usd:.0f}\n"
        f"record: {wins}W {losses}L  ytd: {total_sign}${total_pnl:.0f}\n"
        f"{_fmt_evt(event_ts, f'CLOSED_{reason}')}"
    )
    return send_alert(msg)


@_safe
def alert_bot_down(age_min: float, last_hb: str = "") -> bool:
    msg = (
        f"⚠️  WEBULL BOT HEARTBEAT STALE\n"
        f"last heartbeat: {int(age_min)} min ago\n"
        f"last ts: {last_hb or 'n/a'}\n"
        f"check the launchd / logs immediately."
    )
    return send_alert(msg)


@_safe
def alert_force_entry(spread: str, qty: int, fill_price: float, filled: bool,
                      event_ts: Optional[datetime] = None) -> bool:
    icon = "🟢" if filled else "🔴"
    state = "FILLED" if filled else "NOT FILLED"
    msg = (
        f"{icon}  FORCE ENTRY  {state}\n"
        f"{spread}  qty={qty}  @ {fill_price:.2f}\n"
        f"{_fmt_evt(event_ts, 'FORCE_ENTRY')}"
    )
    return send_alert(msg)


@_safe
def alert_force_report(
    spx: float, spx_pct_from_open: float,
    vix: float, vix_delta_from_open: float, vix_in_zone: bool,
    spreads: list, source: str, min_credit: float = 1.20,
    spx_pct_from_prev_close: float | None = None,
    symbol_label: str = "SPX",
    title: str = "FORCE ENTRY REPORT",
) -> bool:
    """Push a force-entry report to Telegram as a monospace HTML <pre> block.

    `spreads` is a list of SpreadQuote-like objects with
    .short_strike, .long_strike, .mid, .bid, .ask attributes.

    `spx_pct_from_prev_close` is optional. When provided, the report shows
    both intraday move (from today's open) AND total move (from prev close).

    `symbol_label` is the underlying ticker label rendered in the message
    (e.g. "SPX" or "NDX"). The argument names still say `spx` for
    backward-compatibility but values can be any underlying.

    `title` is the bold header line shown above the table.
    """
    import html as _html
    zone_str = "✓ in zone (12-25)" if vix_in_zone else "✗ outside zone"

    sym = symbol_label
    pad = " " * max(0, 3 - len(sym))   # keep alignment when label is 3 chars vs SPX (3) vs NDX (3)

    if spx_pct_from_prev_close is not None:
        sym_line = (f"{sym}{pad} : {spx:,.2f}   {spx_pct_from_open:+.2f}% from open"
                    f"   {spx_pct_from_prev_close:+.2f}% from prev close")
    else:
        sym_line = f"{sym}{pad} : {spx:,.2f}   {spx_pct_from_open:+.2f}% from open"

    lines = [
        sym_line,
        f"VIX{' ':<{max(0,len(sym)-3)}} : {vix:.2f}     {zone_str}   {vix_delta_from_open:+.2f} from open",
        " #   Strikes       OTM%    Mid    Bid    Ask",
    ]
    best_mid = 0.0
    if spreads:
        for i, s in enumerate(spreads, 1):
            otm = (spx - s.short_strike) / spx * 100
            lines.append(
                f" {i}   {int(s.short_strike)}/{int(s.long_strike)}P    "
                f"{otm:.1f}%   {s.mid:.2f}   {s.bid:.2f}   {s.ask:.2f}"
            )
            if s.mid > best_mid:
                best_mid = s.mid
        if best_mid < min_credit:
            lines.append(
                f" ⚠   credits thin (best {best_mid:.2f} vs min {min_credit:.2f}) — not worth the risk."
            )
    else:
        lines.append(" (no 0DTE chain available)")
    lines.append(f" data: {source}")

    body = _html.escape("\n".join(lines))
    msg = f"📋  <b>{_html.escape(title)}</b>\n<pre>{body}</pre>"
    return send_alert(msg, html=True)


@_safe
def alert_close_all(spreads: list[dict]) -> bool:
    if not spreads:
        return send_alert(f"ℹ️  CLOSE-ALL invoked — no positions to close.  ({_now_et()})")
    lines = ["🛎  CLOSE-ALL invoked"]
    for s in spreads:
        st = s.get("fill_status", "?")
        fp = s.get("fill_price")
        fp_s = f" @ {fp:.2f}" if isinstance(fp, (int, float)) else ""
        lines.append(f"  {s.get('spread','?')}  qty={s.get('qty','?')}  [{st}]{fp_s}")
    lines.append(f"({_now_et()})")
    return send_alert("\n".join(lines))
