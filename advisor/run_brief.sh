#!/bin/zsh
# Daily pre-market advisor brief (Tier 0) — launchd: com.stockstest.advisor-brief
# Fetches deterministic context, then runs a headless Claude research session.
set -u
source "$HOME/.webull_env"
REPO=/Users/varmakammili/Documents/GitHub/StocksTradingTest
PY=/opt/anaconda3/envs/llms/bin/python3
CLAUDE=/Users/varmakammili/.nvm/versions/node/v24.14.0/bin/claude
cd "$REPO"
export PYTHONPATH="$REPO"
# claude's shebang is `/usr/bin/env node` — launchd's PATH has no node (exit 127 on 2026-06-12)
export PATH="/Users/varmakammili/.nvm/versions/node/v24.14.0/bin:$PATH"

DATE=$(date +%F)
CTX="advisor/data/context/$DATE"
mkdir -p "$CTX" advisor/logs

# Skip weekends outright (launchd schedule already excludes them; belt+braces)
DOW=$(date +%u)
if [ "$DOW" -gt 5 ]; then echo "weekend — skipping"; exit 0; fi

"$PY" -m advisor.snapshot --json "$CTX/portfolio.json" > "$CTX/portfolio.txt" 2>>advisor/logs/snapshot.err || true
"$PY" -m advisor.market_context --json "$CTX/market.json" > "$CTX/market.txt" 2>>advisor/logs/market.err || true
"$PY" -m advisor.quant --json "$CTX/quant.json" > "$CTX/quant.txt" 2>>advisor/logs/quant.err || true

"$CLAUDE" -p "$(cat advisor/prompts/daily_brief.md)" \
  --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Write(advisor/data/**)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.journal:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.proposals:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.telegram_io:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.vol_check:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.quant:*)" \
    "Bash(date:*)" \
  --max-turns 60 \
  >> "advisor/logs/brief_$DATE.log" 2>&1
RC=$?
echo "[run_brief] $(date) exit=$RC" >> advisor/logs/brief_runs.log
exit $RC
