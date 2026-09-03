from __future__ import annotations

import json

import pytest

from advisor import publication_commit as pc


def _pending(tmp_path):
    brief = {
        "schema_version": 3,
        "redteam": "applied",
        "portfolio_context": {"status": "verified"},
        "views": [{"instrument": "XLE", "yf_ticker": "XLE",
                   "source": "macro_thematic"}],
        "rejected": [{"idea": "ABC long", "yf_ticker": "ABC",
                      "killed_by": "evidence"}],
    }
    (tmp_path / "brief.pending.json").write_text(json.dumps(brief))
    return brief


def test_commit_promotes_only_after_deterministic_effects(tmp_path, monkeypatch):
    _pending(tmp_path)
    calls = []
    monkeypatch.setattr(pc, "validate", lambda _path: ([], []))
    monkeypatch.setattr(pc, "add_batch", lambda rows: [
        {**row, "id": f"id-{i}"} for i, row in enumerate(rows)])
    monkeypatch.setattr(pc, "set_state", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(pc, "stamp_refs", lambda: calls.append("stamp"))
    monkeypatch.setattr(pc.telegram_io, "send", lambda text: calls.append(text) or True)
    receipt = pc.commit(tmp_path)
    assert (tmp_path / "brief.json").exists()
    assert (tmp_path / "brief.md").exists()
    assert not (tmp_path / "brief.pending.json").exists()
    assert receipt["views"] == 1 and len(receipt["journal_ids"]) == 2
    assert calls[-1].startswith("DAILY RESEARCH BRIEF")
    assert calls[-1].endswith("no order was placed")


def test_invalid_pending_artifact_has_zero_effects(tmp_path, monkeypatch):
    _pending(tmp_path)
    called = []
    monkeypatch.setattr(pc, "validate", lambda _path: (["bad"], []))
    monkeypatch.setattr(pc, "add_batch", lambda rows: called.append(rows))
    with pytest.raises(ValueError, match="deterministic validation"):
        pc.commit(tmp_path)
    assert not called
    assert (tmp_path / "brief.pending.json").exists()
    assert not (tmp_path / "brief.json").exists()
