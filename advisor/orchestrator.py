"""Morning-pipeline orchestrator — sequential, degrade-gracefully, never silent.

Runs the three-stage chain (synthesis → red-team → publish) of headless
Claude sessions, replacing the old single 60-turn session. Design rules
(INTELLIGENCE_PLAN §4, ops-critic adopted wholesale):

- SEQUENTIAL only — no parallel provider processes on a host.
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
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.pipeline_status import classify_failure, classify_text, write_status
from advisor.publication_assembly import assemble as assemble_publication
from advisor.publication_commit import commit as commit_publication

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
CLAUDE = os.environ.get(
    "CLAUDE_BIN", "claude")
LOGS = REPO / "advisor" / "logs"
HEARTBEATS = LOGS / "pipeline_runs.jsonl"
PIPELINE_LOCK = "/tmp/advisor-pipeline.lock"

SKIP_REDTEAM_AFTER_S = 45 * 60      # protect the send deadline over the audit

READ_TOOLS = ["Read", "Glob", "Grep"]
_B = f"Bash({PY} -m advisor"


def _tools(*mods: str) -> list[str]:
    # Current CLI grammar is shell prefix + a space-separated glob. The old
    # ':*' suffix did not match and non-interactive sessions wasted turns
    # requesting approvals that nobody could answer.
    return [f"{_B}.{m} *)" for m in mods]


DATE_TOOLS = ["Bash(date)", "Bash(date *)"]


STAGES: dict[str, dict] = {
    "macro": {
        "prompt": "advisor/prompts/macro.md",
        "max_turns": 45,
        "timeout_s": 1200,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch",
                               "Edit(advisor/data/context/{date}/macro.json)",
                               "Edit(advisor/data/knowledge/narrative/current_themes.md)"]
        + _tools("macro_check") + DATE_TOOLS,
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.macro_check", str(ctx / "macro.json")]),
    },
    "synthesis": {
        "prompt": "advisor/prompts/synthesis.md",
        "max_turns": 40,
        "timeout_s": 1800,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch",
                               "Edit(advisor/data/context/{date}/views_draft.json)"]
        + _tools("journal --list", "proposals --list", "vol_check", "quant",
                 "research.fair_value", "research.factors", "research.peek",
                 "watchlist --list", "brief_check") + DATE_TOOLS,
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.brief_check", "--draft",
             str(ctx / "views_draft.json")]),
    },
    "redteam": {
        "prompt": "advisor/prompts/redteam.md",
        "max_turns": 55,
        "timeout_s": 1500,
        "tools": READ_TOOLS + ["WebSearch", "WebFetch",
                               "Edit(advisor/data/context/{date}/redteam.json)"]
        + _tools("journal --list", "vol_check", "research.fair_value",
                 "research.peek", "research.redteam_check") + DATE_TOOLS,
        "check": lambda ctx: _run_check(
            [PY, "-m", "advisor.research.redteam_check",
             str(ctx / "redteam.json"), str(ctx / "views_draft.json")]),
    },
}

STAGE_OUTPUTS = {
    "macro": ("macro.json",),
    "synthesis": ("views_draft.json",),
    "redteam": ("redteam.json",),
}


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _quarantine_changed(stage: str, ctx: Path,
                        before: dict[Path, tuple[int, int] | None]) -> list[Path]:
    """Move only outputs changed by the failed attempt; preserve last-known-good."""
    moved = []
    stamp = datetime.now(ET).strftime("%Y%m%dT%H%M%S")
    for path in (ctx / name for name in STAGE_OUTPUTS.get(stage, ())):
        if path.exists() and _signature(path) != before.get(path):
            target = path.with_name(f"{path.name}.invalid.{stamp}")
            os.replace(path, target)
            moved.append(target)
    return moved


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
    allowed_tools = [rule.format(date=date) for rule in cfg["tools"]]
    ctx = f"advisor/data/context/{date}"
    prompt = (f"CONTEXT_DIR: {ctx}\nTODAY: {date}\n"
              f"ADVISOR_PYTHON: {PY}\n"
              f"For every command documented as `python -m advisor...`, use "
              f"the exact executable `{PY}` instead. It is the allow-listed "
              f"project runtime. Never request interactive approval; if a tool "
              f"is denied, report the denial and stop.\n\n"
              + (REPO / cfg["prompt"]).read_text(encoding="utf-8"))
    cmd = [CLAUDE, "-p", prompt, "--max-turns", str(cfg["max_turns"]),
           "--permission-mode", "dontAsk", "--permission-prompts", "none",
           "--allowedTools", *allowed_tools]
    if dry_run:
        print(f"[orchestrator] DRY-RUN {stage}: {' '.join(cmd[:1])} "
              f"-p <{cfg['prompt']}> --max-turns {cfg['max_turns']} "
              f"--allowedTools {len(allowed_tools)} entries "
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


def preflight() -> tuple[bool, str]:
    """Cheap auth/liveness ping — catches expired `claude /login` loudly."""
    try:
        r = subprocess.run([CLAUDE, "-p", "Reply with exactly: PONG",
                            "--max-turns", "1"],
                           cwd=REPO, capture_output=True, text=True, timeout=180)
        output = r.stdout or r.stderr or ""
        ok = r.returncode == 0 and "PONG" in output
        if not ok:
            out = output[:300]
            print(f"[orchestrator] preflight failed rc={r.returncode} out={out!r}",
                  flush=True)
        return ok, ("none" if ok else classify_text(r.returncode, output))
    except Exception as exc:
        print(f"[orchestrator] preflight exception: {exc}", flush=True)
        return False, "provider_preflight_failure"


def run_stage(stage: str, date: str, dry_run: bool = False) -> int:
    ctx = REPO / "advisor" / "data" / "context" / date
    cfg = STAGES[stage]
    outputs = [ctx / name for name in STAGE_OUTPUTS.get(stage, ())]
    before = {path: _signature(path) for path in outputs}
    t0 = time.time()
    rc = _claude(stage, date, dry_run)
    if rc == 0 and cfg["check"] and not dry_run:
        crc = cfg["check"](ctx)
        if crc != 0:
            rc = 3   # session finished but its output contract is invalid
    note = ""
    if rc != 0 and not dry_run:
        moved = _quarantine_changed(stage, ctx, before)
        if moved:
            note = "quarantined: " + ", ".join(path.name for path in moved)
    if not dry_run:
        _heartbeat(stage, rc, time.time() - t0, note)
    return rc


def run_pipeline(date: str, skip_preflight: bool = False,
                 dry_run: bool = False) -> int:
    if dry_run:
        for stage in STAGES:
            rc = run_stage(stage, date, dry_run=True)
            if rc:
                return rc
        print("[orchestrator] DRY-RUN complete — no status or artifacts written",
              flush=True)
        return 0
    run_id = f"{date}-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(ET).isoformat()
    write_status(run_id=run_id, date=date, state="starting", stage="pipeline",
                 started_at=started_at)
    lock = open(PIPELINE_LOCK, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _heartbeat("pipeline", 75, 0, f"already running; refused date={date}")
        write_status(run_id=run_id, date=date, state="failed", stage="pipeline",
                     returncode=75, reason="already_running",
                     note="single-flight lock refused duplicate run",
                     started_at=started_at)
        return 75
    t_start = time.time()
    _heartbeat("pipeline", None, 0, f"start date={date}")
    write_status(run_id=run_id, date=date, state="running", stage="preflight",
                 started_at=started_at)

    if not skip_preflight and not dry_run:
        preflight_ok, preflight_reason = preflight()
        if not preflight_ok:
            operator_hint = ("provider capacity is exhausted; scheduled retry will "
                             "remain fail-closed" if preflight_reason == "provider_quota"
                             else "provider credentials or CLI availability need review")
            _telegram(f"❌ MORNING PIPELINE ABORTED — provider preflight failed: "
                      f"{preflight_reason}; {operator_hint}.")
            _heartbeat("pipeline", 78, time.time() - t_start,
                       f"preflight failed: {preflight_reason}")
            write_status(run_id=run_id, date=date, state="failed", stage="preflight",
                         returncode=78, reason=preflight_reason,
                         note="provider preflight failed", started_at=started_at)
            return 78

    write_status(run_id=run_id, date=date, state="running", stage="macro",
                 started_at=started_at)
    mrc = run_stage("macro", date, dry_run)
    if mrc != 0:
        _heartbeat("macro", mrc, 0,
                   "failed — synthesis will do its own overnight scan")

    write_status(run_id=run_id, date=date, state="running", stage="synthesis",
                 started_at=started_at)
    rc = run_stage("synthesis", date, dry_run)
    if rc != 0:
        log = LOGS / f"brief_{date}_synthesis.log"
        reason = classify_failure(rc, log)
        _heartbeat("pipeline", rc, time.time() - t_start,
                   "synthesis failed — actionable publication blocked")
        _telegram("❌ NO RECOMMENDATIONS TODAY — synthesis failed validation. "
                  f"Fail-closed; inspect advisor/logs/brief_{date}_synthesis.log")
        write_status(run_id=run_id, date=date, state="failed", stage="synthesis",
                     returncode=rc, reason=reason,
                     note="actionable publication blocked", started_at=started_at)
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
            write_status(run_id=run_id, date=date, state="failed",
                         stage="redteam", returncode=74,
                         reason="redteam_deadline",
                         note="actionable drafts blocked at deadline",
                         started_at=started_at)
            return 74
    else:
        write_status(run_id=run_id, date=date, state="running", stage="redteam",
                     started_at=started_at)
        rrc = run_stage("redteam", date, dry_run)
        if rrc != 0:
            _heartbeat("redteam", rrc, 0, "failed — actionable publication blocked")
            if actionable and not dry_run:
                _telegram("❌ NO RECOMMENDATIONS TODAY — independent red-team "
                          "failed validation. Actionable drafts were blocked.")
                write_status(run_id=run_id, date=date, state="failed",
                             stage="redteam", returncode=rrc,
                             reason=classify_failure(rrc, LOGS / f"brief_{date}_redteam.log"),
                             note="actionable publication blocked",
                             started_at=started_at)
                return rrc

    write_status(run_id=run_id, date=date, state="running", stage="publish",
                 started_at=started_at)
    try:
        assemble_publication(REPO / "advisor" / "data" / "context" / date)
        _heartbeat("assembly", 0, 0, "deterministic merge validated")
    except Exception as exc:
        _telegram("❌ NO BRIEF TODAY — deterministic publication assembly failed "
                  f"after valid research stages: {type(exc).__name__}.")
        _heartbeat("pipeline", 70, time.time() - t_start,
                   f"assembly failed: {type(exc).__name__}: {exc}")
        write_status(run_id=run_id, date=date, state="failed", stage="publish",
                     returncode=70, reason="publication_assembly_failure",
                     note="publication blocked", started_at=started_at)
        return 70

    if not dry_run:
        try:
            receipt = commit_publication(REPO / "advisor" / "data" / "context" / date)
            _heartbeat("commit", 0, 0,
                       f"journaled={len(receipt['journal_ids'])} "
                       f"notification={receipt['notification_status']}")
        except Exception as exc:
            ctx = REPO / "advisor" / "data" / "context" / date
            stamp = datetime.now(ET).strftime("%Y%m%dT%H%M%S")
            for name in ("brief.pending.json",):
                path = ctx / name
                if path.exists():
                    os.replace(path, path.with_name(f"{path.name}.invalid.{stamp}"))
            _heartbeat("commit", 1, 0, f"failed: {type(exc).__name__}: {exc}")
            _telegram("❌ VALIDATED DRAFT NOT PUBLISHED — deterministic commit failed; "
                      "the prior brief was preserved.")
            write_status(run_id=run_id, date=date, state="failed", stage="commit",
                         returncode=69, reason="publication_commit_failure",
                         note="prior brief preserved", started_at=started_at)
            return 69

    _heartbeat("pipeline", 0, time.time() - t_start, "complete")
    write_status(run_id=run_id, date=date, state="complete", stage="pipeline",
                 returncode=0, reason="none", note="validated publication complete",
                 started_at=started_at)
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
