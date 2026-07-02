#!/bin/zsh
# Evening librarian — builds per-name dossiers for tomorrow's synthesis.
# launchd: com.stockstest.advisor-librarian (19:00 ET weekdays)
set -u
source "$HOME/.webull_env"
REPO=/Users/varmakammili/Documents/GitHub/StocksTradingTest
PY=/opt/anaconda3/envs/llms/bin/python3
CLAUDE=/Users/varmakammili/.nvm/versions/node/v24.14.0/bin/claude
cd "$REPO"
export PYTHONPATH="$REPO"
export PATH="/Users/varmakammili/.nvm/versions/node/v24.14.0/bin:$PATH"
ulimit -n 65536 2>/dev/null || true

DATE=$(date +%F)
mkdir -p advisor/logs
LOG="advisor/logs/librarian_$DATE.log"

# weekends: nothing new on the sheet, skip
DOW=$(date +%u)
if [ "$DOW" -gt 5 ]; then echo "weekend — skipping" >> "$LOG"; exit 0; fi

# post-mortems first: resolve level-hit / past-time-stop calls while today's
# price path is fresh (facts already journaled by the watcher)
export CLAUDE_BIN="$CLAUDE"
"$PY" -m advisor.postmortem_runner --max 3 >> "$LOG" 2>&1 || true

# deterministic queue (3-5 names: open calls, new entrants, stale dossiers)
QUEUE="advisor/data/research/queue_$DATE.json"
"$PY" -m advisor.research.librarian_queue --max 5 >> "$LOG" 2>&1 || true
if [ ! -f "$QUEUE" ]; then echo "[librarian] no queue file — abort" >> "$LOG"; exit 1; fi
N=$("$PY" -c "import json;print(len(json.load(open('$QUEUE'))['candidates']))" 2>/dev/null || echo 0)
if [ "$N" -eq 0 ]; then echo "[librarian] queue empty — nothing to do" >> "$LOG"; exit 0; fi

echo "[librarian] $(date) start — $N name(s)" >> "$LOG"
"$CLAUDE" -p "QUEUE_FILE: $QUEUE
TODAY: $DATE

$(cat advisor/prompts/librarian.md)" \
  --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" \
    "Write(advisor/data/knowledge/**)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.deep_pull:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.peek:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.research.fair_value:*)" \
    "Bash(/opt/anaconda3/envs/llms/bin/python3 -m advisor.vol_check:*)" \
    "Bash(date:*)" \
  --max-turns 45 \
  >> "$LOG" 2>&1
RC=$?
echo "{\"ts\": \"$(date -Iseconds)\", \"stage\": \"librarian\", \"rc\": $RC, \"n_names\": $N}" >> advisor/logs/pipeline_runs.jsonl
echo "[librarian] $(date) exit=$RC" >> "$LOG"
exit $RC
