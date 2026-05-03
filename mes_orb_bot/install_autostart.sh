#!/usr/bin/env bash
# install_autostart.sh — One-command automation setup for the MES ORB bot.
#
# What this does:
#   1. Creates the logs directory
#   2. Installs the LaunchAgent plist so the scheduler starts at login
#   3. Loads it immediately (no reboot needed)
#   4. Prints status and next scheduled run time
#
# Usage:
#   chmod +x install_autostart.sh
#   ./install_autostart.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.mesorb.scheduler.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.mesorb.scheduler.plist"
LAUNCH_LABEL="com.mesorb.scheduler"
LOGS_DIR="$SCRIPT_DIR/logs"
PYTHON="/opt/anaconda3/bin/python"

echo ""
echo "======================================================"
echo "  MES ORB Bot — Automation Installer"
echo "======================================================"

# ── Verify prerequisites ────────────────────────────────────────────────────

if [[ ! -f "$PLIST_SRC" ]]; then
    echo "ERROR: plist not found at $PLIST_SRC"
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python not found at $PYTHON"
    echo "Update PYTHON path in this script to match your installation."
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/scheduler.py" ]]; then
    echo "ERROR: scheduler.py not found in $SCRIPT_DIR"
    exit 1
fi

# ── Create logs directory ────────────────────────────────────────────────────

mkdir -p "$LOGS_DIR"
echo "✓ Logs directory: $LOGS_DIR"

# ── Install LaunchAgent ──────────────────────────────────────────────────────

mkdir -p "$HOME/Library/LaunchAgents"

# Unload existing if present (ignore error if not loaded)
launchctl unload "$PLIST_DST" 2>/dev/null || true

cp "$PLIST_SRC" "$PLIST_DST"
echo "✓ Installed plist: $PLIST_DST"

launchctl load "$PLIST_DST"
echo "✓ LaunchAgent loaded and running"

# ── Verify it loaded ─────────────────────────────────────────────────────────

sleep 1
if launchctl list | grep -q "$LAUNCH_LABEL"; then
    PID=$(launchctl list | grep "$LAUNCH_LABEL" | awk '{print $1}')
    echo "✓ Scheduler running (PID: ${PID:-starting})"
else
    echo "⚠ Scheduler may not have started — check:"
    echo "  launchctl list | grep mesorb"
    echo "  cat $LOGS_DIR/scheduler_stderr.log"
fi

# ── Show next scheduled run ──────────────────────────────────────────────────

echo ""
echo "──────────────────────────────────────────────────────"
"$PYTHON" "$SCRIPT_DIR/scheduler.py" --status
echo ""

echo "======================================================"
echo "  Setup complete. The scheduler now:"
echo "    • Starts automatically at login"
echo "    • Restarts automatically if it crashes"
echo "    • Launches the bot each trading day at 09:15 ET"
echo ""
echo "  Useful commands:"
echo "    Status  : launchctl list | grep mesorb"
echo "    Next run: python scheduler.py --status"
echo "    Stop    : launchctl unload ~/Library/LaunchAgents/com.mesorb.scheduler.plist"
echo "    Logs    : tail -f $LOGS_DIR/scheduler.log"
echo "    Bot log : ls -lt $LOGS_DIR/bot_*.log | head -3"
echo "======================================================"
echo ""

echo "IMPORTANT — TWS must also be set to auto-start:"
echo "  1. Open TWS → Edit → Global Configuration"
echo "  2. Lock and Exit → set 'Auto restart' = YES"
echo "  3. Add TWS to macOS Login Items:"
echo "     System Settings → General → Login Items → add Trader Workstation"
echo "  4. In TWS login screen: check 'Remember username and password'"
echo ""
