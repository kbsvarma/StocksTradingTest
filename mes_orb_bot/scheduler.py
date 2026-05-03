#!/usr/bin/env python3
"""
scheduler.py — Daily market-session automation for the MES ORB bot.

Runs continuously as a background daemon. Each trading day:
  1. Sleeps until BOT_START_TIME (default 09:15 ET)
  2. Polls port 7497 until TWS paper is accepting connections
  3. Launches main.py as a subprocess, tees stdout/stderr to a dated log
  4. Monitors the process; restarts up to MAX_CRASH_RESTARTS times if it
     dies before EOD (before SESSION_DONE_HOUR ET)
  5. After EOD, sleeps until the next valid NYSE trading day

Usage:
  python scheduler.py              # run forever (normal operation)
  python scheduler.py --once       # run for today only, then exit
  python scheduler.py --status     # print next run time and exit
  python scheduler.py --test-start # start bot immediately (skip sleep)

macOS LaunchAgent (auto-start on login):
  See the companion file: com.mesorb.scheduler.plist
"""

import argparse
import datetime as dt
import logging
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ── Configuration ──────────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")

BOT_START_TIME    = dt.time(8, 45)    # ET: IBC launches TWS at this time
TWS_PORT          = 4002              # IB Gateway paper port
TWS_HOST          = "127.0.0.1"
TWS_POLL_INTERVAL = 10               # seconds between TWS connection attempts
TWS_POLL_TIMEOUT  = 600              # seconds to wait for TWS before giving up (10 min)
BOT_DELAY_SECS    = 60               # seconds to wait after TWS ready before starting bot
SESSION_DONE_HOUR = 16               # ET hour after which we consider the session complete
MAX_CRASH_RESTARTS = 2               # restart bot up to N times per day if it crashes early

BOT_DIR  = Path(__file__).parent.resolve()
BOT_CMD  = [sys.executable, str(BOT_DIR / "main.py")]
LOG_DIR  = BOT_DIR / "logs"

# ── IBC (automated TWS launcher) ──────────────────────────────────────────────
# IBC logs into TWS automatically using credentials in config.ini.
# Download from https://github.com/IbcAlpha/IBC
# Set IBC_DIR to wherever you extracted IBC (e.g. ~/IBC).
# Set IBC_CONFIG to your config.ini path.
#
# IBC_ENABLED can be overridden by the MES_IBC_ENABLED environment variable.
# On Linux servers, ibgateway.service manages Gateway separately, so the
# systemd unit sets MES_IBC_ENABLED=false to skip IBC startup here.
_ibc_env = os.environ.get("MES_IBC_ENABLED", "true").strip().lower()
IBC_ENABLED = _ibc_env not in ("false", "0", "no")

IBC_DIR    = Path.home() / "IBC"
IBC_CONFIG = IBC_DIR / "config.ini"

# macOS uses gatewaystartmacos.sh; Linux uses gatewaystart.sh
if platform.system() == "Darwin":
    IBC_SCRIPT = IBC_DIR / "gatewaystartmacos.sh"
else:
    IBC_SCRIPT = IBC_DIR / "gatewaystart.sh"

# ── NYSE market holidays 2025–2027 ────────────────────────────────────────────
# Source: NYSE official calendar. Half-day sessions are NOT included (bot
# has HALT_ON_ABNORMAL_SESSION=True and will self-halt if needed).
_HOLIDAYS = {
    # 2025
    dt.date(2025, 1,  1),   # New Year's Day
    dt.date(2025, 1, 20),   # MLK Day
    dt.date(2025, 2, 17),   # Presidents Day
    dt.date(2025, 4, 18),   # Good Friday
    dt.date(2025, 5, 26),   # Memorial Day
    dt.date(2025, 6, 19),   # Juneteenth
    dt.date(2025, 7,  4),   # Independence Day
    dt.date(2025, 9,  1),   # Labor Day
    dt.date(2025, 11, 27),  # Thanksgiving
    dt.date(2025, 12, 25),  # Christmas
    # 2026
    dt.date(2026, 1,  1),   # New Year's Day
    dt.date(2026, 1, 19),   # MLK Day
    dt.date(2026, 2, 16),   # Presidents Day
    dt.date(2026, 4,  3),   # Good Friday
    dt.date(2026, 5, 25),   # Memorial Day
    dt.date(2026, 6, 19),   # Juneteenth
    dt.date(2026, 7,  3),   # Independence Day (observed)
    dt.date(2026, 9,  7),   # Labor Day
    dt.date(2026, 11, 26),  # Thanksgiving
    dt.date(2026, 12, 25),  # Christmas
    # 2027
    dt.date(2027, 1,  1),   # New Year's Day
    dt.date(2027, 1, 18),   # MLK Day
    dt.date(2027, 2, 15),   # Presidents Day
    dt.date(2027, 3, 26),   # Good Friday
    dt.date(2027, 5, 31),   # Memorial Day
    dt.date(2027, 6, 18),   # Juneteenth (observed)
    dt.date(2027, 7,  5),   # Independence Day (observed)
    dt.date(2027, 9,  6),   # Labor Day
    dt.date(2027, 11, 25),  # Thanksgiving
    dt.date(2027, 12, 24),  # Christmas (observed)
}


