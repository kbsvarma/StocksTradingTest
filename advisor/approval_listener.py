"""Telegram approval listener — the Tier 1 human-in-the-loop gate.

Long-polls getUpdates for replies from the ONE authorized chat:

    YES AB12   → PENDING→APPROVED, spawn detached executor
    NO AB12    → PENDING→REJECTED
    STATUS     → list pending proposals

Anything from any other chat id is ignored (and logged). Runs forever under
launchd (com.stockstest.advisor-approvals, KeepAlive). Telegram-non-blocking
invariant holds: this service only *initiates* the executor; the trading path
itself never waits on Telegram.

Run: python -m advisor.approval_listener
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor import proposals as P
from advisor import telegram_io

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "advisor" / "logs"

CMD_RE = re.compile(r"^\s*(yes|no|approve|reject)\s+([a-z0-9]{4})\s*$", re.IGNORECASE)
MAX_MSG_AGE_S = 600   # ignore Telegram messages older than this — protects
                      # against replaying an old YES after offset-file loss
SWEEP_EVERY = 10      # poll cycles between expired-PENDING sweeps


def _log(msg: str) -> None:
    print(f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}", flush=True)


def _spawn_executor(pid: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"executor_{pid}_{datetime.now(ET).strftime('%Y%m%d_%H%M%S')}.log"
    with open(log_path, "ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "advisor.executor", "--proposal", pid],
            cwd=str(REPO_ROOT), stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,   # survives listener restarts
        )
    _log(f"spawned executor for {pid} → {log_path.name}")


def _handle_command(verb: str, pid: str) -> None:
    p = P.load(pid)
    if p is None:
        telegram_io.send(f"❓ unknown proposal {pid}")
        return
    if verb in ("yes", "approve"):
        if p.expired() and p.status == "PENDING":
            try:
                P.transition(pid, "EXPIRED", "expired before approval")
            except ValueError:
                pass
            telegram_io.send(f"⏳ {pid} already expired ({p.expires_ts[11:16]} ET) — "
                             f"ask for a fresh proposal if still valid")
            return
        try:
            P.transition(pid, "APPROVED", "user approved via Telegram")
        except ValueError as exc:
            telegram_io.send(f"⚠️ {pid}: {exc}")
            return
        telegram_io.send(f"✅ {pid} approved — executing now (rails re-check, "
                         f"then LIMIT walk-down)")
        _spawn_executor(pid)
    else:
        try:
            P.transition(pid, "REJECTED", "user rejected via Telegram")
            telegram_io.send(f"🚫 {pid} rejected — no order placed")
        except ValueError as exc:
            telegram_io.send(f"⚠️ {pid}: {exc}")


def _sweep_expired() -> None:
    """Transition expired PENDING proposals to EXPIRED (housekeeping —
    previously only happened lazily when a YES arrived too late)."""
    for p in P.load_all():
        if p.status == "PENDING" and p.expired():
            try:
                P.transition(p.id, "EXPIRED", "listener sweep")
                _log(f"swept expired proposal {p.id}")
            except ValueError:
                pass


def _handle_status() -> None:
    pend = [p for p in P.load_all() if p.status == "PENDING" and not p.expired()]
    if not pend:
        telegram_io.send("no pending proposals")
        return
    lines = ["PENDING PROPOSALS"]
    for p in pend:
        lines.append(f"[{p.id}] {p.symbol} {int(p.short_strike)}/{int(p.long_strike)}P "
                     f"qty={p.qty} limit={p.limit_price}  expires {p.expires_ts[11:16]} ET")
    telegram_io.send("\n".join(lines))


def run() -> None:
    if not telegram_io.enabled():
        print("FATAL: TELEGRAM_BOT_TOKEN/CHAT_ID not set", file=sys.stderr)
        sys.exit(1)
    me = telegram_io.authorized_chat_id()
    _log(f"approval listener up — authorized chat {me[:4]}…")
    cfg = P.load_advisor_cfg()
    timeout = int(cfg["approval"]["poll_timeout_seconds"])
    cycles = 0

    while True:
        try:
            cycles += 1
            if cycles % SWEEP_EVERY == 1:
                _sweep_expired()
            updates = telegram_io.get_updates(timeout=timeout)
            for u in updates:
                telegram_io.save_offset(u["update_id"])
                msg = u.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text = (msg.get("text") or "").strip()
                if chat_id != me:
                    _log(f"IGNORED message from unauthorized chat {chat_id!r}")
                    continue
                msg_age = time.time() - int(msg.get("date", 0) or 0)
                if msg_age > MAX_MSG_AGE_S:
                    _log(f"IGNORED stale message ({msg_age:.0f}s old): {text!r} "
                         f"— replay protection")
                    continue
                if text.lower() == "status":
                    _handle_status()
                    continue
                m = CMD_RE.match(text)
                if m:
                    _handle_command(m.group(1).lower(), m.group(2).upper())
                # non-command chatter: ignore silently
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            _log(f"poll error ({type(exc).__name__}: {exc}) — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    run()
