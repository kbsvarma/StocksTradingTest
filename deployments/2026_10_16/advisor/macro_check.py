"""macro.json validator — the macro stage's output contract.

CLI: python -m advisor.macro_check <macro.json>   (exit 0 = valid)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


def _https(value) -> bool:
    try:
        p = urlsplit(value)
        return p.scheme == "https" and bool(p.netloc)
    except (TypeError, ValueError):
        return False


def _iso(value) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def validate(path: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    try:
        d = json.loads(path.read_text())
    except Exception as exc:
        return [f"unparseable JSON: {exc}"], []
    if not _iso(d.get("as_of")):
        errs.append("as_of must be an ISO-8601 timestamp")
    for key in ("overnight", "calendar"):
        v = d.get(key)
        if not isinstance(v, list):
            errs.append(f"missing '{key}' array")
        elif not v:
            warns.append(f"'{key}' is empty — quiet tape or lazy scan?")
    for i, o in enumerate(d.get("overnight") or []):
        if not o.get("fact"):
            errs.append(f"overnight[{i}]: missing fact")
        if not _https(o.get("url")):
            errs.append(f"overnight[{i}]: source must be an absolute HTTPS URL")
        if not _iso(o.get("ts")):
            errs.append(f"overnight[{i}]: ts must be an ISO-8601 timestamp")
        if not isinstance(o.get("primary"), bool):
            errs.append(f"overnight[{i}]: primary must be boolean")
    for i, c in enumerate(d.get("calendar") or []):
        if not c.get("event"):
            errs.append(f"calendar[{i}]: missing event")
        if not _https(c.get("url")):
            errs.append(f"calendar[{i}]: official/source HTTPS URL required")
        if not _iso(c.get("retrieved")):
            errs.append(f"calendar[{i}]: retrieved must be ISO-8601")
        if not isinstance(c.get("primary"), bool):
            errs.append(f"calendar[{i}]: primary must be boolean")
    if d.get("calendar") and not any(c.get("primary") is True for c in d["calendar"]):
        errs.append("calendar: at least one primary-source event required")
    for i, a in enumerate(d.get("anomalies") or []):
        if not a.get("asset") or not a.get("why"):
            errs.append(f"anomalies[{i}]: asset and why required")
        if not _https(a.get("url")):
            errs.append(f"anomalies[{i}]: source must be an absolute HTTPS URL")
    if not d.get("themes_updated"):
        warns.append("themes_updated not true — narrative memory went stale today")
    return errs, warns


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    errs, warns = validate(Path(sys.argv[1]))
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"ERROR {e}")
    print(f"macro_check: {'INVALID' if errs else 'valid'} "
          f"({len(errs)} errors, {len(warns)} warnings)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
