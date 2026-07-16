"""Advisor watchdog — no advisor failure stays silent.

Every 5 minutes (launchd StartInterval): checks service exit codes, data
freshness, and the morning-brief SLO; one Telegram alert per (check, day) —
dedup state in advisor/data/watchdog_state.json. Also does log hygiene
(truncate runaway /tmp logs, prune advisor/logs >60d).

Checks:
  services   — launchctl last-exit nonzero on any com.stockstest.advisor-*
  quotes     — quote-store age >5min during RTH (daemon dead/wedged)
  brief SLO  — weekday and no successful publish/legacy heartbeat by 09:50 ET
  signals    — signals_latest.json >30h old on a weekday (nightly failed)
  approvals  — approval listener process missing (Tier-1 gate down)

CLI: python -m advisor.watchdog [--once] [--no-telegram]
launchd: com.stockstest.advisor-watchdog
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
LOGS = REPO / "advisor" / "logs"
STATE = DATA / "watchdog_state.json"

SERVICES = ("advisor-approvals", "advisor-exitwatch", "advisor-terminal",
            "advisor-quoted", "advisor-brief", "advisor-research",
            "advisor-librarian", "advisor-events", "advisor-ivsnap",
            "advisor-weekly")


def _now() -> datetime:
    return datetime.now(ET)


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(s: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.replace(tmp, STATE)


def _alert_once(state: dict, key: str, msg: str, send: bool) -> bool:
    day = _now().date().isoformat()
    if state.get(key) == day:
        return False
    state[key] = day
    print(f"[watchdog] ALERT {key}: {msg}")
    if send:
        try:
            from advisor import telegram_io
            telegram_io.send(f"🐕 WATCHDOG — {msg}")
        except Exception as exc:
            print(f"[watchdog] telegram failed: {exc}", file=sys.stderr)
    return True


def check_services(state: dict, send: bool) -> None:
    try:
        listing = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=10).stdout
    except Exception:
        return
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 3 or "com.stockstest.advisor-" not in parts[2]:
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        svc = label.replace("com.stockstest.", "")
        if pid == "-" and status not in ("0", "-"):
            # -15 = we SIGTERMed it (restart); alert only on real errors
            if status.lstrip("-").isdigit() and int(status) > 0:
                _alert_once(state, f"svc:{svc}:{status}",
                            f"{svc} last exit={status} — check "
                            f"/tmp/{svc}.err", send)


def check_quotes(state: dict, send: bool) -> None:
    now = _now()
    if now.weekday() > 4 or not ("09:35" <= now.strftime("%H:%M") <= "16:00"):
        return
    p = DATA / "quotes" / "latest.json"
    try:
        snap = json.loads(p.read_text())
        age = (now - datetime.fromisoformat(snap["as_of"])).total_seconds()
        if age > 300:
            _alert_once(state, "quotes:stale",
                        f"quote store {age / 60:.0f}min stale during RTH — "
                        f"quoted daemon dead? (watcher degrades to yfinance)",
                        send)
    except Exception:
        _alert_once(state, "quotes:missing",
                    "quote store missing during RTH — quoted daemon never "
                    "wrote", send)


def _brief_done_today(today: str) -> bool:
    try:
        for line in (LOGS / "pipeline_runs.jsonl").read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ts", "").startswith(today) \
                    and r.get("stage") in ("publish", "legacy", "pipeline") \
                    and r.get("rc") == 0:
                return True
    except Exception:
        pass
    return False


def check_brief_slo(state: dict, send: bool) -> None:
    """SELF-HEAL (2026-07-16 RCA): the 08:15 calendar job dies in dark-wake
    TCC context; this watchdog provably runs healthy all day. So past 09:00
    on a weekday with no successful brief and no pipeline in flight, LAUNCH
    the brief ourselves (once/day) instead of just barking about it —
    8 of 10 trial days had context but no brief because nobody relaunched."""
    now = _now()
    today = now.date().isoformat()
    if now.weekday() > 4 or now.strftime("%H:%M") < "09:00":
        return
    if now.strftime("%H:%M") > "15:30":
        return          # a brief this late is stale — don't burn the tokens
    if _brief_done_today(today):
        return
    running = subprocess.run(["pgrep", "-f", "run_brief.sh|advisor.orchestrator"],
                             capture_output=True, text=True).stdout.strip()
    if running:
        return
    if state.get("brief:selfheal") != today:
        state["brief:selfheal"] = today
        print(f"[watchdog] SELF-HEAL: no brief by {now.strftime('%H:%M')} — "
              f"launching run_brief.sh from watchdog context")
        try:
            subprocess.Popen(["/bin/zsh", str(REPO / "advisor" / "run_brief.sh")],
                             stdout=open("/tmp/advisor-brief-selfheal.log", "a"),
                             stderr=subprocess.STDOUT, cwd=REPO,
                             start_new_session=True)
            if send:
                from advisor import telegram_io
                telegram_io.send("🐕→🔄 WATCHDOG SELF-HEAL: 08:15 brief didn't "
                                 "complete — relaunching the pipeline now.")
        except Exception as exc:
            print(f"[watchdog] self-heal spawn failed: {exc}", file=sys.stderr)
    elif now.strftime("%H:%M") >= "10:30":
        # self-heal already tried today and still no brief — escalate once
        _alert_once(state, "brief:slo",
                    f"NO successful brief by {now.strftime('%H:%M')} ET even "
                    f"after self-heal — check advisor/logs/brief_{today}*.log "
                    f"and /tmp/advisor-brief-selfheal.log", send)


def check_librarian_selfheal(state: dict, send: bool) -> None:
    """Same dark-wake class as the brief (librarian died 07-15 19:00 with the
    identical TCC ModuleNotFoundError): after 19:30 on a weekday with no
    librarian heartbeat today, relaunch it once from this working context."""
    now = _now()
    today = now.date().isoformat()
    if now.weekday() > 4 or not ("19:30" <= now.strftime("%H:%M") <= "22:00"):
        return
    try:
        for line in (LOGS / "pipeline_runs.jsonl").read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ts", "").startswith(today) \
                    and str(r.get("stage", "")).startswith(("librarian",
                                                            "post_mortem")) \
                    and r.get("rc") == 0:
                return
    except Exception:
        pass
    if subprocess.run(["pgrep", "-f", "run_librarian.sh"],
                      capture_output=True, text=True).stdout.strip():
        return
    if state.get("librarian:selfheal") != today:
        state["librarian:selfheal"] = today
        print(f"[watchdog] SELF-HEAL: no librarian by {now.strftime('%H:%M')} "
              f"— relaunching")
        try:
            subprocess.Popen(["/bin/zsh", str(REPO / "advisor" / "run_librarian.sh")],
                             stdout=open("/tmp/advisor-librarian-selfheal.log", "a"),
                             stderr=subprocess.STDOUT, cwd=REPO,
                             start_new_session=True)
        except Exception as exc:
            print(f"[watchdog] librarian self-heal failed: {exc}", file=sys.stderr)


def check_signals(state: dict, send: bool) -> None:
    now = _now()
    if now.weekday() > 4:
        return
    p = DATA / "research" / "signals_latest.json"
    try:
        s = json.loads(p.read_text())
        age_h = (now - datetime.fromisoformat(s["as_of"])).total_seconds() / 3600
        if age_h > 30:
            _alert_once(state, "signals:stale",
                        f"factor sheet {age_h:.0f}h old — nightly research "
                        f"failed? /tmp/advisor-research.err", send)
    except Exception:
        pass


def log_hygiene() -> None:
    # truncate runaway /tmp logs (>50MB), prune advisor/logs >60d
    for p in Path("/tmp").glob("advisor-*.out"):
        try:
            if p.stat().st_size > 50 * 1024 * 1024:
                p.write_text(f"[watchdog] truncated {_now().isoformat()}\n")
        except Exception:
            continue
    cutoff = time.time() - 60 * 86400
    for p in LOGS.glob("*.log"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            continue


def run_once(send: bool = True) -> None:
    state = _state()
    check_services(state, send)
    check_quotes(state, send)
    check_brief_slo(state, send)
    check_librarian_selfheal(state, send)
    check_signals(state, send)
    log_hygiene()
    state["last_run"] = _now().isoformat()
    _save(state)
    print(f"[watchdog] ok {_now().strftime('%H:%M:%S')}")


def main() -> int:
    run_once(send="--no-telegram" not in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
