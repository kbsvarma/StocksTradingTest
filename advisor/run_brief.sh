#!/usr/bin/env bash
# Daily pre-market advisor brief (Tier 0) — launchd or systemd user timer.
# Fetches deterministic context, then runs a headless Claude research session.
set -uo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY=${ADVISOR_PYTHON:-"$REPO/.venv/bin/python"}
[ -x "$PY" ] || PY=$(command -v python3)
CLAUDE=${CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}
[ -n "$CLAUDE" ] || { echo "[run_brief] claude executable not found" >&2; exit 78; }
ENV_FILE=${ADVISOR_ENV_FILE:-}
if [ -n "$ENV_FILE" ] && [ -r "$ENV_FILE" ]; then . "$ENV_FILE"; fi
cd "$REPO"
export PYTHONPATH="$REPO"
# claude's shebang is `/usr/bin/env node` — launchd's PATH has no node (exit 127 on 2026-06-12)
export PATH="$(dirname "$CLAUDE"):$PATH"
# launchd caps file descriptors at 256 — too low for the conda-env python
# imports (surfaced as PermissionError during module find_spec) AND the headless
# `claude` CLI ("Unexpected error, low max file descriptors: 256"). Raise it so
# the brief pipeline stops silently failing. (2026-07-01 fix)
ulimit -n 65536 2>/dev/null || true   # claude CLI needs >8192 FDs; kernel cap is 92160

DATE=$(date +%F)
CTX="advisor/data/context/$DATE"
mkdir -p "$CTX" advisor/logs

# Import probe (RCA 2026-07-16: calendar-fired 08:15 runs execute in a
# dark-wake context where TCC denies the import path — PermissionError in
# _path_importer_cache — while interval-fired jobs (watchdog) and
# user-context runs always work. 8 of 10 trial days died this way.)
# Probe; on persistent failure ABORT LOUDLY — the watchdog self-heal
# (ops_watchdog) relaunches this script from its working context after 09:00.
PROBE_OK=0
for _try in 1 2 3; do
  if "$PY" -c "import advisor.orchestrator" 2>>advisor/logs/wake_probe.err; then
    PROBE_OK=1; break
  fi
  echo "[run_brief] $(date) import probe failed (attempt $_try/3) — dark-wake TCC? retry in 60s" >> advisor/logs/brief_runs.log
  sleep 60
done
if [ "$PROBE_OK" -ne 1 ]; then
  echo "[run_brief] $(date) ABORT: import denied (dark-wake TCC) — watchdog will relaunch after 09:00" >> advisor/logs/brief_runs.log
  "$PY" -m advisor.telegram_io --send "⚠️ 08:15 brief blocked by dark-wake permissions — watchdog will auto-relaunch it after 09:00. No action needed unless no brief by 10:00." 2>/dev/null || true
  exit 78
fi

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
cp advisor/data/research/technical_latest.json "$CTX/technical.json" 2>/dev/null || true
cp advisor/data/research/macro/fred_latest.json "$CTX/fred_macro.json" 2>/dev/null || true
cp advisor/data/research/edgar_fundamental_latest.json "$CTX/edgar_fundamental.json" 2>/dev/null || true
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
