"""macro.json validator — the macro stage's output contract.

CLI: python -m advisor.macro_check <macro.json>   (exit 0 = valid)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def validate(path: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    try:
        d = json.loads(path.read_text())
    except Exception as exc:
        return [f"unparseable JSON: {exc}"], []
    for key in ("overnight", "calendar"):
        v = d.get(key)
        if not isinstance(v, list):
            errs.append(f"missing '{key}' array")
        elif not v:
            warns.append(f"'{key}' is empty — quiet tape or lazy scan?")
    for i, o in enumerate(d.get("overnight") or []):
        if not o.get("fact"):
            errs.append(f"overnight[{i}]: missing fact")
        if not (o.get("url") or "").startswith(("http://", "https://")):
            warns.append(f"overnight[{i}]: no source url")
    for i, c in enumerate(d.get("calendar") or []):
        if not c.get("event"):
            errs.append(f"calendar[{i}]: missing event")
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
