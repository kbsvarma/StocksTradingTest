from __future__ import annotations

import json
from pathlib import Path

from advisor.pipeline_status import classify_failure, classify_text, write_status
import advisor.pipeline_status as ps


def test_failure_classifier_detects_provider_quota(tmp_path):
    log = tmp_path / "stage.log"
    log.write_text("You've hit your session limit; resets later")
    assert classify_failure(1, log) == "provider_quota"
    assert classify_text(1, "You've hit your limit · resets 9:40am") == "provider_quota"
    assert classify_text(1, "Not authenticated; run /login") == "provider_auth"


def test_status_replacement_is_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "pipeline_status.json"
    monkeypatch.setattr(ps, "STATUS", path)
    write_status(run_id="r1", date="2026-09-03", state="running", stage="macro")
    write_status(run_id="r1", date="2026-09-03", state="failed", stage="synthesis",
                 returncode=1, reason="provider_quota")
    value = json.loads(path.read_text())
    assert value["reason"] == "provider_quota"
    assert value["actionable_output_allowed"] is False
    assert not list(tmp_path.glob("*.tmp.*"))
