#!/bin/bash
# Orchestrates the 8 stop-multiplier runs, max 3 concurrent, all with --resume
# (resume is a no-op on empty dirs, and crash-safe on partially-filled ones).
set -u
PY=/opt/anaconda3/bin/python3
HERE="$(cd "$(dirname "$0")" && pwd)"
BT="$(dirname "$HERE")"
RUNNER="$HERE/run_cache_only.py"
START=2020-01-01
END=2026-04-30
RUNS=(stop_100 stop_125 stop_150 stop_175/rerun_2020_2026 stop_200 stop_250 stop_300 stop_nosl)

run_one() {
  local rel="$1"
  local dir="$BT/$rel"
  echo "$(date '+%H:%M:%S') launching $rel"
  nohup "$PY" "$RUNNER" \
    --config "$dir/config_run.yaml" \
    --output-dir "$dir" \
    --start-date "$START" --end-date "$END" \
    --resume \
    >> "$dir/run_2026_07_03.out" 2>&1 &
  echo $! > "$dir/run_2026_07_03.pid"
}

active=0
for rel in "${RUNS[@]}"; do
  while [ "$(jobs -rp | wc -l)" -ge 3 ]; do sleep 10; done
  run_one "$rel"
  sleep 2
done
wait
echo "$(date '+%H:%M:%S') all runs finished"
for rel in "${RUNS[@]}"; do
  s="$BT/$rel/summary.json"
  if [ -f "$s" ]; then
    echo "== $rel: $($PY -c "import json;d=json.load(open('$s'));print(d['trades_taken'],d['total_pnl'],d['win_rate'])")"
  else
    echo "== $rel: NO SUMMARY (check $BT/$rel/run_2026_07_03.out)"
  fi
done
