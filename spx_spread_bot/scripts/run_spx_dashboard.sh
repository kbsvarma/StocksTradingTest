#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STREAMLIT_BIN="${ROOT}/.venv/bin/streamlit"
exec "$STREAMLIT_BIN" run "$ROOT/dashboard.py" --server.address 127.0.0.1 --server.port 8503 --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false
