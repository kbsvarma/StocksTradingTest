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
      type="entry_observed"  entry_observed_px/ts/src written by exit_watcher
      type="resolve_pending" hit_level/hit_px/hit_ts written by exit_watcher

CLI:
    python -m advisor.journal --add '<json>'
    python -m advisor.journal --list [--open]
    python -m advisor.journal --resolve ID --status hit_target --note "..."
    python -m advisor.journal --stamp-ref     # ref_px for unstamped view/rejected rows
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
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


@contextmanager
def _write_lock():
    """Serialize read-check-append transactions across advisor processes."""
    lock_path = journal_path().with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _append_unlocked(entry: dict) -> dict:
    with journal_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return entry


def _prepare_add(entry: dict, raw: list[dict], now: datetime) -> tuple[dict, bool]:
    """Validate/normalize one origin against a transaction snapshot.

    Returns ``(entry, should_append)``. Existing idempotent decisions are
    returned without being appended; any conflict aborts the whole batch.
    """
    entry = dict(entry)
    entry.setdefault("ts", now.isoformat())
    entry.setdefault("type", "view")
    entry.setdefault("status", "rejected" if entry["type"] == "rejected" else "open")
    key = entry.get("decision_key")
    if key:
        matches = [e for e in raw if e.get("decision_key") == key
                   and e.get("type") in _ORIGIN_TYPES]
        if matches:
            existing = matches[0]
            comparable = lambda e: {k: v for k, v in e.items()
                                    if k not in ("id", "ts")}
            if comparable(existing) != comparable(entry):
                raise ValueError(f"decision_key conflict: {key} already records "
                                 f"a different decision")
            return existing, False
    if entry["type"] == "rejected":
        day = str(entry["ts"])[:10]
        for existing in raw:
            if existing.get("type") == "rejected" \
                    and existing.get("instrument") == entry.get("instrument") \
                    and (existing.get("ts") or "").startswith(day):
                return existing, False
    existing_ids = {e.get("id") for e in raw if e.get("id")}
    if entry.get("id"):
        if entry["id"] in existing_ids:
            raise ValueError(f"journal id collision: {entry['id']} already exists")
    else:
        while True:
            eid = f"V-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:12].upper()}"
            if eid not in existing_ids:
                entry["id"] = eid
                break
    return entry, True


def add_batch(entries: list[dict]) -> list[dict]:
    """Atomically validate and append a publication's journal origins."""
    with _write_lock():
        raw = read_all()
        prepared: list[dict] = []
        pending: list[dict] = []
        now = _now()
        for candidate in entries:
            result, should_append = _prepare_add(candidate, raw + pending, now)
            prepared.append(result)
            if should_append:
                pending.append(result)
        if pending:
            with journal_path().open("a", encoding="utf-8") as handle:
                for entry in pending:
                    handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return prepared


def add(entry: dict) -> dict:
    return add_batch([entry])[0]


def append_raw(entry: dict) -> dict:
    """Append a machine row verbatim (no defaults). Used by --stamp-ref and
    exit_watcher's resolve_pending — callers set type/ts explicitly."""
    entry = dict(entry)
    if entry.get("type") not in _DATA_ONLY_TYPES:
        raise ValueError(f"append_raw only accepts machine rows: {_DATA_ONLY_TYPES}")
    with _write_lock():
        raw = read_all()
        if entry.get("id") not in {e.get("id") for e in raw}:
            raise ValueError(f"machine row targets unknown journal id {entry.get('id')}")
        return _append_unlocked(entry)


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
_DATA_ONLY_TYPES = ("stamp", "entry_observed", "resolve_pending")
_ORIGIN_TYPES = ("view", "rejected", "proposal", "execution")


def integrity_report(entries: list[dict] | None = None) -> dict:
    entries = entries if entries is not None else read_all()
    origins: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("type") in _ORIGIN_TYPES:
            origins.setdefault(e.get("id", "?"), []).append(e)
    collisions = {
        eid: [{"type": e.get("type"), "instrument": e.get("instrument"),
               "ts": e.get("ts")} for e in rows]
        for eid, rows in origins.items() if len(rows) > 1
    }
    return {"rows": len(entries), "origin_ids": len(origins),
            "collisions": collisions, "ok": not collisions}


def _collision_replacement(e: dict, used: set[str]) -> str:
    day = str(e.get("ts") or _now().isoformat())[:10].replace("-", "")
    seed = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()
    suffix = hashlib.sha256(seed).hexdigest()[:12].upper()
    candidate = f"V-{day}-{suffix}"
    n = 0
    while candidate in used:
        n += 1
        candidate = f"V-{day}-{suffix[:10]}{n:02X}"
    return candidate


