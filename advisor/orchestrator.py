"""Morning-pipeline orchestrator — sequential, degrade-gracefully, never silent.

Runs the three-stage chain (synthesis → red-team → publish) of headless
Claude sessions, replacing the old single 60-turn session. Design rules
(INTELLIGENCE_PLAN §4, ops-critic adopted wholesale):

- SEQUENTIAL only — no parallel claude processes on this Mac.
- Fail closed for actionable output: synthesis or red-team failure means no
  recommendation is published. A zero-view draft may still publish after a
  red-team outage because there is no trade to approve.
- Deadline guard: if synthesis has eaten the morning, skip red-team rather
  than push the brief past the open.
- Preflight: a 1-turn claude ping catches the expired-/login failure mode at
  06:00-grade blast radius BEFORE burning the context fetch.
- Heartbeats: one JSONL row per stage to advisor/logs/pipeline_runs.jsonl.
- After publish: `journal --stamp-ref` code-stamps reference prices
  (deterministic — never left to the model).

CLI: python -m advisor.orchestrator [--date YYYY-MM-DD] [--stage NAME]
     [--skip-preflight] [--dry-run]
Called by run_brief.sh (which owns env: ~/.webull_env, PATH w/ node, ulimit).
"""
from __future__ import annotations

import json
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
CLAUDE = os.environ.get(
    "CLAUDE_BIN", "/Users/varmakammili/.nvm/versions/node/v24.14.0/bin/claude")
LOGS = REPO / "advisor" / "logs"
HEARTBEATS = LOGS / "pipeline_runs.jsonl"
PIPELINE_LOCK = "/tmp/advisor-pipeline.lock"

SKIP_REDTEAM_AFTER_S = 45 * 60      # protect the send deadline over the audit

READ_TOOLS = ["Read", "Glob", "Grep"]
_B = f"Bash({PY} -m advisor"


def _tools(*mods: str) -> list[str]:
    return [f"{_B}.{m}:*)" for m in mods]


