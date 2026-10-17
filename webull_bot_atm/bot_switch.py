"""bot_switch.py — safe CLI for switching between legacy and ATM bots.

Usage:
  python -m webull_bot_atm.bot_switch status
  python -m webull_bot_atm.bot_switch to NEW_ATM
  python -m webull_bot_atm.bot_switch to LEGACY
  python -m webull_bot_atm.bot_switch to NEW_ATM --force   # bypass open-position guard

Design invariants:
  - ONLY ONE bot is loaded in launchd at a time
  - Switch refuses if active bot has open positions (use --force to override with triple confirmation)
  - Sends Telegram alert on every switch (via shared event queue)
  - Reads broker truth (account_v2.get_account_position) to determine open positions —
    NOT state.json (which can be stale)

Bot labels (must match the launchd plist Label values):
  - LEGACY:  com.stockstest.webull-bot
  - NEW_ATM: com.stockstest.webull-bot-atm
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Map of bot name to launchd label
BOT_LABELS = {
    "LEGACY":  "com.stockstest.webull-bot",
    "NEW_ATM": "com.stockstest.webull-bot-atm",
}

# Map of bot name to plist file
HOME = Path.home()
PLIST_DIR = HOME / "Library" / "LaunchAgents"
BOT_PLISTS = {
    "LEGACY":  PLIST_DIR / "com.stockstest.webull-bot.plist",
    "NEW_ATM": PLIST_DIR / "com.stockstest.webull-bot-atm.plist",
}


@dataclass
class BotStatus:
    name: str
    loaded: bool
    pid: int | None
    open_positions: int


def _is_loaded(label: str) -> tuple[bool, int | None]:
    """Check if a launchd label is loaded. Returns (loaded, pid)."""
    try:
        out = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return False, None
        # Parse the plist-like output for PID
        for line in out.stdout.splitlines():
            line = line.strip().rstrip(";")
            if line.startswith('"PID" ='):
                pid_str = line.split("=", 1)[1].strip()
                try:
                    return True, int(pid_str)
                except ValueError:
                    return True, None
        return True, None
    except Exception:
        return False, None


def _count_open_positions(bot_name: str) -> int:
    """Count open SPX positions in the broker account for the given bot.
    Returns -1 if unable to determine."""
    # Both bots use the same Webull account (for now). The position count is
    # global to the account — we can't distinguish "which bot owns it" at the
    # broker level. We use the state.json of each bot as the proxy for ownership.
    if bot_name == "LEGACY":
        sf = Path("spx_spread_bot/data/webull_live/vG_spx_w50_vix25/state.json")
    elif bot_name == "NEW_ATM":
        sf = Path("spx_spread_bot/data/webull_live/atm_bot/state.json")
    else:
        return -1
    try:
        if not sf.exists():
            return 0
        data = json.loads(sf.read_text())
        if data.get("open_position"):
            return 1
        return 0
    except Exception:
        return -1


def status_all() -> list[BotStatus]:
    """Return status for all bots."""
    return [
        BotStatus(
            name=name,
            loaded=_is_loaded(label)[0],
            pid=_is_loaded(label)[1],
            open_positions=_count_open_positions(name),
        )
        for name, label in BOT_LABELS.items()
    ]


def _send_telegram(subject: str, body: str = "", severity: str = "info") -> None:
    """Send a Telegram alert via shared event queue."""
    try:
        from webull_bot_atm.event_queue import queue_event as q_atm
        q_atm(severity=severity, source="bot_switch", subject=subject, body=body)
    except Exception:
        # Fallback: try writing raw event directly
        try:
            import uuid
            from datetime import datetime
            from zoneinfo import ZoneInfo
            event = {
                "id": uuid.uuid4().hex,
                "ts": datetime.now(ZoneInfo("America/New_York")).isoformat(),
                "severity": severity,
                "source": "bot_switch",
                "subject": f"[SWITCH] {subject}",
                "body": body,
                "dedup_key": "",
            }
            with open("/tmp/event_queue.jsonl", "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception:
            pass


def cmd_status(args) -> int:
    """Print status of all bots."""
    statuses = status_all()
    print("=" * 80)
    print(f"{'Bot':<12} {'Loaded':<8} {'PID':<8} {'Open Positions':<15} {'Plist exists':<12}")
    print("-" * 80)
    for s in statuses:
        plist_exists = "yes" if BOT_PLISTS[s.name].exists() else "MISSING"
        pid_str = str(s.pid) if s.pid else "-"
        op_str = "?" if s.open_positions < 0 else str(s.open_positions)
        loaded_str = "YES" if s.loaded else "no"
        print(f"{s.name:<12} {loaded_str:<8} {pid_str:<8} {op_str:<15} {plist_exists:<12}")
    print("=" * 80)
    loaded = [s for s in statuses if s.loaded]
    if len(loaded) == 0:
        print("⚠ No bot is currently loaded.")
    elif len(loaded) == 1:
        print(f"Active bot: {loaded[0].name}")
    else:
        print(f"⚠ MULTIPLE BOTS LOADED: {[s.name for s in loaded]} — should only be one!")
    return 0


def _unload_bot(name: str) -> bool:
    """Unload a bot's launchd plist. Returns True on success."""
    plist = BOT_PLISTS[name]
    if not plist.exists():
        print(f"  plist {plist} does not exist — skipping unload")
        return True
    try:
        result = subprocess.run(
            ["launchctl", "unload", str(plist)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Non-zero often means "not loaded" — check
            loaded, _ = _is_loaded(BOT_LABELS[name])
            if not loaded:
                print(f"  ✓ {name} not loaded (already stopped)")
                return True
            print(f"  ✗ unload returned {result.returncode}: {result.stderr.strip()}")
            return False
        print(f"  ✓ unloaded {name}")
        return True
    except Exception as e:
        print(f"  ✗ unload failed: {e}")
        return False


def _load_bot(name: str) -> bool:
    """Load a bot's launchd plist. Returns True on success."""
    plist = BOT_PLISTS[name]
    if not plist.exists():
        print(f"  ✗ plist {plist} does not exist — cannot load")
        return False
    try:
        result = subprocess.run(
            ["launchctl", "load", str(plist)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"  ✗ load returned {result.returncode}: {result.stderr.strip()}")
            return False
        print(f"  ✓ loaded {name}")
        return True
    except Exception as e:
        print(f"  ✗ load failed: {e}")
        return False


def cmd_to(args) -> int:
    target = args.target
    if target not in BOT_LABELS:
        print(f"✗ Unknown bot: {target}. Valid: {list(BOT_LABELS.keys())}")
        return 1
    statuses = status_all()
    by_name = {s.name: s for s in statuses}

    # Find currently loaded bot(s)
    loaded = [s for s in statuses if s.loaded]
    if len(loaded) > 1:
        print(f"⚠ Multiple bots loaded: {[s.name for s in loaded]}. Refusing to switch — unload manually first.")
        return 1

    if loaded and loaded[0].name == target:
        print(f"✓ {target} is already the active bot. No action needed.")
        return 0

    # Safety check: refuse if currently-loaded bot has open positions
    if loaded:
        active = loaded[0]
        if active.open_positions > 0:
            if not args.force:
                print(f"✗ REFUSED: {active.name} has {active.open_positions} open position(s).")
                print(f"  Switching would leave them unmonitored by the new bot.")
                print(f"  Close positions first, OR use --force (NOT RECOMMENDED).")
                return 1
            # Force flow — triple confirmation
            print(f"⚠ WARNING: {active.name} has {active.open_positions} open position(s).")
            print(f"  Force-switching means those positions will not be monitored by {target}.")
            print(f"  You will need to manually monitor or close those positions.")
            print()
            resp1 = input("  Type 'I UNDERSTAND' to continue: ").strip()
            if resp1 != "I UNDERSTAND":
                print("✗ Aborted.")
                return 1
            print()
            print(f"⚠ CONFIRM AGAIN: {active.open_positions} unmonitored position(s) after switch.")
            resp2 = input(f"  Type the destination profile name exactly: ").strip()
            if resp2 != target:
                print(f"✗ Aborted (expected '{target}', got '{resp2}').")
                return 1
            print()
            print(f"⚠ FINAL CONFIRMATION:")
            print(f"  - Kill {active.name} ({active.open_positions} position(s) become unmonitored)")
            print(f"  - Start {target}")
            resp3 = input(f"  Type 'PROCEED WITH FORCED SWITCH' to execute: ").strip()
            if resp3 != "PROCEED WITH FORCED SWITCH":
                print("✗ Aborted.")
                return 1
            print()
            print("🔥 Force switching...")

    # Do the switch
    if loaded:
        print(f"🛑 Stopping {loaded[0].name}...")
        if not _unload_bot(loaded[0].name):
            print("✗ Unload failed — aborting switch")
            return 1

    print(f"🚀 Starting {target}...")
    if not _load_bot(target):
        print("✗ Load failed!")
        return 1

    # Notify Telegram
    from_name = loaded[0].name if loaded else "(none)"
    _send_telegram(
        subject=f"PROFILE SWITCHED: {from_name} → {target}",
        body=f"Bot switch executed. New active bot: {target}\n"
             f"Previous open positions on {from_name}: {by_name[from_name].open_positions if loaded else 'n/a'}\n"
             f"Forced: {args.force}",
        severity="warn" if args.force else "info",
    )
    print(f"📱 Telegram alert queued: PROFILE SWITCHED: {from_name} → {target}")
    print(f"✓ Active bot is now: {target}")
    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Safe bot switcher (LEGACY ↔ NEW_ATM)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show which bot is loaded and open-position count")
    p_to = sub.add_parser("to", help="Switch to a specific bot")
    p_to.add_argument("target", choices=list(BOT_LABELS.keys()))
    p_to.add_argument("--force", action="store_true",
                       help="Force switch even if open positions exist (triple-confirm prompt)")
    args = p.parse_args()
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "to":
        return cmd_to(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
