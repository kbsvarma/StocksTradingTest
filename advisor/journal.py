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

Schema v2 (2026-07-01, additive — old rows stay valid):
    view/rejected entries carry: ref_px (code-stamped, never model-written),
    source (factor_long|factor_short|factor_shock|news_loop|insider_cluster|
    revision_leader|macro_thematic|user_suggested|other), p_win (0.50-0.85),
    thesis_tags. type="rejected" journals kill-tested ideas with killed_by —
    ref_px on rejects is what makes counterfactual scoring possible.
    Machine rows (data-only merge in effective(), never clobber type/status):
      type="stamp"           ref_px/ref_ts/ref_src written by --stamp-ref
      type="resolve_pending" hit_level/hit_px/hit_ts written by exit_watcher

CLI:
    python -m advisor.journal --add '<json>'
    python -m advisor.journal --list [--open]
    python -m advisor.journal --resolve ID --status hit_target --note "..."
    python -m advisor.journal --stamp-ref     # ref_px for unstamped view/rejected rows
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
    # rejected ideas are terminal on arrival — they are never "open"
    entry.setdefault("status", "rejected" if entry["type"] == "rejected" else "open")
    # rerun idempotency (2026-07-02: a second same-day pipeline run
    # re-journaled all 10 rejects → double-counted counterfactuals): a
    # rejected idea is one row per (instrument, day)
    if entry["type"] == "rejected":
        today = _now().date().isoformat()
        for e in read_all():
            if e.get("type") == "rejected" \
                    and e.get("instrument") == entry.get("instrument") \
                    and (e.get("ts") or "").startswith(today):
                print(f"journal: rejected '{entry.get('instrument')}' already "
                      f"recorded today as {e.get('id')} — skipping duplicate",
                      file=sys.stderr)
                return e
    with journal_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def append_raw(entry: dict) -> dict:
    """Append a machine row verbatim (no defaults). Used by --stamp-ref and
    exit_watcher's resolve_pending — callers set type/ts explicitly."""
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


# machine-row types merge data-only: they must never clobber the original
# entry's type/ts/status (a stamp on an open view keeps it an open view)
_DATA_ONLY_TYPES = ("stamp", "resolve_pending")


def effective(entries: list[dict] | None = None) -> dict[str, dict]:
    """Latest state per id — resolve entries supersede the original."""
    entries = entries if entries is not None else read_all()
    out: dict[str, dict] = {}
    for e in entries:
        eid = e.get("id", "?")
        if eid in out and e.get("type") in _DATA_ONLY_TYPES:
            upd = {k: v for k, v in e.items() if k not in ("id", "ts", "type", "status")}
            if e.get("type") == "resolve_pending":
                upd["resolve_pending"] = True
            out[eid] = {**out[eid], **upd}
        elif eid in out:
            out[eid] = {**out[eid], **e}
        else:
            out[eid] = dict(e)
    return out


def resolve(eid: str, status: str, note: str = "", fields: dict | None = None) -> dict:
    if eid not in effective():
        raise ValueError(f"unknown journal id {eid}")
    entry = {"id": eid, "ts": _now().isoformat(), "type": "resolve",
             "status": status, "note": note}
    if fields:
        # v2 outcome fields: exit_px, exit_ts, realized_return_pct, realized_r,
        # mae_r, mfe_r, holding_days, spy_return_pct, outcome_tag — id/type/status
        # stay owned by this function
        entry.update({k: v for k, v in fields.items()
                      if k not in ("id", "ts", "type", "status")})
    with journal_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def stamp_refs(verbose: bool = True, max_age_days: int = 2) -> int:
    """Code-stamp ref_px on every view/rejected entry that lacks one.

    Run by the pipeline right after the brief lands — the model never writes
    its own reference price, so calibration/counterfactuals can't be fudged.
    Uses delayed yfinance quotes; source is recorded per the always-label rule.
    Only entries journaled within `max_age_days` are stamped: a reference
    price is only honest near publication — legacy rows stay unstamped.
    """
    raw = read_all()
    origin_type = {}
    for e in raw:
        origin_type.setdefault(e.get("id"), e.get("type"))
    stamped = 0
    for eid, e in effective(raw).items():
        if origin_type.get(eid) not in ("view", "rejected"):
            continue
        if isinstance(e.get("ref_px"), (int, float)) or not e.get("yf_ticker"):
            continue
        try:
            age_days = (_now() - datetime.fromisoformat(e.get("ts", ""))).days
        except (ValueError, TypeError):
            age_days = None
        if age_days is None or age_days > max_age_days:
            if verbose:
                print(f"stamp-ref {eid}: skipped (journaled {age_days}d ago — "
                      f"a late stamp would be dishonest)", file=sys.stderr)
            continue
        try:
            import yfinance as yf
            px = float(yf.Ticker(e["yf_ticker"]).fast_info.last_price)
        except Exception as exc:
            if verbose:
                print(f"stamp-ref {eid} ({e.get('yf_ticker')}): fetch failed — {exc}",
                      file=sys.stderr)
            continue
        append_raw({"id": eid, "ts": _now().isoformat(), "type": "stamp",
                    "ref_px": round(px, 4), "ref_ts": _now().isoformat(),
                    "ref_src": "yfinance delayed ~15min"})
        stamped += 1
        if verbose:
            print(f"stamp-ref {eid} {e.get('yf_ticker')} ref_px={px:.2f}")
    if verbose:
        print(f"stamp-ref: {stamped} entr{'y' if stamped == 1 else 'ies'} stamped")
    return stamped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--resolve")
    ap.add_argument("--status")
    ap.add_argument("--note", default="")
    ap.add_argument("--fields", default="",
                    help="extra JSON merged into the resolve row (v2 outcome fields)")
    ap.add_argument("--stamp-ref", action="store_true")
    ap.add_argument("--lesson",
                    help="append a structured post-mortem lesson (JSON) to lessons.jsonl")
    a = ap.parse_args()

    if a.stamp_ref:
        stamp_refs()
        return 0
    if a.lesson:
        try:
            lesson = json.loads(a.lesson)
        except json.JSONDecodeError as exc:
            print(f"REFUSED: --lesson is not valid JSON: {exc}", file=sys.stderr)
            return 1
        lesson.setdefault("ts", _now().isoformat())
        base = journal_path().parent
        with (base / "lessons.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(lesson) + "\n")
        print(f"lesson recorded for {lesson.get('call_id', '?')}")
        return 0

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
        try:
            fields = json.loads(a.fields) if a.fields else None
        except json.JSONDecodeError as exc:
            print(f"REFUSED: --fields is not valid JSON: {exc}", file=sys.stderr)
            return 1
        resolve(a.resolve, a.status, a.note, fields)
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