# ── Logging setup ──────────────────────────────────────────────────────────────

def _setup_scheduler_log() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger("scheduler")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
        # Console
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        log.addHandler(ch)
        # Rotating file
        fh = logging.FileHandler(LOG_DIR / "scheduler.log")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


# ── Trading calendar helpers ───────────────────────────────────────────────────

def is_trading_day(d: dt.date) -> bool:
    """True if NYSE is open on date d (weekday and not a listed holiday)."""
    return d.weekday() < 5 and d not in _HOLIDAYS


def next_trading_day(from_date: dt.date = None) -> dt.date:
    """Return the next trading day strictly after from_date (default: today ET)."""
    if from_date is None:
        from_date = dt.datetime.now(ET).date()
    d = from_date + dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def next_bot_start_dt(now_et: dt.datetime = None) -> dt.datetime:
    """
    Return the next datetime when the bot should start.
    - If today is a trading day AND it's before BOT_START_TIME → start today
    - Otherwise → start on the next trading day
    """
    if now_et is None:
        now_et = dt.datetime.now(ET)
    today = now_et.date()
    today_start = dt.datetime.combine(today, BOT_START_TIME, tzinfo=ET)

    if is_trading_day(today) and now_et < today_start:
        return today_start

    # Find next trading day
    nxt = next_trading_day(today)
    return dt.datetime.combine(nxt, BOT_START_TIME, tzinfo=ET)


# ── TWS readiness probe ────────────────────────────────────────────────────────

def tws_is_ready(host: str = TWS_HOST, port: int = TWS_PORT) -> bool:
    """Quick TCP connect test — True if TWS is accepting connections."""
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True
    except OSError:
        return False


def wait_for_tws(log: logging.Logger,
                 timeout: int = TWS_POLL_TIMEOUT,
                 interval: int = TWS_POLL_INTERVAL) -> bool:
    """
    Block until TWS is ready or timeout expires.
    Returns True if connected, False if timeout hit.
    """
    deadline = time.monotonic() + timeout
    log.info(f"Waiting for TWS on {TWS_HOST}:{TWS_PORT} (timeout={timeout}s)…")
    attempt = 0
    while time.monotonic() < deadline:
        if tws_is_ready():
            log.info(f"TWS ready after {attempt * interval}s")
            return True
        attempt += 1
        log.debug(f"TWS not ready (attempt {attempt}) — retry in {interval}s")
        time.sleep(interval)
    log.error(f"TWS did not become ready within {timeout}s — skipping today")
    return False


# ── IBC / TWS launcher ────────────────────────────────────────────────────────

def launch_tws_via_ibc(log: logging.Logger) -> Optional[subprocess.Popen]:
    """
    Start TWS via IBC so it logs in automatically.
    Returns the IBC subprocess (keep reference so it stays alive),
    or None if IBC is disabled or the script is missing.
    """
    if not IBC_ENABLED:
        log.info("IBC_ENABLED=False — assuming TWS is already running")
        return None

    if not IBC_SCRIPT.exists():
        log.warning(
            f"IBC script not found at {IBC_SCRIPT}. "
            "Falling back to manual TWS — set IBC_ENABLED=False to suppress this warning. "
            "See: https://github.com/IbcAlpha/IBC"
        )
        return None

    if not IBC_CONFIG.exists():
        log.error(f"IBC config not found at {IBC_CONFIG}. Cannot auto-login.")
        return None

    log.info(f"Launching TWS via IBC: {IBC_SCRIPT} {IBC_CONFIG}")
    try:
        ibc_log = LOG_DIR / f"ibc_{dt.datetime.now(ET).strftime('%Y-%m-%d')}.log"
        ibc_out = open(ibc_log, "w", buffering=1)
        proc = subprocess.Popen(
            ["/bin/bash", str(IBC_SCRIPT), str(IBC_CONFIG)],
            cwd=str(IBC_DIR),
            stdout=ibc_out,
            stderr=subprocess.STDOUT,
        )
        log.info(f"IBC started (PID={proc.pid}) | log → {ibc_log}")
        return proc
    except Exception as exc:
        log.error(f"Failed to start IBC: {exc}")
        return None