STAGES: dict[str, dict] = {
    "macro": {
        "prompt": "advisor/prompts/macro.md",
        "max_turns": 25,
        "timeout_s": 1200,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch",
                               "Write(advisor/data/context/**)",
                               "Edit(advisor/data/context/**)",
                               "Write(advisor/data/knowledge/narrative/**)",
                               "Edit(advisor/data/knowledge/narrative/**)"]
        + ["Bash(date:*)"],
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.macro_check", str(ctx / "macro.json")]),
    },
    "synthesis": {
        "prompt": "advisor/prompts/synthesis.md",
        "max_turns": 40,
        "timeout_s": 1800,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch", "Write(advisor/data/**)",
                               "Edit(advisor/data/**)"]
        + _tools("journal --list", "proposals --list", "vol_check", "quant",
                 "research.fair_value", "research.factors", "research.peek",
                 "watchlist --list", "brief_check") + ["Bash(date:*)"],
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.brief_check", "--draft",
             str(ctx / "views_draft.json")]),
    },
    "redteam": {
        "prompt": "advisor/prompts/redteam.md",
        "max_turns": 30,
        "timeout_s": 1500,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch",
                               "Write(advisor/data/context/**)",
                               "Edit(advisor/data/context/**)"]
        + _tools("journal --list", "vol_check", "research.fair_value",
                 "research.peek", "research.redteam_check") + ["Bash(date:*)"],
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.research.redteam_check",
             str(ctx / "redteam.json"), str(ctx / "views_draft.json")]),
    },
    "publish": {
        "prompt": "advisor/prompts/publish.md",
        "max_turns": 20,
        "timeout_s": 1200,
        "tools": READ_TOOLS + ["Write(advisor/data/**)", "Edit(advisor/data/**)"]
        + _tools("journal", "proposals", "telegram_io", "watchlist",
                 "brief_check") + ["Bash(date:*)"],
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.brief_check", str(ctx / "brief.json")]),
    },
}


def _heartbeat(stage: str, rc: int | None, secs: float, note: str = "") -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(ET).isoformat(), "stage": stage, "rc": rc,
           "secs": round(secs, 1), "note": note}
    with HEARTBEATS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[orchestrator] {stage}: rc={rc} {secs:.0f}s {note}", flush=True)


def _run_check(cmd: list[str]) -> int:
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=120)
        if r.stdout:
            print(r.stdout[-2000:], flush=True)
        return r.returncode
    except Exception as exc:
        print(f"[orchestrator] check failed to run: {exc}", flush=True)
        return 1


def _telegram(msg: str) -> None:
    try:
        subprocess.run([PY, "-m", "advisor.telegram_io", "--send", msg],
                       cwd=REPO, timeout=60)
    except Exception as exc:
        print(f"[orchestrator] telegram alert failed: {exc}", flush=True)


def _claude(stage: str, date: str, dry_run: bool = False) -> int:
    cfg = STAGES[stage]
    ctx = f"advisor/data/context/{date}"
    prompt = (f"CONTEXT_DIR: {ctx}\nTODAY: {date}\n\n"
              + (REPO / cfg["prompt"]).read_text(encoding="utf-8"))
    cmd = [CLAUDE, "-p", prompt, "--max-turns", str(cfg["max_turns"]),
           "--allowedTools", *cfg["tools"]]
    if dry_run:
        print(f"[orchestrator] DRY-RUN {stage}: {' '.join(cmd[:1])} "
              f"-p <{cfg['prompt']}> --max-turns {cfg['max_turns']} "
              f"--allowedTools {len(cfg['tools'])} entries "
              f"(timeout {cfg['timeout_s']}s)", flush=True)
        return 0
    log = LOGS / f"brief_{date}_{stage}.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as lf:
        lf.write(f"\n===== {stage} start {datetime.now(ET).isoformat()} =====\n")
        lf.flush()
        try:
            r = subprocess.run(cmd, cwd=REPO, stdout=lf, stderr=subprocess.STDOUT,
                               timeout=cfg["timeout_s"])
            return r.returncode
        except subprocess.TimeoutExpired:
            lf.write(f"\n===== {stage} TIMEOUT after {cfg['timeout_s']}s =====\n")
            return 124


def preflight() -> bool:
    """Cheap auth/liveness ping — catches expired `claude /login` loudly."""
    try:
        r = subprocess.run([CLAUDE, "-p", "Reply with exactly: PONG",
                            "--max-turns", "1"],
                           cwd=REPO, capture_output=True, text=True, timeout=180)
        ok = r.returncode == 0 and "PONG" in (r.stdout or "")
        if not ok:
            out = (r.stdout or r.stderr or "")[:300]
            print(f"[orchestrator] preflight failed rc={r.returncode} out={out!r}",
                  flush=True)
        return ok
    except Exception as exc:
        print(f"[orchestrator] preflight exception: {exc}", flush=True)
        return False


def run_stage(stage: str, date: str, dry_run: bool = False) -> int:
    ctx = REPO / "advisor" / "data" / "context" / date
    cfg = STAGES[stage]
    t0 = time.time()
    rc = _claude(stage, date, dry_run)
    if rc == 0 and cfg["check"] and not dry_run:
        crc = cfg["check"](ctx)
        if crc != 0:
            rc = 3   # session finished but its output contract is invalid
    _heartbeat(stage, rc, time.time() - t0)
    return rc


def run_pipeline(date: str, skip_preflight: bool = False,
                 dry_run: bool = False) -> int:
    lock = open(PIPELINE_LOCK, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _heartbeat("pipeline", 75, 0, f"already running; refused date={date}")
        return 75
    t_start = time.time()
    _heartbeat("pipeline", None, 0, f"start date={date}")

    if not skip_preflight and not dry_run:
        if not preflight():
            _telegram("❌ MORNING PIPELINE ABORTED — claude CLI preflight failed "
                      "(likely expired `claude /login`). No brief today until fixed.")
            _heartbeat("pipeline", 78, time.time() - t_start, "preflight failed")
            return 78

    mrc = run_stage("macro", date, dry_run)
    if mrc != 0:
        _heartbeat("macro", mrc, 0,
                   "failed — synthesis will do its own overnight scan")

    rc = run_stage("synthesis", date, dry_run)
    if rc != 0:
        _heartbeat("pipeline", rc, time.time() - t_start,
                   "synthesis failed — actionable publication blocked")
        _telegram("❌ NO RECOMMENDATIONS TODAY — synthesis failed validation. "
                  f"Fail-closed; inspect advisor/logs/brief_{date}_synthesis.log")
        return rc

    ctx = REPO / "advisor" / "data" / "context" / date
    try:
        actionable = bool(json.loads((ctx / "views_draft.json").read_text())
                          .get("views"))
    except Exception:
        actionable = True

    elapsed = time.time() - t_start
    if elapsed > SKIP_REDTEAM_AFTER_S:
        _heartbeat("redteam", None, 0,
                   f"skipped — {elapsed:.0f}s elapsed, protecting send deadline")
        if actionable and not dry_run:
            _telegram("❌ NO RECOMMENDATIONS TODAY — red-team deadline expired. "
                      "Actionable drafts were blocked, not shipped unreviewed.")
            return 74
    else:
        rrc = run_stage("redteam", date, dry_run)
        if rrc != 0:
            _heartbeat("redteam", rrc, 0, "failed — actionable publication blocked")
            if actionable and not dry_run:
                _telegram("❌ NO RECOMMENDATIONS TODAY — independent red-team "
                          "failed validation. Actionable drafts were blocked.")
                return rrc

    prc = run_stage("publish", date, dry_run)
    if prc != 0:
        _heartbeat("publish", prc, 0, "failed — one retry")
        prc = run_stage("publish", date, dry_run)
    if prc != 0:
        _telegram("❌ NO BRIEF TODAY — publish stage failed twice after a good "
                  f"synthesis. Draft views exist in advisor/data/context/{date}/"
                  f"views_draft.json; logs: advisor/logs/brief_{date}_publish.log")
        _heartbeat("pipeline", prc, time.time() - t_start, "publish failed twice")
        return prc

    if not dry_run:
        try:
            subprocess.run([PY, "-m", "advisor.journal", "--stamp-ref"],
                           cwd=REPO, timeout=300, check=True)
        except Exception as exc:
            _heartbeat("stamp-ref", 1, 0, f"failed: {exc}")
            _telegram("❌ Published brief could not be armed with reference prices; "
                      "treat its views as non-actionable until repaired.")
            return 69

    _heartbeat("pipeline", 0, time.time() - t_start, "complete")
    return 0


def main() -> int:
    args = sys.argv[1:]
    date = (args[args.index("--date") + 1] if "--date" in args
            else datetime.now(ET).date().isoformat())
    dry = "--dry-run" in args
    if "--stage" in args:
        return run_stage(args[args.index("--stage") + 1], date, dry)
    return run_pipeline(date, skip_preflight="--skip-preflight" in args,
                        dry_run=dry)


if __name__ == "__main__":
    sys.exit(main())
