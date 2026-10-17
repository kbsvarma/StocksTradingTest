#!/bin/bash
# Launch pulls for 8 underlyings in parallel, then backtest each as it finishes.
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest
source ~/.databento_env
LOG=spx_spread_bot/backtests/edge_hunt_cache/multi_underlying.log
exec >> "$LOG" 2>&1

echo "=== MULTI-UNDERLYING START $(date) ==="

PY=/opt/anaconda3/envs/llms/bin/python
BASE=spx_spread_bot/backtests/edge_hunt_cache

# Config: ticker grid_$ otm_pct backtest_widths_csv
declare -a configs=(
    "TLT 1 0.05 1,2,3,5"
    "GLD 1 0.05 1,2,3,5,10"
    "SLV 0.5 0.07 0.5,1,2,3"
    "IWM 1 0.07 1,2,3,5"
    "QQQ 1 0.07 2,5,10,15,20"
    "EEM 0.5 0.07 0.5,1,2,3"
    "XLF 0.5 0.07 0.5,1,2,3"
    "XLE 1 0.07 1,2,3,5"
)

# Launch all pulls in parallel
PULL_PIDS=()
for c in "${configs[@]}"; do
    set -- $c
    TICKER=$1; GRID=$2; OTM=$3
    echo "[launch] pulling $TICKER (grid=\$$GRID otm=$OTM)"
    nohup $PY -u $BASE/pull_underlying.py --ticker $TICKER --grid $GRID --otm $OTM \
        > $BASE/pull_$(echo $TICKER | tr A-Z a-z).log 2>&1 &
    PULL_PIDS+=($!)
    sleep 5  # stagger to avoid auth race
done

echo "[parallel] launched ${#PULL_PIDS[@]} pulls, waiting..."
wait

echo "=== ALL PULLS DONE $(date) ==="

# Backtest each
for c in "${configs[@]}"; do
    set -- $c
    TICKER=$1; WIDTHS=$4
    echo ""
    echo "=== BACKTEST $TICKER (widths=$WIDTHS) $(date) ==="
    $PY -u $BASE/backtest_underlying.py --ticker $TICKER --widths $WIDTHS --years 2>&1 | tee $BASE/bt_$(echo $TICKER | tr A-Z a-z).log | grep -E "###|n=|total|TOP|cred=|DTE|^  20" | head -200
done

echo ""
echo "=== MULTI-UNDERLYING COMPLETE $(date) ==="
