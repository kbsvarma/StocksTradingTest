from __future__ import annotations

import json

from advisor import publication_assembly as pa
from advisor.research.redteam_check import validate as validate_redteam


def _write(path, value):
    path.write_text(json.dumps(value))


def test_zero_view_assembly_is_deterministic(tmp_path, monkeypatch):
    _write(tmp_path / "views_draft.json", {
        "schema_version": 3, "regime_summary": "risk neutral",
        "portfolio_context": {"status": "unavailable"},
        "views": [], "rejected": [{"idea": "XYZ", "killed_by": "no edge"}],
    })
    _write(tmp_path / "redteam.json", {"verdicts": []})
    _write(tmp_path / "calendar.json", {"events": [
        {"date": "2026-09-03", "event": "ISM"}]})
    _write(tmp_path / "macro.json", {"themes_delta": "rates firmer"})
    monkeypatch.setattr(pa, "load_watchlist", lambda: {
        "ABC": {"state": "watchlist", "triggered": {"ts": "now"}}})
    out = pa.assemble(tmp_path)
    result = json.loads(out.read_text())
    assert result["views"] == []
    assert result["redteam"] == "applied"
    assert result["calendar"][0]["event"] == "ISM"
    assert result["watchlist"]["n_parked"] == 1


def test_killed_view_cannot_survive_assembly(tmp_path, monkeypatch):
    _write(tmp_path / "views_draft.json", {
        "schema_version": 3, "regime_summary": "x",
        "portfolio_context": {"status": "unavailable"},
        "views": [{"instrument": "XYZ", "yf_ticker": "XYZ",
                   "direction": "long", "source": "other"}],
        "rejected": [],
    })
    _write(tmp_path / "redteam.json", {"verdicts": [{
        "instrument": "XYZ", "verdict": "kill", "reason": "weak evidence",
        "checks": {"evidence_audit": "fail"}}]})
    monkeypatch.setattr(pa, "load_watchlist", lambda: {})
    result = json.loads(pa.assemble(tmp_path).read_text())
    assert result["views"] == []
    assert result["rejected"][0]["killed_by"] == "red-team: weak evidence"


def test_duplicate_redteam_verdict_is_invalid(tmp_path):
    draft = tmp_path / "views_draft.json"
    redteam = tmp_path / "redteam.json"
    _write(draft, {"views": [{"instrument": "XYZ"}]})
    row = {"instrument": "XYZ", "verdict": "survive", "checks": {"x": "pass"}}
    _write(redteam, {"verdicts": [row, row]})
    errors, _ = validate_redteam(redteam, draft)
    assert any("duplicate verdict" in error for error in errors)
