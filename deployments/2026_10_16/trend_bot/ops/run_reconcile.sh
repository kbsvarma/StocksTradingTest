#!/bin/bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$HOME/.webull_env" ]]; then
  set -a
  source "$HOME/.webull_env"
  set +a
fi
cd "$BASE"
PYTHON="$BASE/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "refusing to reconcile: pinned .venv missing" >&2
  exit 78
fi
exec "$PYTHON" reconcile.py
