#!/bin/bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
PYTHON="$PWD/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "refusing dashboard start: pinned .venv missing" >&2
  exit 78
fi
exec "$PYTHON" -m streamlit run dashboard.py \
  --server.port 8503 --server.headless true
