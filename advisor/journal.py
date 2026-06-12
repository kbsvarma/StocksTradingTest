"""Decision journal — append-only JSONL record of every advisor call.

Every recommendation in a brief gets logged here VERBATIM (entry, target,
stop, time stop) so the weekly review scores calls against what was actually
said — no revisionism. Executions and resolutions append too; nothing is
ever rewritten, only superseded by a later `resolve` entry with the same id.

Entry shape:
    {"id": "V-20260612-AB", "ts": ..., "type": "view|proposal|execution|resolve",
     "instrument": "XLE", "direction": "long", "conviction": "high",
     "thesis": "...", "entry": "92-93", "target": "101", "stop": "88.40",
     "time_stop": "2026-07-10", "status": "open|hit_target|stopped|time_stop|closed",
     "note": "..."}

CLI:
    python -m advisor.journal --add '<json>'
    python -m advisor.journal --list [--open]
    python -m advisor.journal --resolve ID --status hit_target --note "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent


def journal_path() -> Path:
    env = os.environ.get("ADVISOR_DATA_DIR")
    base = Path(env) if env else REPO_ROOT / "advisor" / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "decision_journal.jsonl"


def _now() -> datetime:
    return datetime.now(ET)


def add(entry: dict) -> dict:
    entry.setdefault("id", f"V-{_now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:2].upper()}")
    entry.setdefault("ts", _now().isoformat())
    entry.setdefault("type", "view")
    entry.setdefault("status", "open")
    with journal_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all() -> list[dict]:
    p = journal_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def effective(entries: list[dict] | None = None) -> dict[str, dict]:
    """Latest state per id — resolve entries supersede the original."""
    entries = entries if entries is not None else read_all()
    out: dict[str, dict] = {}
    for e in entries:
        eid = e.get("id", "?")
        if eid in out:
            out[eid] = {**out[eid], **e}
        else:
            out[eid] = dict(e)
    return out


def resolve(eid: str, status: str, note: str = "") -> dict:
    if eid not in effective():
        raise ValueError(f"unknown journal id {eid}")
    entry = {"id": eid, "ts": _now().isoformat(), "type": "resolve",
             "status": status, "note": note}
    with journal_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--resolve")
    ap.add_argument("--status")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    if a.add:
        try:
            entry = add(json.loads(a.add))
        except json.JSONDecodeError as exc:
            print(f"REFUSED: --add is not valid JSON: {exc}", file=sys.stderr)
            return 1
        print(f"journaled {entry['id']}")
        return 0
    if a.resolve:
        if not a.status:
            ap.error("--resolve requires --status")
        resolve(a.resolve, a.status, a.note)
        print(f"resolved {a.resolve} → {a.status}")
        return 0
    if a.list:
        for eid, e in effective().items():
            if a.open and e.get("status") != "open":
                continue
            print(f"{eid}  {e.get('status','?'):<11} {e.get('type','?'):<9} "
                  f"{e.get('instrument','?'):<8} {e.get('direction','')} "
                  f"entry={e.get('entry','—')} tgt={e.get('target','—')} "
                  f"stop={e.get('stop','—')}  {e.get('thesis','')[:60]}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
