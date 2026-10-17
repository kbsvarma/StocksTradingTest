"""Regressions for spend accounting and duplicate-run suppression.

Two audit findings, both invisible for the same reason — nothing measured cost:

  F13  No token or dollar figure anywhere in 12,697 lines, so "is this worth
       the operational cost" could not be answered from the system's own data.
  F11  Every kill in the 2026-09-03 brief read "re-kill: unchanged data from
       this morning's kill" against an identical panel_build_id. The pipeline
       paid full model cost twice for the same six conclusions.
"""
import json

import pytest

from advisor import orchestrator as orch


RESULT = {
    "type": "result", "total_cost_usd": 0.1234567, "num_turns": 9,
    "usage": {"input_tokens": 11, "output_tokens": 222,
              "cache_read_input_tokens": 33333,
              "cache_creation_input_tokens": 4444},
}


def test_usage_is_parsed_from_the_final_result_event(tmp_path):
    log = tmp_path / "s.log"
    log.write_text("\n".join([
        json.dumps({"type": "system"}),
        json.dumps({"type": "assistant", "message": {}}),
        json.dumps(RESULT)]) + "\n")
    u = orch._parse_usage(log)
    assert u["cost_usd"] == pytest.approx(0.123457)
    assert u["output_tokens"] == 222
    assert u["cache_read_tokens"] == 33333
    assert u["num_turns"] == 9


def test_non_json_transcript_lines_are_skipped(tmp_path):
    log = tmp_path / "s.log"
    log.write_text("===== macro start =====\ngarbage\n" + json.dumps(RESULT) + "\n")
    assert orch._parse_usage(log)["cost_usd"] == pytest.approx(0.123457)


def test_missing_or_costless_log_yields_no_usage(tmp_path):
    assert orch._parse_usage(tmp_path / "nope.log") == {}
    log = tmp_path / "s.log"
    log.write_text(json.dumps({"type": "assistant"}) + "\n")
    assert orch._parse_usage(log) == {}


def test_heartbeat_records_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "LOGS", tmp_path)
    monkeypatch.setattr(orch, "HEARTBEATS", tmp_path / "hb.jsonl")
    orch._heartbeat("macro", 0, 12.3, "", usage={"cost_usd": 0.5,
                                                 "output_tokens": 100})
    row = json.loads((tmp_path / "hb.jsonl").read_text().splitlines()[-1])
    assert row["cost_usd"] == 0.5 and row["output_tokens"] == 100


def test_heartbeat_without_usage_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "LOGS", tmp_path)
    monkeypatch.setattr(orch, "HEARTBEATS", tmp_path / "hb.jsonl")
    orch._heartbeat("macro", 0, 1.0, "note")
    row = json.loads((tmp_path / "hb.jsonl").read_text().splitlines()[-1])
    assert "cost_usd" not in row


def test_run_cost_sums_only_that_date_and_only_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "HEARTBEATS", tmp_path / "hb.jsonl")
    rows = [
        {"ts": "2026-09-04T08:00:00", "stage": "macro", "cost_usd": 0.10},
        {"ts": "2026-09-04T08:05:00", "stage": "synthesis", "cost_usd": 0.20},
        {"ts": "2026-09-04T08:09:00", "stage": "pipeline", "cost_usd": 0.30},
        {"ts": "2026-09-03T08:00:00", "stage": "macro", "cost_usd": 9.99},
    ]
    (tmp_path / "hb.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    # pipeline is a rollup, not a stage — counting it would double the total
    assert orch._run_cost("2026-09-04") == pytest.approx(0.30)


# --- duplicate-run suppression --------------------------------------------

@pytest.fixture
def research(tmp_path, monkeypatch):
    d = tmp_path / "advisor" / "data" / "research"
    d.mkdir(parents=True)
    monkeypatch.setattr(orch, "REPO", tmp_path)

    def _write(build_id, tickers):
        (d / "signals_latest.json").write_text(
            json.dumps({"panel_build_id": build_id}))
        (d / "candidates_latest.json").write_text(json.dumps(
            {"slate": [{"ticker": t} for t in tickers], "n_pickable": len(tickers)}))
    return _write


def test_fingerprint_is_stable_for_identical_inputs(research):
    research("B1", ["AAA", "BBB"])
    assert orch.input_fingerprint()["fingerprint"] == \
           orch.input_fingerprint()["fingerprint"]


def test_fingerprint_changes_with_the_panel(research):
    research("B1", ["AAA", "BBB"])
    a = orch.input_fingerprint()["fingerprint"]
    research("B2", ["AAA", "BBB"])
    assert orch.input_fingerprint()["fingerprint"] != a


def test_fingerprint_changes_with_the_slate(research):
    research("B1", ["AAA", "BBB"])
    a = orch.input_fingerprint()["fingerprint"]
    research("B1", ["AAA", "CCC"])
    assert orch.input_fingerprint()["fingerprint"] != a


def test_fingerprint_ignores_slate_ordering(research):
    research("B1", ["AAA", "BBB"])
    a = orch.input_fingerprint()["fingerprint"]
    research("B1", ["BBB", "AAA"])
    assert orch.input_fingerprint()["fingerprint"] == a


def test_last_fingerprint_reads_the_most_recent_completed_run(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "HEARTBEATS", tmp_path / "hb.jsonl")
    (tmp_path / "hb.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"stage": "pipeline", "fingerprint": "old"},
        {"stage": "macro", "cost_usd": 1},
        {"stage": "pipeline", "fingerprint": "new"}]) + "\n")
    assert orch.last_fingerprint() == "new"


def test_no_prior_run_means_no_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "HEARTBEATS", tmp_path / "absent.jsonl")
    assert orch.last_fingerprint() is None
