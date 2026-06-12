#!/bin/zsh
# Weekly portfolio review (Tier 0) — launchd: com.stockstest.advisor-weekly
set -u
source "$HOME/.webull_env"
REPO=/Users/varmakammili/Documents/GitHub/StocksTradingTest
PY=/opt/anaconda3/envs/llms/bin/python3
CLAUDE=/Users/varmakammili/.nvm/versions/node/v24.14.0/bin/claude
cd "$REPO"
export PYTHONPATH="$REPO"
export PATH="/Users/varmakammili/.nvm/versions/node/v24.14.0/bin:$PATH"
mkdir -p advisor/logs advisor/data/context

DATE=$(date +%F)
"$CLAUDE" -p "$(cat advisor/prompts/weekly_review.md)" \
  --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Write(advisor/data/**)" "Write(/tmp/**)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.journal:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.market_context:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.telegram_io:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.validate:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.fair_value:*)" \
    "Bash(date:*)" \
  --max-turns 60 \
  >> "advisor/logs/weekly_$DATE.log" 2>&1
RC=$?
echo "[run_weekly] $(date) exit=$RC" >> advisor/logs/brief_runs.log
exit $RC
