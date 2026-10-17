#!/bin/bash
# Chain2: wait for call pull → run IC/IF backtest
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest
source ~/.databento_env
LOG=spx_spread_bot/backtests/edge_hunt_cache/chain2.log
exec >> "$LOG" 2>&1

echo "=== CHAIN2 START $(date) ==="
echo "[chain2] waiting for pull_short_dte_calls..."
while pgrep -f pull_short_dte_calls > /dev/null; do sleep 60; done
echo "[chain2] call pull done at $(date)"
ls spx_spread_bot/backtests/edge_hunt_cache/short_dte_calls_data/ 2>/dev/null | wc -l | xargs echo "[chain2] call files:"

echo "=== IC/IF backtest $(date) ==="
/opt/anaconda3/envs/llms/bin/python -u spx_spread_bot/backtests/edge_hunt_cache/backtest_ic_if_intraday.py

echo "=== waiting for SPY pull $(date) ==="
while pgrep -f pull_spy_short_dte > /dev/null; do sleep 60; done
echo "[chain2] SPY pull done at $(date)"
ls spx_spread_bot/backtests/edge_hunt_cache/spy_short_dte_data/ 2>/dev/null | wc -l | xargs echo "[chain2] SPY files:"

echo "=== SPY intraday backtest $(date) ==="
/opt/anaconda3/envs/llms/bin/python -u spx_spread_bot/backtests/edge_hunt_cache/backtest_spy_intraday.py
echo "=== CHAIN2 COMPLETE $(date) ==="
