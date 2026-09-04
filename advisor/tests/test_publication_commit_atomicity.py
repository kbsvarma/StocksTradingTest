"""A notification failure must not half-commit a publication.

2026-09-04, seen for real. `commit()` writes the journal, watchlist and
ref-stamps, THEN sent a Telegram message and raised if it failed. The raise
left the decision recorded but the brief never promoted — the terminal showed
"NO BRIEF TODAY" over research that existed, was valid, and had already cost
$4.19 to produce.

Two things are wrong with gating on it: the ordering makes the raise
destructive, and a push notification is a courtesy — the dashboard is the
delivery. The failure is recorded on the receipt instead.
"""
import json
import shutil
from pathlib import Path

import pytest

from advisor import publication_commit as pc

FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_e2e"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from advisor.publication_assembly import assemble
    d = tmp_path / "2026-09-04"
    d.mkdir()
    for f in FIXTURE.iterdir():
        shutil.copy(f, d / f.name)
    import advisor.brief_check as bc
    monkeypatch.setattr(bc, "_existing_open_capital", lambda keys: 0.0)
    assemble(d)
    # isolate every deterministic side effect
    monkeypatch.setattr(pc, "add_batch",
                        lambda entries: [{**e, "id": f"J-{i}"}
                                         for i, e in enumerate(entries)])
    monkeypatch.setattr(pc, "set_state", lambda *a, **k: None)
    monkeypatch.setattr(pc, "stamp_refs", lambda: None)
    return d


def test_failed_notification_still_publishes(ctx, monkeypatch):
    monkeypatch.setattr(pc.telegram_io, "muted", lambda: False)
    monkeypatch.setattr(pc.telegram_io, "send", lambda *a, **k: False)
    receipt = pc.commit(ctx)
    assert (ctx / "brief.json").exists(), "brief was not promoted"
    assert (ctx / "brief.md").exists()
    assert receipt["notification_status"] == "failed"
    assert receipt["notified"] is False
    assert "published regardless" in receipt["notification_note"]


def test_successful_notification_is_recorded_as_delivered(ctx, monkeypatch):
    monkeypatch.setattr(pc.telegram_io, "muted", lambda: False)
    monkeypatch.setattr(pc.telegram_io, "send", lambda *a, **k: True)
    receipt = pc.commit(ctx)
    assert receipt["notification_status"] == "delivered"
    assert receipt["notified"] is True
    assert receipt["notification_note"] is None


def test_muted_channel_publishes_and_is_labelled(ctx, monkeypatch):
    monkeypatch.setattr(pc.telegram_io, "muted", lambda: True)
    monkeypatch.setattr(pc.telegram_io, "send", lambda *a, **k: True)
    receipt = pc.commit(ctx)
    assert receipt["notification_status"] == "muted"
    assert (ctx / "brief.json").exists()


def test_notify_false_skips_delivery_entirely(ctx, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("send must not be called when notify=False")
    monkeypatch.setattr(pc.telegram_io, "send", boom)
    receipt = pc.commit(ctx, notify=False)
    assert receipt["notification_status"] == "disabled"
    assert (ctx / "brief.json").exists()


def test_invalid_brief_is_still_refused_before_any_side_effect(ctx, monkeypatch):
    """Fail-closed on VALIDATION must survive this change."""
    pending = ctx / "brief.pending.json"
    doc = json.loads(pending.read_text())
    doc["views"][0]["sizing"].pop("method")
    pending.write_text(json.dumps(doc))
    called = []
    monkeypatch.setattr(pc, "add_batch", lambda e: called.append(e) or [])
    with pytest.raises(ValueError, match="deterministic validation"):
        pc.commit(ctx, notify=False)
    assert not called, "journal was written for an invalid brief"
    assert not (ctx / "brief.json").exists()
