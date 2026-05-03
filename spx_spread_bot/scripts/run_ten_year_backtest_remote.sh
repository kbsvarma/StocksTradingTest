#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
RUN_DIR="backtests/ten_year"
mkdir -p "$RUN_DIR/raw_dbn_cache"

if [[ -z "${DATABENTO_API_KEY:-}" ]]; then
  echo "DATABENTO_API_KEY is required" >&2
  exit 1
fi

while true; do
  . .venv/bin/activate
  python backtest_databento.py \
    --config config.yaml \
    --start-date 2016-05-01 \
    --end-date 2026-04-30 \
    --output-dir "$RUN_DIR" \
    --resume \
    --raw-cache-dir "$RUN_DIR/raw_dbn_cache"
  rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "$(date -u +%FT%TZ) completed successfully" >> "$RUN_DIR/run.out"
    break
  fi
  echo "$(date -u +%FT%TZ) crashed with rc=$rc; restarting in 30s" >> "$RUN_DIR/run.out"
  sleep 30
done
