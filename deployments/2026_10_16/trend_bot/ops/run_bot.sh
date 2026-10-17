#!/bin/bash
# trend_bot launcher — sources telegram creds, runs the hourly loop
set -euo pipefail
if [[ -f "$HOME/.webull_env" ]]; then
  set -a
  source "$HOME/.webull_env"
  set +a
fi
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
PYTHON="$PWD/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "refusing to start: pinned .venv missing (run ops/bootstrap_host.sh)" >&2
  exit 78
fi
# caffeinate -i: hold an idle-sleep assertion while the bot runs — the
# 2026-07-21 outage began with macOS 'Idle Sleep' on battery (pmset log
# 17:10:10 ET) severing both gateways' upstream sessions. Lid-close and
# forced sleep still sleep the host; this only stops IDLE sleep.
exec /usr/bin/caffeinate -i "$PYTHON" bot.py
