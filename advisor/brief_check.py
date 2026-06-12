"""brief.json validator — run by the morning session AFTER writing brief.json.

Catches malformed structured briefs before the terminal renders them and
before the session claims success. Zero deps, exit 0 = valid.

Checks per view: required keys, numeric levels coherent with direction
(long: stop < target; short: stop > target), evidence URLs http(s),
conviction in {high, medium}. Warns (non-fatal) on missing yf_ticker /
numeric levels — those views can't be exit-watched and must say why.

CLI: python -m advisor.brief_check advisor/data/context/<date>/brief.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED_VIEW_KEYS = ("instrument", "direction", "conviction", "thesis",
                      "entry", "target", "stop")


def validate(path: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    try:
        d = json.loads(path.read_text())
    except Exception as exc:
        return [f"unparseable JSON: {exc}"], []
    views = d.get("views")
    if views is None:
        errs.append("missing 'views' (use [] for a no-ideas day)")
        views = []
    for i, v in enumerate(views):
        tag = f"views[{i}] ({v.get('instrument', '?')})"
        for k in REQUIRED_VIEW_KEYS:
            if not v.get(k):
                errs.append(f"{tag}: missing '{k}'")
        if v.get("conviction") not in ("high", "medium"):
            errs.append(f"{tag}: conviction must be high|medium")
        tp, sp = v.get("target_px"), v.get("stop_px")
        if isinstance(tp, (int, float)) and isinstance(sp, (int, float)):
            long_ = (v.get("direction") or "long").lower() != "short"
            if long_ and not sp < tp:
                errs.append(f"{tag}: long but stop_px {sp} >= target_px {tp}")
            if not long_ and not sp > tp:
                errs.append(f"{tag}: short but stop_px {sp} <= target_px {tp}")
        else:
            warns.append(f"{tag}: no numeric target_px/stop_px — exit watcher "
                         f"cannot arm; acceptable only with stated reason")
        if not v.get("yf_ticker"):
            warns.append(f"{tag}: no yf_ticker — unwatchable")
        for j, ev in enumerate(v.get("evidence", [])):
            url = ev.get("url", "")
            if url and not url.startswith(("http://", "https://")):
                errs.append(f"{tag}: evidence[{j}] url not http(s): {url[:40]}")
            if not ev.get("claim"):
                errs.append(f"{tag}: evidence[{j}] missing claim")
        if not v.get("evidence"):
            warns.append(f"{tag}: no evidence array — terminal card will be bare")
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
    print(f"brief_check: {'INVALID — fix before sending' if errs else 'valid'} "
          f"({len(errs)} errors, {len(warns)} warnings)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
