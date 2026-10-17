from __future__ import annotations

import json

import advisor.actionability as actionability


def test_actionability_fails_closed_without_every_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_DIR", str(tmp_path))
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "calibration_latest.json").write_text(json.dumps({
        "actionable_probability_allowed": True, "calibration_id": "c1",
        "n_explicit_scored": 30}))
    base = {"recommendation_class": "actionable_idea",
            "portfolio_context_status": "verified",
            "probability_basis": {"calibrated": True,
                                  "type": "empirical_calibration",
                                  "calibration_id": "c1", "sample_n": 30}}
    assert actionability.is_actionable(base)
    for key in ("recommendation_class", "portfolio_context_status",
                "probability_basis"):
        broken = dict(base)
        broken.pop(key)
        assert not actionability.is_actionable(broken)


def test_research_idea_is_never_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_DIR", str(tmp_path))
    assert not actionability.is_actionable({"recommendation_class": "research_idea"})