def repair_collisions() -> dict:
    """Rewrite only colliding IDs, preserving row order and an immutable backup.

    Once a second origin appears for an ID, later update rows are routed to that
    newest origin. This matches append-only journal semantics and repairs the two
    collision shapes observed in the live journal.
    """
    with _write_lock():
        path = journal_path()
        raw = read_all()
        before = integrity_report(raw)
        if before["ok"]:
            return {**before, "changed": 0, "backup": None, "mapping": []}
        used: set[str] = set()
        active: dict[str, str] = {}
        out, mapping = [], []
        for row in raw:
            e = dict(row)
            old = e.get("id", "?")
            if e.get("type") in _ORIGIN_TYPES:
                if old in active:
                    new = _collision_replacement(e, used)
                    mapping.append({"old_id": old, "new_id": new,
                                    "instrument": e.get("instrument"),
                                    "ts": e.get("ts")})
                    active[old] = new
                    e["id"] = new
                else:
                    active[old] = old
                used.add(e["id"])
            elif old in active:
                e["id"] = active[old]
            out.append(e)
        after = integrity_report(out)
        if not after["ok"] or len(out) != len(raw):
            raise RuntimeError(f"collision repair failed validation: {after}")
        stamp = _now().strftime("%Y%m%dT%H%M%S")
        backup = path.with_name(f"{path.name}.pre_collision_repair.{stamp}")
        backup.write_bytes(path.read_bytes())
        tmp = path.with_suffix(".jsonl.repair.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in out:
                f.write(json.dumps(e) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        manifest = path.with_name(f"journal_repair_{stamp}.json")
        manifest.write_text(json.dumps({"before": before, "after": after,
                                        "mapping": mapping,
                                        "backup": str(backup)}, indent=2))
        return {**after, "changed": len(mapping), "backup": str(backup),
                "manifest": str(manifest), "mapping": mapping}


def effective(entries: list[dict] | None = None) -> dict[str, dict]:
    """Latest state per id — resolve entries supersede the original."""
    entries = entries if entries is not None else read_all()
    out: dict[str, dict] = {}
    origin_ts: dict[str, str] = {}
    for e in entries:
        eid = e.get("id", "?")
        if eid not in origin_ts and e.get("type") in _ORIGIN_TYPES:
            origin_ts[eid] = e.get("ts", "")
        if eid in out and e.get("type") in _DATA_ONLY_TYPES:
            upd = {k: v for k, v in e.items() if k not in ("id", "ts", "type", "status")}
            if e.get("type") == "stamp":
                try:
                    age = (datetime.fromisoformat(e.get("ts", ""))
                           - datetime.fromisoformat(origin_ts[eid])).total_seconds()
                except (KeyError, TypeError, ValueError):
                    age = float("inf")
                if age < 0 or age > 2 * 86400:
                    upd = {"ref_invalid_reason": "stamp outside 2-day origin window"}
            if e.get("type") == "resolve_pending":
                upd["resolve_pending"] = True
            if e.get("type") == "entry_observed":
                upd["entry_observed"] = True
            out[eid] = {**out[eid], **upd}
        elif eid in out:
            out[eid] = {**out[eid], **e}
        else:
            out[eid] = dict(e)
    return out


def resolve(eid: str, status: str, note: str = "", fields: dict | None = None) -> dict:
    with _write_lock():
        if eid not in effective(read_all()):
            raise ValueError(f"unknown journal id {eid}")
        entry = {"id": eid, "ts": _now().isoformat(), "type": "resolve",
                 "status": status, "note": note}
        if fields:
            # v2 outcome fields: exit_px, exit_ts, realized_return_pct, realized_r,
            # mae_r, mfe_r, holding_days, spy_return_pct, outcome_tag — id/type/status
            # stay owned by this function
            entry.update({k: v for k, v in fields.items()
                          if k not in ("id", "ts", "type", "status")})
        return _append_unlocked(entry)


def stamp_refs(verbose: bool = True, max_age_days: int = 2) -> int:
    """Code-stamp ref_px on every view/rejected entry that lacks one.

    Run by the pipeline right after the brief lands — the model never writes
    its own reference price, so calibration/counterfactuals can't be fudged.
    Uses delayed yfinance quotes; source is recorded per the always-label rule.
    Only entries journaled within `max_age_days` are stamped: a reference
    price is only honest near publication — legacy rows stay unstamped.
    """
    raw = read_all()
    origins = {}
    for e in raw:
        origins.setdefault(e.get("id"), e)
    stamped = 0
    for eid, e in effective(raw).items():
        origin = origins.get(eid, {})
        if origin.get("type") not in ("view", "rejected"):
            continue
        if isinstance(e.get("ref_px"), (int, float)) or not e.get("yf_ticker"):
            continue
        try:
            age_days = (_now() - datetime.fromisoformat(origin.get("ts", ""))).days
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
    ap.add_argument("--audit-integrity", action="store_true")
    ap.add_argument("--repair-collisions", action="store_true")
    ap.add_argument("--lesson",
                    help="append a structured post-mortem lesson (JSON) to lessons.jsonl")
    a = ap.parse_args()

    if a.audit_integrity:
        report = integrity_report()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if a.repair_collisions:
        print(json.dumps(repair_collisions(), indent=2))
        return 0

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
