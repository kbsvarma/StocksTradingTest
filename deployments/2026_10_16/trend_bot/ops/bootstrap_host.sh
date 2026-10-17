#!/bin/bash
set -euo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
import sys
if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
    raise SystemExit(
        f"Python 3.11-3.13 required, got {sys.version.split()[0]}")
PY

if [[ -e "$BASE/.venv" ]]; then
  echo "refusing to replace existing $BASE/.venv; remove it deliberately first" >&2
  exit 64
fi

BUILD="$BASE/.venv.build.$$"
cleanup() { rm -rf "$BUILD"; }
trap cleanup EXIT
"$PYTHON_BIN" -m venv "$BUILD"
"$BUILD/bin/python" -m pip install --upgrade 'pip==25.1.1'
"$BUILD/bin/python" -m pip install --no-deps -r "$BASE/requirements.lock"
"$BUILD/bin/python" -m pip check
cd "$BASE"
"$BUILD/bin/python" - <<'PY'
import importlib.metadata as m
from pathlib import Path

base = Path.cwd()
pins = {}
for line in (base / "requirements.txt").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "==" in line:
        name, version = line.split("==", 1)
        pins[name.lower()] = version
for name, want in pins.items():
    got = m.version(name)
    if got != want:
        raise SystemExit(f"pin mismatch {name}: wanted {want}, got {got}")
print(f"validated {len(pins)} direct dependency pins")
PY
"$PYTHON_BIN" - "$BUILD" "$BASE/.venv" <<'PY'
import os
import sys

source, destination = sys.argv[1:]
try:
    os.rename(source, destination)
except OSError as exc:
    if os.path.exists(destination):
        raise SystemExit(
            f"refusing to replace concurrently-created {destination}") from exc
    raise
PY
trap - EXIT
echo "pinned runtime ready at $BASE/.venv"
