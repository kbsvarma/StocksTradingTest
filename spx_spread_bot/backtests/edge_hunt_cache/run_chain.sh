#!/bin/bash
# Chain: wait for current multi-DTE pull → run intraday short-DTE pull → run hold-to-expiry backtest
# All output -> chain.log
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest
source ~/.databento_env
LOG=spx_spread_bot/backtests/edge_hunt_cache/chain.log
exec >> "$LOG" 2>&1

echo "=== CHAIN START $(date) ==="

# Step 1: wait for in-flight multi-DTE pull v2 to exit
echo "[chain] waiting for pull_multi_dte_v2..."
while pgrep -f pull_multi_dte_v2 > /dev/null; do
    sleep 60
done
echo "[chain] multi_dte_v2 done at $(date)"
ls spx_spread_bot/backtests/edge_hunt_cache/multi_dte_data/ | wc -l | xargs echo "[chain] multi_dte_data files:"

# Step 2: hold-to-expiry backtest on whatever multi_dte data we got
echo "=== STEP 2: hold-to-expiry backtest $(date) ==="
/opt/anaconda3/envs/llms/bin/python -u spx_spread_bot/backtests/edge_hunt_cache/backtest_multi_dte.py test || echo "[chain] backtest_multi_dte failed (non-fatal)"

# Step 3: short-DTE intraday pull (2-7 DTE, 09:45 + 15:55)
echo "=== STEP 3: short-DTE intraday pull $(date) ==="
/opt/anaconda3/envs/llms/bin/python -u spx_spread_bot/backtests/edge_hunt_cache/pull_short_dte_intraday.py --full
echo "[chain] short_dte pull done at $(date)"
ls spx_spread_bot/backtests/edge_hunt_cache/short_dte_intraday_data/ 2>/dev/null | wc -l | xargs echo "[chain] short_dte files:"

echo "=== CHAIN COMPLETE $(date) ==="
