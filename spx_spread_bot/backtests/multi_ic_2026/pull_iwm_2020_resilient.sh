#!/bin/bash
# Self-restarting wrapper. If python dies (SIGHUP, OOM, transient API error, anything),
# this loop restarts it. Script supports resume via skip-if-exists on each file.
# Exits cleanly when the python process exits with [IWM done].
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/backtests/multi_ic_2026
source ~/.databento_env

LOG=pull_IWM_2020_2023.log
PY=/opt/anaconda3/envs/llms/bin/python
ATTEMPT=0
MAX_ATTEMPTS=20

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    ATTEMPT=$((ATTEMPT + 1))
    echo "" >> $LOG
    echo "=== ATTEMPT $ATTEMPT $(date) ===" >> $LOG

    # Already done?
    if grep -q "\[IWM done\]" $LOG 2>/dev/null; then
        echo "[wrapper] [IWM done] already present — exiting wrapper" >> $LOG
        exit 0
    fi

    $PY -u pull_multi.py --ticker IWM --start 2020-01-02 --end 2023-12-31 \
        --otm-max 0.05 --out-subdir iwm_wide --max-day-cost 3.0 --confirm >> $LOG 2>&1 < /dev/null

    EXIT=$?
    echo "[wrapper] python exit=$EXIT at $(date)" >> $LOG

    # Re-check: did it finish (printed [IWM done])?
    if grep -q "\[IWM done\]" $LOG 2>/dev/null; then
        echo "[wrapper] success — exiting wrapper" >> $LOG
        exit 0
    fi

    echo "[wrapper] not done, sleeping 30s before retry" >> $LOG
    sleep 30
done

echo "[wrapper] gave up after $MAX_ATTEMPTS attempts" >> $LOG
exit 1
