"""Deterministically merge a validated draft with validated red-team verdicts."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from advisor.brief_check import validate as validate_brief
from advisor.research.redteam_check import validate as validate_redteam
from advisor.watchlist import load as load_watchlist


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def assemble(context_dir: Path) -> Path:
    context_dir = Path(context_dir)
    draft_path = context_dir / "views_draft.json"
    redteam_path = context_dir / "redteam.json"
    errors, _warnings = validate_redteam(redteam_path, draft_path)
    if errors:
        raise ValueError("invalid red-team artifact: " + "; ".join(errors[:5]))
    draft = _object(draft_path)
    redteam = _object(redteam_path)
    verdicts = {row["instrument"]: row for row in redteam["verdicts"]}

    surviving, killed = [], []
    for original in draft.get("views") or []:
        view = copy.deepcopy(original)
        verdict = verdicts[view["instrument"]]
        if verdict["verdict"] == "kill":
            killed.append({
                "idea": view["instrument"],
                "instrument": view["instrument"],
                "yf_ticker": view.get("yf_ticker"),
                "direction": view.get("direction"),
                "source": view.get("source"),
                "killed_by": f"red-team: {verdict['reason']}",
            })
            continue
        if verdict["verdict"] == "amend":
            view.update(copy.deepcopy(verdict["amended"]))
            view["thesis"] = (str(view.get("thesis") or "")
                              + f" [red-team amended: {verdict['reason']}]")
        surviving.append(view)

    calendar = {}
    macro = {}
    try:
        calendar = _object(context_dir / "calendar.json")
    except Exception:
        pass
    try:
        macro = _object(context_dir / "macro.json")
    except Exception:
        pass
    watch = load_watchlist()
    triggered = [row for row in watch.values() if row.get("triggered")]
    parked = sum(1 for row in watch.values() if row.get("state") == "watchlist")
    result = {
        "schema_version": 3,
        "regime_summary": draft.get("regime_summary", ""),
        "portfolio_context": copy.deepcopy(draft.get("portfolio_context") or {}),
        "views": surviving,
        "rejected": copy.deepcopy(draft.get("rejected") or []) + killed,
        "redteam": "applied",
        "calendar": copy.deepcopy(calendar.get("events") or []),
        "watchlist": {"triggered": triggered, "n_parked": parked},
        "narrative_delta": copy.deepcopy(macro.get("themes_delta")),
    }
    out = context_dir / "brief.pending.json"
    tmp = out.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, out)
    errors, _warnings = validate_brief(out)
    if errors:
        out.unlink(missing_ok=True)
        raise ValueError("assembled brief invalid: " + "; ".join(errors[:5]))
    return out