# ── Bot process management ────────────────────────────────────────────────────

def _dated_log_path() -> Path:
    """Returns a unique log path like logs/bot_2025-05-01.log"""
    LOG_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(ET).strftime("%Y-%m-%d")
    base = LOG_DIR / f"bot_{stamp}.log"
    # Avoid overwriting when restarting on the same day
    if not base.exists():
        return base
    for n in range(1, 10):
        candidate = LOG_DIR / f"bot_{stamp}_{n}.log"
        if not candidate.exists():
            return candidate
    return base   # last resort: overwrite


def run_bot_process(log: logging.Logger) -> int:
    """
    Launch main.py, tee its output to stdout and a dated log file.
    Blocks until the process exits.
    Returns the process exit code.
    """
    log_path = _dated_log_path()
    log.info(f"Starting bot → {' '.join(BOT_CMD)}")
    log.info(f"Bot log  → {log_path}")

    proc = None
    exit_code = -1
    try:
        with open(log_path, "w", buffering=1) as log_file:
            proc = subprocess.Popen(
                BOT_CMD,
                cwd=str(BOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            # Tee output to both terminal and log file in real time
            for line in proc.stdout:
                sys.stdout.write(line)
                log_file.write(line)
            proc.wait()
            exit_code = proc.returncode

    except KeyboardInterrupt:
        if proc and proc.poll() is None:
            log.info("KeyboardInterrupt — sending SIGTERM to bot…")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise

    except Exception as exc:
        log.error(f"Error running bot process: {exc}")
        if proc and proc.poll() is None:
            proc.kill()

    log.info(f"Bot process exited with code {exit_code}")
    return exit_code


# ── Daily run logic ────────────────────────────────────────────────────────────

def run_today(log: logging.Logger) -> None:
    """
    Execute the full bot session for today:
    1. Launch TWS via IBC (auto-login)
    2. Wait for TWS port to be ready
    3. Brief settle delay, then run bot
    4. Restart bot up to MAX_CRASH_RESTARTS times on early crashes
    5. Kill IBC/TWS cleanly after EOD
    """
    ibc_proc = launch_tws_via_ibc(log)

    if not wait_for_tws(log):
        log.error("Skipping today: TWS not available")
        if ibc_proc and ibc_proc.poll() is None:
            ibc_proc.terminate()
        return

    if BOT_DELAY_SECS > 0:
        log.info(f"TWS ready — waiting {BOT_DELAY_SECS}s for full initialisation before bot starts…")
        time.sleep(BOT_DELAY_SECS)

    restarts = 0
    while True:
        exit_code = run_bot_process(log)

        now_et = dt.datetime.now(ET)
        session_done = (now_et.hour >= SESSION_DONE_HOUR)

        if exit_code == 0 or session_done:
            # Clean exit or post-EOD crash — no restart needed
            if exit_code != 0 and session_done:
                log.warning(f"Bot exited with code {exit_code} after EOD — not restarting")
            else:
                log.info("Bot session completed normally")
            break

        # Pre-EOD non-zero exit: potential crash
        if restarts >= MAX_CRASH_RESTARTS:
            log.error(
                f"Bot crashed {restarts + 1} time(s) today (max={MAX_CRASH_RESTARTS}). "
                "No more restarts. Check logs."
            )
            break

        restarts += 1
        wait_secs = 30 * restarts   # back off: 30s, 60s…
        log.warning(
            f"Bot exited early with code {exit_code}. "
            f"Restart {restarts}/{MAX_CRASH_RESTARTS} in {wait_secs}s…"
        )
        time.sleep(wait_secs)

        # Re-check TWS is still up before restarting
        if not tws_is_ready():
            log.error("TWS connection lost before restart — aborting today")
            break

    # Shut down IBC / TWS after EOD
    if ibc_proc and ibc_proc.poll() is None:
        log.info("Sending SIGTERM to IBC/TWS process…")
        ibc_proc.terminate()
        try:
            ibc_proc.wait(timeout=30)
            log.info("IBC/TWS exited cleanly")
        except subprocess.TimeoutExpired:
            log.warning("IBC/TWS did not exit in 30s — killing")
            ibc_proc.kill()


# ── Main scheduler loop ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MES ORB bot daily scheduler")
    parser.add_argument("--once",       action="store_true",
                        help="Run for today only, then exit")
    parser.add_argument("--status",     action="store_true",
                        help="Print next scheduled run time and exit")
    parser.add_argument("--test-start", action="store_true",
                        help="Start the bot immediately (skip sleep to BOT_START_TIME)")
    args = parser.parse_args()

    log = _setup_scheduler_log()

    now_et = dt.datetime.now(ET)

    # ── --status mode ──────────────────────────────────────────────────────────
    if args.status:
        today = now_et.date()
        nxt = next_bot_start_dt(now_et)
        td  = is_trading_day(today)
        print(f"Current time   : {now_et.strftime('%Y-%m-%d %H:%M %Z')}")
        print(f"Today trading? : {'YES' if td else 'NO'}")
        print(f"Next bot start : {nxt.strftime('%Y-%m-%d %H:%M %Z')}")
        secs_until = max(0, (nxt - now_et).total_seconds())
        h, rem = divmod(int(secs_until), 3600)
        m, s   = divmod(rem, 60)
        print(f"Starts in      : {h}h {m}m {s}s")
        return

    # ── Graceful shutdown on SIGTERM ───────────────────────────────────────────
    _stop = False
    def _sigterm(sig, frame):
        nonlocal _stop
        log.info("SIGTERM received — scheduler will exit after current session")
        _stop = True
    signal.signal(signal.SIGTERM, _sigterm)

    log.info("=" * 60)
    log.info("MES ORB Scheduler starting")
    log.info(f"Bot directory : {BOT_DIR}")
    log.info(f"Bot command   : {' '.join(BOT_CMD)}")
    log.info(f"Start time    : {BOT_START_TIME.strftime('%H:%M')} ET")
    log.info(f"TWS port      : {TWS_PORT}")
    log.info("=" * 60)

    # ── Main loop ──────────────────────────────────────────────────────────────
    first_iteration = True
    while not _stop:
        now_et = dt.datetime.now(ET)

        if args.test_start and first_iteration:
            target_dt = now_et   # start immediately
            log.info("--test-start: skipping sleep, starting bot now")
        else:
            target_dt = next_bot_start_dt(now_et)
            sleep_secs = max(0, (target_dt - now_et).total_seconds())
            log.info(
                f"Next session  : {target_dt.strftime('%A %Y-%m-%d %H:%M %Z')} "
                f"(in {sleep_secs / 3600:.1f}h)"
            )
            if sleep_secs > 0:
                _sleep_until(target_dt, log)
                if _stop:
                    break

        first_iteration = False
        session_date = target_dt.date()

        # Double-check it's still a trading day (holiday may have been added)
        if not is_trading_day(session_date):
            log.info(f"{session_date} is not a trading day — skipping")
            if args.once:
                break
            continue

        log.info(f"{'=' * 60}")
        log.info(f"SESSION {session_date}  starting at "
                 f"{dt.datetime.now(ET).strftime('%H:%M:%S %Z')}")
        log.info(f"{'=' * 60}")

        run_today(log)

        log.info(f"SESSION {session_date} complete")

        if args.once:
            log.info("--once flag set — scheduler exiting after single session")
            break

    log.info("Scheduler stopped")


def _sleep_until(target: dt.datetime, log: logging.Logger) -> None:
    """
    Sleep in 60-second chunks until target datetime.
    Logs a wake-up message every 30 minutes.
    Allows keyboard interrupt to propagate cleanly.
    """
    last_log = time.monotonic()
    while True:
        now_et = dt.datetime.now(ET)
        remaining = (target - now_et).total_seconds()
        if remaining <= 0:
            return
        chunk = min(60.0, remaining)
        try:
            time.sleep(chunk)
        except KeyboardInterrupt:
            raise
        # Log progress every 30 minutes
        if time.monotonic() - last_log >= 1800:
            now_et = dt.datetime.now(ET)
            remaining2 = max(0, (target - now_et).total_seconds())
            h, rem = divmod(int(remaining2), 3600)
            m, _   = divmod(rem, 60)
            log.info(f"Sleeping… next session in {h}h {m}m "
                     f"({target.strftime('%a %H:%M %Z')})")
            last_log = time.monotonic()


if __name__ == "__main__":
    main()
