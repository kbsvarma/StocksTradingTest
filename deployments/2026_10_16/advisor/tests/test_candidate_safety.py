from __future__ import annotations

import json

import pandas as pd

import advisor.research.candidates as candidates
import advisor.research.factors_fundamental as fundamental


def test_repeat_kill_anecdote_is_not_a_candidate_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(candidates, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(fundamental, "compute_scores",
                        lambda: (pd.DataFrame(), {}))
    monkeypatch.setattr(candidates, "repeat_kills",
                        lambda: [{"ticker": "XYZ", "n_kills": 10,
                                  "last_kill": "2026-09-03",
                                  "kill_reasons": ["extended"]}])
    (tmp_path / "signals_latest.json").write_text(json.dumps({
        "longs": [], "shorts": [],
    }))
    result = candidates.build()
    assert "repeat_kill" not in candidates.CAPS
    assert all("repeat_kill" not in row["buckets"] for row in result["slate"])
    assert all(row["ticker"] != "XYZ" for row in result["slate"])
    assert all(scope.get("market_wide") is False
               for scope in result["generator_scope"].values())


def test_candidate_publish_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(candidates, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(candidates, "OUT", tmp_path / "candidates_latest.json")
    monkeypatch.setattr(candidates, "build", lambda: {
        "as_of": "2026-09-03T00:00:00-04:00", "n": 0,
        "confluence": [], "slate": [], "generator_scope": {}, "method": "test"})
    assert candidates.main() == 0
    assert json.loads(candidates.OUT.read_text())["n"] == 0
    assert not list(tmp_path.glob("*.tmp.*"))
