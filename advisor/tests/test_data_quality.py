from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from advisor.research import datastore
from advisor.research.data_quality import assess
from advisor.research import factors


def _panels(jump_ticker=None):
    dates = pd.bdate_range(end="2026-07-16", periods=260)
    cols = ["A", "SPY", "^VIX"]
    close = pd.DataFrame(100.0, index=dates, columns=cols)
    if jump_ticker:
        close.loc[dates[-1], jump_ticker] = 175.0
    return {
        "Close": close,
        "High": close * 1.01,
        "Low": close * 0.99,
        "Volume": pd.DataFrame(1_000_000.0, index=dates, columns=cols),
    }


def test_symbol_anomaly_is_quarantined_without_poisoning_dataset():
    result = assess(_panels("A"), requested=3, now="2026-07-16 22:00:00")
    assert result["ok"] is True
    assert "A" in result["quarantine"]
    assert result["n_quarantined"] == 1


def test_benchmark_anomaly_fails_closed():
    result = assess(_panels("SPY"), requested=3, now="2026-07-16 22:00:00")
    assert result["ok"] is False
    assert any("benchmark SPY" in error for error in result["errors"])


def test_stale_or_misaligned_panel_fails_closed():
    panels = _panels()
    panels["Volume"] = panels["Volume"].iloc[:-1]
    result = assess(panels, requested=3, now="2026-07-22 22:00:00")
    assert result["ok"] is False
    assert any("not aligned" in error for error in result["errors"])
    assert any("business days old" in error for error in result["errors"])


def test_current_pointer_selects_one_immutable_build(tmp_path, monkeypatch):
    panel_dir = tmp_path / "panels"
    builds = panel_dir / "builds"
    old = builds / "old"
    new = builds / "new"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "meta.json").write_text(json.dumps({"build_id": "old"}))
    (new / "meta.json").write_text(json.dumps({"build_id": "new"}))
    current = panel_dir / "CURRENT"
    current.write_text("old\n")
    monkeypatch.setattr(datastore, "PANEL_DIR", panel_dir)
    monkeypatch.setattr(datastore, "BUILDS_DIR", builds)
    monkeypatch.setattr(datastore, "CURRENT", current)
    assert datastore.current_meta()["build_id"] == "old"
    current.write_text("new\n")
    assert datastore.current_meta()["build_id"] == "new"


def test_invalid_current_pointer_is_rejected(tmp_path, monkeypatch):
    panel_dir = tmp_path / "panels"
    panel_dir.mkdir()
    current = panel_dir / "CURRENT"
    current.write_text("../escape\n")
    monkeypatch.setattr(datastore, "PANEL_DIR", panel_dir)
    monkeypatch.setattr(datastore, "BUILDS_DIR", panel_dir / "builds")
    monkeypatch.setattr(datastore, "CURRENT", current)
    try:
        datastore.current_build_dir()
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe CURRENT pointer was accepted")


def test_unproven_factor_model_is_machine_labeled_research_only(tmp_path, monkeypatch):
    research = tmp_path / "advisor/data/research"
    research.mkdir(parents=True)
    (research / "validation2_latest.json").write_text(json.dumps({
        "as_of": "2026-07-01", "walk_forward_top20": {
            "deflated_sharpe": {"dsr": 0.79}},
        "tests": {"mom_12_1|oos": {"fdr10_survives": False}}}))
    (research / "ic_live.json").write_text(json.dumps({"n_matured": 0}))
    monkeypatch.setattr(factors, "REPO_ROOT", tmp_path)
    status = factors.validation_status()
    assert status["status"] == "research_only"
    assert status["role"] == "discovery_only"
    assert any("deflated Sharpe" in reason for reason in status["reasons"])
