"""redteam.json validator — every draft view must receive a coherent verdict.

CLI: python -m advisor.research.redteam_check <redteam.json> <views_draft.json>
Exit 0 = valid. Zero deps, same pattern as brief_check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

VERDICTS = ("survive", "kill", "amend")
AMENDABLE = ("stop_px", "target_px", "entry_px_low", "entry_px_high",
             "entry", "target", "stop", "time_stop", "sizing", "p_win",
             "reward_risk", "expected_value_r")


def validate(redteam_path: Path, draft_path: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    try:
        rt = json.loads(redteam_path.read_text())
    except Exception as exc:
        return [f"redteam.json unparseable: {exc}"], []
    try:
        draft = json.loads(draft_path.read_text())
    except Exception as exc:
        return [f"views_draft.json unparseable: {exc}"], []

    verdicts = rt.get("verdicts")
    if not isinstance(verdicts, list):
        return ["missing 'verdicts' array"], []

    drafted = {v.get("instrument") for v in draft.get("views", [])}
    seen = set()
    for i, v in enumerate(verdicts):
        tag = f"verdicts[{i}] ({v.get('instrument', '?')})"
        inst = v.get("instrument")
        if not inst:
            errs.append(f"{tag}: missing instrument")
            continue
        if inst in seen:
            errs.append(f"{tag}: duplicate verdict for '{inst}'")
        seen.add(inst)
        if inst not in drafted:
            warns.append(f"{tag}: instrument not in views_draft (stale name?)")
        if v.get("verdict") not in VERDICTS:
            errs.append(f"{tag}: verdict must be one of {VERDICTS}")
        if v.get("verdict") in ("kill", "amend") and not v.get("reason"):
            errs.append(f"{tag}: {v.get('verdict')} requires a reason")
        if v.get("verdict") == "amend":
            am = v.get("amended")
            if not isinstance(am, dict) or not am:
                errs.append(f"{tag}: amend requires non-empty 'amended'")
            else:
                for k in am:
                    if k not in AMENDABLE:
                        errs.append(f"{tag}: amended field '{k}' not allowed "
                                    f"(allowed: {AMENDABLE})")
        if not v.get("checks"):
            warns.append(f"{tag}: no 'checks' detail — thin audit trail")
    missing = drafted - seen
    for inst in missing:
        errs.append(f"no verdict for drafted view '{inst}' — publish is blocked")
    return errs, warns


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    errs, warns = validate(Path(sys.argv[1]), Path(sys.argv[2]))
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"ERROR {e}")
    print(f"redteam_check: {'INVALID' if errs else 'valid'} "
          f"({len(errs)} errors, {len(warns)} warnings)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
