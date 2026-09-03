"""Post-mortem trigger — finds calls needing resolution, spawns one headless
session each (sequential, capped). Facts are already journaled by the exit
watcher (resolve_pending rows); the session interprets and writes the final
resolve + lesson. Called by run_librarian.sh each evening (no extra service).

CLI: python -m advisor.postmortem_runner [--max 3] [--dry-run]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.outcomes import load_rows, is_test_row

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
CLAUDE = os.environ.get(
    "CLAUDE_BIN", "/Users/varmakammili/.nvm/versions/node/v24.14.0/bin/claude")
PY = sys.executable
LOGS = REPO / "advisor" / "logs"

TOOLS = ["Read", "Glob", "Grep", "WebFetch",
         "Write(advisor/data/knowledge/**)",
         "Edit(advisor/data/knowledge/**)",
         f"Bash({PY} -m advisor.journal:*)",
         f"Bash({PY} -m advisor.watchlist:*)",
         f"Bash({PY} -m advisor.research.outcomes:*)",
         f"Bash({PY} -m advisor.research.peek:*)",
         "Bash(date:*)"]


def pending() -> list[dict]:
    """Calls needing a post-mortem: watcher-flagged hits, or open past
    time_stop. (User-closed calls get resolved by the weekly review.)"""
    eff, origin = load_rows()
    today = datetime.now(ET).date().isoformat()
    out = []
    for eid, e in eff.items():
        if origin.get(eid) != "view" or is_test_row(e):
            continue
        if e.get("status") != "open":
            continue
        if e.get("resolve_pending"):
            out.append({**e, "id": eid, "why": f"level hit ({e.get('hit_level')})"})
        elif (e.get("time_stop") or "9999") < today:
            out.append({**e, "id": eid, "why": "past time_stop"})
    return out


def run_one(call: dict, dry_run: bool = False) -> int:
    prompt = (f"CALL_ID: {call['id']}\n"
              f"CALL_JSON: {json.dumps(call)}\n\n"
              + (REPO / "advisor" / "prompts" / "post_mortem.md").read_text())
    cmd = [CLAUDE, "-p", prompt, "--max-turns", "18", "--allowedTools", *TOOLS]
    if dry_run:
        print(f"[post-mortem] DRY-RUN {call['id']} ({call['why']})")
        return 0
    log = LOGS / f"postmortem_{call['id']}_{datetime.now(ET).strftime('%Y%m%d')}.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as lf:
        try:
            r = subprocess.run(cmd, cwd=REPO, stdout=lf,
                               stderr=subprocess.STDOUT, timeout=900)
            return r.returncode
        except subprocess.TimeoutExpired:
            lf.write("\nTIMEOUT 900s\n")
            return 124


def main() -> int:
    args = sys.argv[1:]
    cap = int(args[args.index("--max") + 1]) if "--max" in args else 3
    dry = "--dry-run" in args
    todo = pending()[:cap]
    if not todo:
        print("[post-mortem] nothing pending")
        return 0
    print(f"[post-mortem] {len(todo)} call(s): "
          + ", ".join(f"{c['id']}({c['why']})" for c in todo))
    failures = 0
    for c in todo:
        t0 = time.time()
        rc = run_one(c, dry)
        with (LOGS / "pipeline_runs.jsonl").open("a") as f:
            f.write(json.dumps({"ts": datetime.now(ET).isoformat(),
                                "stage": f"post_mortem:{c['id']}", "rc": rc,
                                "secs": round(time.time() - t0, 1)}) + "\n")
        failures += 1 if rc != 0 else 0
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
