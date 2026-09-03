#!/usr/bin/env bash
# Weekly portfolio review (Tier 0) — launchd: com.stockstest.advisor-weekly
set -uo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${ADVISOR_PYTHON:-"$REPO/.venv/bin/python"}
[ -x "$PY" ] || PY=$(command -v python3)
CLAUDE=${CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}
[ -n "$CLAUDE" ] || { echo "[weekly] claude executable not found" >&2; exit 78; }
ENV_FILE=${ADVISOR_ENV_FILE:-}
if [ -n "$ENV_FILE" ] && [ -r "$ENV_FILE" ]; then . "$ENV_FILE"; fi
cd "$REPO"
export PYTHONPATH="$REPO"
export PATH="$(dirname "$CLAUDE"):$PATH"
# launchd caps FDs at 256 — the claude CLI needs more (same 2026-07-01 fix
# as run_brief.sh; this script missing it was why weekly exited 1)
ulimit -n 65536 2>/dev/null || true
mkdir -p advisor/logs advisor/data/context

DATE=$(date +%F)
"$CLAUDE" -p "$(cat advisor/prompts/weekly_review.md)" \
  --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Write(advisor/data/**)" "Edit(advisor/data/**)" "Write(/tmp/**)" \
    "Bash($PY -m advisor.journal:*)" \
    "Bash($PY -m advisor.market_context:*)" \
    "Bash($PY -m advisor.telegram_io:*)" \
    "Bash($PY -m advisor.research.validate:*)" \
    "Bash($PY -m advisor.research.fair_value:*)" \
    "Bash($PY -m advisor.research.calibration:*)" \
    "Bash($PY -m advisor.research.attribution:*)" \
    "Bash($PY -m advisor.research.ic_monitor:*)" \
    "Bash($PY -m advisor.watchlist:*)" \
    "Bash($PY -m advisor.research.peek:*)" \
    "Bash(date:*)" \
  --max-turns 60 \
  >> "advisor/logs/weekly_$DATE.log" 2>&1
RC=$?
echo "[run_weekly] $(date) exit=$RC" >> advisor/logs/brief_runs.log
exit $RC
