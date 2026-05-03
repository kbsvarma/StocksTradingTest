#!/usr/bin/env bash
set -euo pipefail

BOT_ROOT="/home/ubuntu/StocksTradingTest/spx_spread_bot"
RUN_DIR="$BOT_ROOT/backtests/ten_year"
QUEUE_LOG="$RUN_DIR/extend_queue.out"
CURRENT_PATTERN='backtest_databento.py --config config.yaml --start-date 2016-05-01 --end-date 2026-04-30 --output-dir backtests/ten_year'

mkdir -p "$RUN_DIR"
cd "$BOT_ROOT"

echo "$(date -u +%FT%TZ) queue started: wait for current 2016->2026 run" >> "$QUEUE_LOG"

while pgrep -af "$CURRENT_PATTERN" >/dev/null 2>&1; do
  echo "$(date -u +%FT%TZ) waiting: current run still active" >> "$QUEUE_LOG"
  sleep 120
done

echo "$(date -u +%FT%TZ) launching extension run: 2013-04-01 -> 2026-04-30" >> "$QUEUE_LOG"

. .venv/bin/activate
python backtest_databento.py \
  --config config.yaml \
  --start-date 2013-04-01 \
  --end-date 2026-04-30 \
  --output-dir backtests/ten_year \
  --resume \
  --raw-cache-dir backtests/ten_year/raw_dbn_cache \
  >> "$RUN_DIR/run.out" 2>&1

rc=$?
echo "$(date -u +%FT%TZ) extension run exited rc=$rc" >> "$QUEUE_LOG"
exit "$rc"
