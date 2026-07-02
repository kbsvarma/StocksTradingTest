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
# launchd caps file descriptors at 256 — too low for the conda-env python
# imports (surfaced as PermissionError during module find_spec) AND the headless
# `claude` CLI ("Unexpected error, low max file descriptors: 256"). Raise it so
# the brief pipeline stops silently failing. (2026-07-01 fix)
ulimit -n 65536 2>/dev/null || true   # claude CLI needs >8192 FDs; kernel cap is 92160

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
    print("[run_brief] panel stale — inline rebuild (no ingest: signals only)")
    subprocess.run([sys.executable, "-m", "advisor.research.nightly", "--no-ingest"],
                   timeout=1200)
PYEOF
cp advisor/data/research/signals_latest.json "$CTX/factor_sheet.json" 2>/dev/null || true
cp advisor/data/research/signals_latest.txt "$CTX/factor_sheet.txt" 2>/dev/null || true
# stratified candidate slate + fundamental sheets (nightly generators)
cp advisor/data/research/candidates_latest.json "$CTX/candidates.json" 2>/dev/null || true
cp advisor/data/research/fundamental_latest.json "$CTX/fundamental.json" 2>/dev/null || true
# watchlist: expire stale entries, evaluate price triggers (deterministic —
# synthesis reads the ⚡ triggered flags as its warmest leads)
"$PY" -m advisor.watchlist --sweep 2>>advisor/logs/watchlist.err || true
"$PY" -m advisor.watchlist --check 2>>advisor/logs/watchlist.err || true
# catalyst calendar: 14d earnings lookahead on held/watched/slate names
"$PY" -m advisor.research.calendar_feed --json "$CTX/calendar.json" 2>>advisor/logs/calendar.err || true

# Three-stage pipeline (synthesis → red-team → publish), INTELLIGENCE_PLAN §4.
# The orchestrator owns stage tool-whitelists, checks, retries, the legacy
# single-session fallback, failure Telegram alerts, and post-publish
# `journal --stamp-ref`. Stage logs: advisor/logs/brief_$DATE_<stage>.log
export CLAUDE_BIN="$CLAUDE"
"$PY" -m advisor.orchestrator >> "advisor/logs/brief_$DATE.log" 2>&1
RC=$?
echo "[run_brief] $(date) exit=$RC" >> advisor/logs/brief_runs.log
exit $RC
