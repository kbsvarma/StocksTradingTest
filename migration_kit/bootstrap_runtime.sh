#!/usr/bin/env bash
# Run on the DESTINATION after payload/home has been copied to the chosen home.
# Creates the exact captured Python environment. Does not start any service.
set -euo pipefail
STAGE=${1:?usage: bootstrap_runtime.sh STAGE [PYTHON_BIN]}
PYTHON_BIN=${2:-python3.12}
TARGET_HOME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target_home"])' "$STAGE/stage.json")
"$PYTHON_BIN" - "$STAGE/stage.json" <<'PY'
import json,platform,sys
m=json.load(open(sys.argv[1]));want=m['python'].split()[1].split('.')[:2]
if platform.system()!='Linux' or platform.machine()!=m['architecture']:
 raise SystemExit('Destination OS/architecture differs; this captured runtime requires deliberate revalidation.')
if list(map(str,sys.version_info[:2]))!=want:raise SystemExit('Use the captured Python major/minor version.')
PY
VENV="$TARGET_HOME/trendbot/trend_bot/.venv"
if [[ -e "$VENV" ]]; then echo "Refusing to overwrite existing $VENV" >&2; exit 1; fi
"$PYTHON_BIN" -m venv "$VENV"
# Keep an incomplete runtime visibly marked until checks pass.
touch "$VENV/MIGRATION_INCOMPLETE"
if [[ -d "$STAGE/wheels" ]]; then
 "$VENV/bin/python" -m pip install --no-index --find-links "$STAGE/wheels" --no-deps -r "$STAGE/runtime.lock"
else
 "$VENV/bin/python" -m pip install --no-deps -r "$STAGE/runtime.lock"
fi
"$VENV/bin/python" -m pip check
(cd "$TARGET_HOME/trendbot/trend_bot" && "$VENV/bin/python" -m pytest tests/ -q)
"$VENV/bin/python" - "$TARGET_HOME/trendbot/shadow_capture" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1])
for name,want in json.loads((p/'release.json').read_text()).items():
 if hashlib.sha256((p/name).read_bytes()).hexdigest()!=want:raise SystemExit('Shadow release mismatch: '+name)
print('Frozen shadow release verified; no gateway connections or services started.')
PY
rm "$VENV/MIGRATION_INCOMPLETE"
echo 'Runtime prepared. Services remain stopped; complete private configuration and cold cutover separately.'
