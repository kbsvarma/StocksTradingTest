from __future__ import annotations

import json

import advisor.brief_check as brief_check
from advisor.research import calibration


def _contract_probability(calibration_id="claimed", sample_n=30):
    return {
        "decision_key": "2026-09-03:ABC:long",
        "data_as_of": "2026-09-03T08:00:00-04:00",
        "time_stop": "2026-09-10",
        "catalyst": {"event": "filing", "date": "2026-09-04", "mechanism": "x"},
        "disconfirmers": ["a", "b"],
        "p_win": 0.60, "reward_risk": 2.0, "expected_value_r": 0.8,
        "probability_basis": {"type": "empirical_calibration", "calibrated": True,
                              "sample_n": sample_n,
                              "calibration_id": calibration_id},
        "recommendation_class": "actionable_idea",
    }


def test_self_declared_calibration_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(brief_check, "CALIBRATION", tmp_path / "missing.json")
    errors = brief_check._production_contract(_contract_probability(), "view")
    assert any("canonical calibration" in e for e in errors)
    assert any("calibration_id" in e for e in errors)


def test_actionable_probability_must_match_approved_canonical_artifact(
        tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"actionable_probability_allowed": True,
                                "calibration_id": "canonical", "n_explicit_scored": 31}))
    monkeypatch.setattr(brief_check, "CALIBRATION", path)
    errors = brief_check._production_contract(
        _contract_probability("canonical", 31), "view")
    relevant = [e for e in errors if any(x in e for x in
                ("canonical calibration", "calibration_id", "explicit scored sample"))]
    assert relevant == []


def test_calibration_requires_objective_gate_and_matching_human_approval(
        tmp_path, monkeypatch):
    rows = []
    for i in range(30):
        rows.append({"win": 1 if i < 18 else 0, "p_win_effective": 0.60,
                     "p_win_origin": "explicit", "calibration_eligible": True})
    monkeypatch.setattr(calibration, "resolved_views", lambda: rows)
    approval = tmp_path / "approval.json"
    monkeypatch.setattr(calibration, "APPROVAL", approval)
    first = calibration.compute()
    assert first["objective_actionability_gate"] is True
    assert first["actionable_probability_allowed"] is False
    approval.write_text(json.dumps({"calibration_id": first["calibration_id"],
                                    "approved_by": "risk-reviewer",
                                    "approved_at": "2026-09-03T08:00:00-04:00"}))
    second = calibration.compute()
    assert second["actionable_probability_allowed"] is True
    assert second["approval_status"] == "approved"
