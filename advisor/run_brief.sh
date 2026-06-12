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
# Factor sheet from the nightly full-market research job (com.stockstest.advisor-research).
# If the panel is stale (>20h) rebuild inline so the brief never runs blind.
if [ -f advisor/data/research/signals_latest.json ]; then
  cp advisor/data/research/signals_latest.json "$CTX/factor_sheet.json"
  cp advisor/data/research/signals_latest.txt "$CTX/factor_sheet.txt" 2>/dev/null || true
fi
"$PY" - <<'PYEOF' 2>>advisor/logs/research.err || true
from advisor.research.datastore import panel_age_hours
import subprocess, sys
if panel_age_hours() > 20:
    print("[run_brief] panel stale — inline rebuild")
    subprocess.run([sys.executable, "-m", "advisor.research.nightly"], timeout=1200)
PYEOF
cp advisor/data/research/signals_latest.json "$CTX/factor_sheet.json" 2>/dev/null || true
cp advisor/data/research/signals_latest.txt "$CTX/factor_sheet.txt" 2>/dev/null || true

"$CLAUDE" -p "$(cat advisor/prompts/daily_brief.md)" \
  --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Write(advisor/data/**)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.journal:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.proposals:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.telegram_io:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.vol_check:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.quant:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.fair_value:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.factors:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.brief_check:*)" \
    "Bash(date:*)" \
  --max-turns 60 \
  >> "advisor/logs/brief_$DATE.log" 2>&1
RC=$?
echo "[run_brief] $(date) exit=$RC" >> advisor/logs/brief_runs.log
exit $RC
