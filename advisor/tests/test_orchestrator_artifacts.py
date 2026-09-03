from __future__ import annotations

from advisor.orchestrator import _quarantine_changed, _signature


def test_failed_stage_quarantines_only_changed_output(tmp_path):
    old = tmp_path / "unrelated.json"
    changed = tmp_path / "views_draft.json"
    old.write_text("known good")
    before = {old: _signature(old), changed: None}
    changed.write_text('{"partial":')
    moved = _quarantine_changed("synthesis", tmp_path, before)
    assert old.read_text() == "known good"
    assert not changed.exists()
    assert len(moved) == 1 and moved[0].read_text() == '{"partial":'
