from __future__ import annotations

import json

import advisor.research.factors as factors


def test_model_promotion_requires_oos_independence_and_matching_approval(tmp_path, monkeypatch):
    data = tmp_path / "advisor" / "data" / "research"
    data.mkdir(parents=True)
    validation = {
        "as_of": "2026-09-03T01:00:00-04:00",
        "config_hash": "abc123",
        "walk_forward_top20": {
            "oos_n_periods": 24,
            "deflated_sharpe_oos": {"dsr": 0.97},
        },
        "tests": {"mom_12_1|insample": {"fdr10_survives": True},
                  "mom_12_1|oos": {"fdr10_survives": True}},
    }
    (data / "validation2_latest.json").write_text(json.dumps(validation))
    (data / "ic_live.json").write_text(json.dumps({
        "n_matured": 252,
        "factors": {"mom_12_1": {"n_independent": 12}},
    }))
    monkeypatch.setattr(factors, "REPO_ROOT", tmp_path)

    assert factors.validation_status()["status"] == "research_only"
    (data / "model_approval.json").write_text(json.dumps({
        "approved": True, "config_hash": "wrong",
    }))
    assert factors.validation_status()["status"] == "research_only"
    (data / "model_approval.json").write_text(json.dumps({
        "approved": True, "config_hash": "abc123",
    }))
    result = factors.validation_status()
    assert result["status"] == "research_only"
    assert any("parity" in reason for reason in result["reasons"])
    assert result["fdr10_survivors"] == ["mom_12_1|oos"]


def test_insample_survivor_cannot_promote_model(tmp_path, monkeypatch):
    data = tmp_path / "advisor" / "data" / "research"
    data.mkdir(parents=True)
    (data / "validation2_latest.json").write_text(json.dumps({
        "config_hash": "abc", "walk_forward_top20": {
            "oos_n_periods": 30, "deflated_sharpe_oos": {"dsr": 0.99}},
        "tests": {"mom_12_1|insample": {"fdr10_survives": True}},
    }))
    (data / "ic_live.json").write_text(json.dumps({
        "n_matured": 300, "factors": {"mom_12_1": {"n_independent": 15}}}))
    (data / "model_approval.json").write_text(json.dumps({
        "approved": True, "config_hash": "abc"}))
    monkeypatch.setattr(factors, "REPO_ROOT", tmp_path)
    result = factors.validation_status()
    assert result["status"] == "research_only"
    assert any("out-of-sample factor" in reason for reason in result["reasons"])
