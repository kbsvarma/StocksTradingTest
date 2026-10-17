#!/bin/bash
set -u
cd /Users/varmakammili/Documents/GitHub/StocksTradingTest
exec >> spx_spread_bot/backtests/edge_hunt_cache/pin_chain.log 2>&1

echo "=== PIN CHAIN START $(date) ==="
echo "[chain] waiting for OI pull..."
while pgrep -f "pull_oi_pin.py" > /dev/null; do sleep 30; done
echo "[chain] OI pull done at $(date)"
ls spx_spread_bot/backtests/edge_hunt_cache/pin_oi_data/spx_*.parquet 2>/dev/null | wc -l | xargs echo "[chain] SPX OI files:"
ls spx_spread_bot/backtests/edge_hunt_cache/pin_oi_data/avgo_*.parquet 2>/dev/null | wc -l | xargs echo "[chain] AVGO OI files:"

echo ""
echo "=== PIN BACKTEST $(date) ==="
/opt/anaconda3/envs/llms/bin/python -u spx_spread_bot/backtests/edge_hunt_cache/backtest_pin_strategy.py
echo "=== PIN CHAIN COMPLETE $(date) ==="
