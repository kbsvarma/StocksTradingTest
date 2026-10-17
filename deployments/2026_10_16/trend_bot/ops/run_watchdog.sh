#!/bin/bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
# credentials for alert delivery — the watchdog is the LAST line of paging;
# without these every "bot is down" alert dead-letters forever (review
# 2026-07-18 P0-2: today's outage paged no one)
if [[ -f "$HOME/.webull_env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.webull_env"
fi
# watchdog is stdlib-only: prefer the pinned venv but NEVER refuse to run —
# a safety net must not require the artifact it guards (review P1-1)
PYTHON="$BASE/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  for cand in /opt/anaconda3/bin/python /usr/bin/python3 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      PYTHON="$cand"
      echo "watchdog: pinned .venv missing — falling back to $PYTHON" >&2
      break
    fi
  done
fi
cd "$BASE"
exec "$PYTHON" ops/watchdog.py
