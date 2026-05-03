#!/usr/bin/env bash
# go_live.sh — Flip the bot from paper trading to live with one command.
#
# What this does:
#   1. Sets PAPER_TRADING=False in config.py  → bot connects to live account
#   2. Sets TradingMode=live in IBC config    → Gateway logs into live account
#   3. Restarts ibgateway (live login on port 4001)
#   4. Restarts mes-orb-scheduler
#
# To revert to paper:
#   bash go_live.sh --paper
#
# PRECONDITIONS before going live:
#   - You have a funded IBKR live account
#   - Live account credentials are in ~/IBC/config.ini (same as paper, IBKR
#     uses the same username/password for both paper and live accounts)
#   - You have reviewed at least 20 paper trading days of results
#   - You are comfortable with real money losses up to $400/day

set -euo pipefail

BOT_CONFIG="${HOME}/StocksTradingTest/mes_orb_bot/config.py"
IBC_CONFIG="${HOME}/IBC/config.ini"
MODE="${1:-live}"

if [[ "$MODE" == "--paper" ]]; then
    TARGET_MODE="paper"
    PAPER_BOOL="True"
else
    TARGET_MODE="live"
    PAPER_BOOL="False"
fi

echo ""
echo "======================================================"
if [[ "$TARGET_MODE" == "live" ]]; then
    echo "  ⚠️  SWITCHING TO LIVE TRADING"
    echo ""
    echo "  This will trade REAL MONEY using your live IBKR account."
    echo "  Daily loss limit: \$400 (4 contracts × ~\$100 stop-out)"
    echo "  Max normal drawdown per month: ~\$2,000 (5% of \$50K)"
    echo ""
    echo "  Type GOLIVE to confirm, or anything else to abort:"
    read -r CONFIRM
    if [[ "$CONFIRM" != "GOLIVE" ]]; then
        echo "Aborted."
        exit 0
    fi
else
    echo "  Reverting to PAPER trading"
    echo ""
fi
echo "======================================================"
echo ""

echo "[1/3] Updating bot config: PAPER_TRADING=${PAPER_BOOL}…"
python3 - "$BOT_CONFIG" "$PAPER_BOOL" <<'PYEOF'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
val = sys.argv[2]
txt = p.read_text()
txt = re.sub(r'^PAPER_TRADING: bool = \S+', f'PAPER_TRADING: bool = {val}', txt, flags=re.MULTILINE)
p.write_text(txt)
PYEOF
echo "✓ PAPER_TRADING = ${PAPER_BOOL}"

echo "[2/3] Updating IBC config: TradingMode=${TARGET_MODE}…"
python3 - "$IBC_CONFIG" "$TARGET_MODE" <<'PYEOF'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
txt = p.read_text()
txt = re.sub(r'^TradingMode=.*', f'TradingMode={mode}', txt, flags=re.MULTILINE)
p.write_text(txt)
PYEOF
echo "✓ TradingMode = ${TARGET_MODE}"

echo "[3/3] Restarting services…"
sudo systemctl restart ibgateway
sleep 5
sudo systemctl restart mes-orb-scheduler

echo ""
echo "======================================================"
echo "  Done. Bot is now in ${TARGET_MODE^^} mode."
echo ""
echo "  Verify Gateway logged in:"
echo "    sudo journalctl -u ibgateway -f"
echo ""
echo "  Check scheduler:"
echo "    sudo journalctl -u mes-orb-scheduler -f"
echo ""
if [[ "$TARGET_MODE" == "live" ]]; then
    echo "  To revert to paper at any time:"
    echo "    bash $(realpath "$0") --paper"
fi
echo "======================================================"
