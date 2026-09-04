"""Deterministic post-validation commit for a generated research brief.

The model may write candidate publication artifacts, but it cannot mutate the
journal, watchlist, notification channel, or proposal queue. This module owns
those effects and is called only after ``brief_check`` succeeds.
"""
from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from advisor.brief_check import validate
from advisor.journal import add_batch, stamp_refs
from advisor import telegram_io
from advisor.watchlist import set_state

ET = ZoneInfo("America/New_York")


def _load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def journal_entries(brief: dict) -> list[dict]:
    """Build the only journal origins permitted from a validated brief."""
    portfolio_status = (brief.get("portfolio_context") or {}).get("status")
    entries: list[dict] = []
    for view in brief.get("views") or []:
        entries.append({**view, "type": "view",
                        "portfolio_context_status": portfolio_status})
    for rejected in brief.get("rejected") or []:
        ticker = rejected.get("yf_ticker")
        if not ticker:
            continue
        entries.append({**rejected, "type": "rejected",
                        "instrument": rejected.get("instrument")
                                      or rejected.get("idea")})
    return entries


def _line(value: object, limit: int = 260) -> str:
    return " ".join(str(value or "").split())[:limit]


def render_markdown(brief: dict, publication_date: str) -> str:
    """Render the notification from validated fields; never trust parallel prose."""
    portfolio = brief.get("portfolio_context") or {}
    lines = [f"DAILY RESEARCH BRIEF — {publication_date}",
             f"① CONTEXT  portfolio {portfolio.get('status', 'unavailable')} · "
             "risk figures are hypothetical, not personalized sizing",
             f"② MARKET   {_line(brief.get('regime_summary'), 360)}",
             "③ RESEARCH"]
    views = brief.get("views") or []
    if not views:
        lines.append("No research view survived the evidence and red-team gates.")
    for view in views:
        probability = view.get("probability_basis") or {}
        p_label = ("CAL" if probability.get("calibrated") is True else "UNCAL")
        sizing = view.get("sizing") or {}
        lines.extend([
            f"{_line(view.get('instrument'), 16)} · "
            f"{_line(view.get('direction'), 8).upper()} · "
            f"{_line(view.get('recommendation_class'), 24).upper()} · "
            f"p={view.get('p_win', '—')} {p_label}",
            f"THESIS  {_line(view.get('thesis'), 420)}",
            f"SCENARIO entry {_line(view.get('entry'), 60)} · target "
            f"{_line(view.get('target'), 60)} · invalidation {_line(view.get('stop'), 60)} "
            f"· time stop {_line(view.get('time_stop'), 20)}",
            f"RISK     hypothetical capital ${sizing.get('capital_usd', '—')} · "
            f"max modeled loss ${sizing.get('max_loss_usd', '—')} · "
            f"R:R {view.get('reward_risk', '—')}",
        ])
        for evidence in (view.get("evidence") or [])[:2]:
            host = urlsplit(str(evidence.get("url") or "")).hostname or "source"
            lines.append(f"EVIDENCE [{host}] {_line(evidence.get('claim'), 220)}")
        disconfirmers = view.get("disconfirmers") or []
        if disconfirmers:
            lines.append("WATCH    " + " · ".join(_line(x, 120) for x in disconfirmers[:2]))
    rejected = brief.get("rejected") or []
    if rejected:
        lines.append("④ KILLED")
        for item in rejected[:6]:
            lines.append(f"{_line(item.get('idea') or item.get('instrument'), 60)} → "
                         f"{_line(item.get('killed_by'), 170)}")
    calendar = brief.get("calendar") or []
    if calendar:
        lines.append("⑤ CALENDAR")
        for event in calendar[:5]:
            lines.append(f"{_line(event.get('date'), 20)} · {_line(event.get('event'), 180)}")
    footer = "Research ideas, not personalized advice · no order was placed"
    body = "\n".join(lines)
    if len(body) + len(footer) + 1 > 3500:
        body = body[:3500 - len(footer) - 3].rstrip() + "…"
    return body + "\n" + footer


def commit(context_dir: Path, *, notify: bool = True) -> dict:
    """Commit one already-generated publication or raise without success status."""
    context_dir = Path(context_dir)
    brief_json = context_dir / "brief.pending.json"
    errors, _warnings = validate(brief_json)
    if errors:
        raise ValueError("brief failed deterministic validation: " + "; ".join(errors[:5]))
    brief = _load_object(brief_json)
    if brief.get("redteam") != "applied":
        raise ValueError("publication is not bound to an applied red-team artifact")
    message = render_markdown(brief, context_dir.name)

    committed = add_batch(journal_entries(brief))
    view_rows = [row for row in committed if row.get("type") == "view"]
    for row in view_rows:
        ticker = str(row.get("instrument") or row.get("yf_ticker") or "").upper()
        if not ticker:
            raise ValueError("journaled view has no watchlist ticker")
        set_state(ticker, "active_view", by="publication_commit",
                  reason="validated brief committed", journal_id=row["id"],
                  source=str(row.get("source") or ""))

    stamp_refs()
    # NOTIFICATION IS NOT A GATE ON PUBLICATION.
    #
    # This used to raise, which is wrong twice over. The journal, watchlist
    # and ref-stamps are already written by the time we get here, so a raise
    # leaves a HALF-COMMITTED state: the decision is recorded but the brief
    # is never promoted, and the terminal shows "NO BRIEF TODAY" over research
    # that exists and is valid. Seen for real on 2026-09-04.
    #
    # And the brief is the deliverable — the dashboard is the delivery. A push
    # notification is a courtesy. Withholding published research because a
    # chat message did not go out is the wrong trade. The failure is recorded
    # on the receipt so it stays visible.
    notification_status = "disabled"
    if notify:
        notification_status = "muted" if telegram_io.muted() else "delivered"
        if not telegram_io.send(message):
            notification_status = "failed"

    receipt = {
        "schema_version": 1,
        "committed_at": datetime.now(ET).isoformat(),
        "brief_sha256": hashlib.sha256(brief_json.read_bytes()).hexdigest(),
        "journal_ids": [row.get("id") for row in committed],
        "views": len(view_rows),
        "rejected": len(committed) - len(view_rows),
        "notification_status": notification_status,
        "notified": notification_status == "delivered",
        "notification_note": (
            "delivery failed; the brief is published regardless — the journal "
            "was already committed and the dashboard is the delivery"
            if notification_status == "failed" else None),
    }
    # Promotion occurs only after every deterministic effect above succeeds.
    rendered_tmp = context_dir / f"brief.md.tmp.{os.getpid()}"
    rendered_tmp.write_text(message + "\n", encoding="utf-8")
    os.replace(brief_json, context_dir / "brief.json")
    os.replace(rendered_tmp, context_dir / "brief.md")
    path = context_dir / "publication_commit.json"
    tmp = path.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return receipt
