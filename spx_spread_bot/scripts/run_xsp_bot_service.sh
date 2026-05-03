#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT}/.venv/bin/python"
exec "$PYTHON_BIN" "$ROOT/main.py" "$ROOT/config_xsp.yaml"
