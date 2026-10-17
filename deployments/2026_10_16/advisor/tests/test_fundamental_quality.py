from __future__ import annotations

import json

import pandas as pd
import numpy as np

import advisor.research.factors_fundamental as ff
from advisor.research.ingest.runner import _apply_quality


def test_ingest_quality_rejects_partial_success():
    result = _apply_quality("info", {"rows": 60, "errors": 40}, 100)
    assert result["ok"] is False
    assert result["success_ratio"] == 0.6


def test_low_coverage_disables_value_quality_but_keeps_estimate_candidates(tmp_path,
                                                                          monkeypatch):
    monkeypatch.setattr(ff, "RESEARCH_DIR", tmp_path)
    (tmp_path / "snapshots" / "info").mkdir(parents=True)
    (tmp_path / "estimates").mkdir()
    (tmp_path / "_meta").mkdir()
    pd.DataFrame([{
        "ticker": "ABC", "forwardPE": 10.0, "freeCashflow": 100.0,
        "marketCap": 1000.0, "returnOnEquity": 0.2, "grossMargins": 0.5,
        "earningsGrowth": 0.1, "shortPercentOfFloat": 0.1,
    }]).to_parquet(tmp_path / "snapshots" / "info" / "dt=2026-09-03.parquet")
    pd.DataFrame([{
        "ticker": "ABC", "epsrev_0y_upLast30days": 2,
        "epsrev_0y_downLast30days": 0, "epstrend_0y_current": 1.1,
        "epstrend_0y_30daysAgo": 1.0,
    }]).to_parquet(tmp_path / "estimates" / "dt=2026-09-03.parquet")
    (tmp_path / "_meta" / "ingest_manifest.json").write_text(json.dumps({
        "n_tickers": 100,
        "scope": {"estimates_events": "active_set(1)"},
        "datasets": {"info": {"ok": True, "rows": 60},
                     "estimates": {"ok": True, "rows": 1}},
    }))
    frame, meta = ff.compute_scores()
    assert meta["input_quality"]["info"]["usable"] is False
    assert "value" not in frame and "quality" not in frame
    assert "est_revision" in frame
    rendered = ff.render_top(frame, meta)
    assert rendered["scope"]["ranking_scope"] == "active_estimate_set(1)"
    assert rendered["scope"]["market_wide_rank"] is False


def test_fundamental_result_normalizes_numpy_scalars_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "OUT", tmp_path / "fundamental_latest.json")
    ff.write_result({"rows": [{"days_since_report": np.int64(3),
                               "score": np.float64(1.25)}]})
    assert json.loads(ff.OUT.read_text())["rows"][0] == {
        "days_since_report": 3, "score": 1.25}
    assert not list(tmp_path.glob("*.tmp.*"))
