#!/usr/bin/env bash
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${ADVISOR_PYTHON:-"$REPO/.venv/bin/python"}
[ -x "$PY" ] || PY=$(command -v python3)
cd "$REPO"
export PYTHONPATH="$REPO"
exec "$PY" -m advisor.intelligence.worker
