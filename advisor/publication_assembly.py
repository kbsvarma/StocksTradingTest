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


def _deep_update(base: dict, patch: dict) -> dict:
    """Recursively merge `patch` into `base`, preserving untouched nested keys.

    A red-team amendment is a PATCH, not a replacement: it carries only the
    fields it wants to change. Nested blocks (sizing, probability_basis,
    catalyst) must therefore be merged key-by-key, not overwritten.
    """
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], copy.deepcopy(value))
        else:
            base[key] = copy.deepcopy(value)
    return base


def _capital_amended(originals: list, surviving: list) -> bool:
    """Did an amendment change any view's deployed capital?"""
    before = {v.get("decision_key"): (v.get("sizing") or {}).get("capital_usd")
              for v in originals}
    return any((v.get("sizing") or {}).get("capital_usd")
               != before.get(v.get("decision_key")) for v in surviving)


def _recompute_after_capital(views: list) -> None:
    """Restate the derived post-trade exposure across every surviving view.

    Uses brief_check's own helper so there is exactly ONE definition of
    existing open capital and the two cannot drift apart.
    """
    from advisor.brief_check import _existing_open_capital, _finite_number

    keys = {v.get("decision_key") for v in views if v.get("decision_key")}
    try:
        existing = _existing_open_capital(keys)
    except Exception:
        return                      # never block publication on this
    deployed = sum(float((v.get("sizing") or {}).get("capital_usd") or 0)
                   for v in views
                   if _finite_number((v.get("sizing") or {}).get("capital_usd")))
    after = round(existing + deployed, 2)
    for v in views:
        if isinstance(v.get("sizing"), dict):
            v["sizing"]["portfolio_capital_after_usd"] = after


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
            # DEEP merge. `dict.update` is shallow, so an amendment that
            # touches part of a nested block replaces the WHOLE block and
            # silently destroys the untouched keys.
            #
            # 2026-09-04: the red-team amended DELL's sizing to cut quantity
            # 20 -> 15 and sent {quantity, capital_usd, max_loss_usd}. The
            # shallow update dropped portfolio_capital_after_usd,
            # instrument_type, slippage_bps and method, and the production
            # contract then rejected the assembled brief — killing a
            # publication that had already cost $4.19 in model time, over a
            # merge bug rather than anything wrong with the idea.
            #
            # The red-team is right to send only what it changed; the merge
            # has to honour that.
            _deep_update(view, verdict["amended"])
            view["thesis"] = (str(view.get("thesis") or "")
                              + f" [red-team amended: {verdict['reason']}]")
        surviving.append(view)

    # portfolio_capital_after_usd is DERIVED, not a judgement: it is
    # existing open capital plus the capital deployed by this brief. The
    # model asserts it in the draft and brief_check verifies that assertion —
    # a useful integrity check on the model's own arithmetic.
    #
    # But an amendment is applied by CODE, not re-asserted by the model. When
    # the red-team cut DELL's capital_usd 10280 -> 7710 on 2026-09-04 the
    # derived field kept the draft's value and the brief was rejected for not
    # reconciling. Recompute it here — and only when an amendment actually
    # moved capital — so the invariant survives the patch while the model's
    # unamended arithmetic is still checked.
    if _capital_amended(draft.get("views") or [], surviving):
        _recompute_after_capital(surviving)

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
