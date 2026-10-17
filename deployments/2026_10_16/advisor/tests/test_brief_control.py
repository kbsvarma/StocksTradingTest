from __future__ import annotations

import json
from types import SimpleNamespace

import advisor.brief_control as bc


def _runner(states, calls):
    def run(cmd, **kwargs):
        calls.append(cmd)
        if "is-active" in cmd:
            return SimpleNamespace(stdout=states.pop(0), stderr="", returncode=3)
        return SimpleNamespace(stdout="", stderr="", returncode=0)
    return run


def test_ui_arm_ttl_is_bounded_and_fail_closed():
    assert bc.arm_remaining_s(1060, now_ts=1000) == 60
    assert bc.arm_remaining_s(1030.9, now_ts=1000) == 30
    assert bc.arm_remaining_s(999, now_ts=1000) == 0
    assert bc.arm_remaining_s("not-a-time", now_ts=1000) == 0
    assert bc.arm_remaining_s(float("inf"), now_ts=1000) == 0


def test_request_queues_systemd_service_and_audits(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DATA", tmp_path / "data")
    monkeypatch.setattr(bc, "LOCK", tmp_path / "data" / ".lock")
    monkeypatch.setattr(bc, "AUDIT", tmp_path / "logs" / "audit.jsonl")
    calls = []
    row = bc.request_generation(cooldown_s=0,
                                runner=_runner(["inactive\n"], calls))
    assert row["outcome"] == "accepted"
    assert calls[-1] == ["systemctl", "--user", "start", "--no-block",
                         "advisor-brief-on-demand.service"]
    assert json.loads(bc.AUDIT.read_text().splitlines()[-1])["actor"] == "portal_operator"


def test_request_refuses_active_service(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DATA", tmp_path / "data")
    monkeypatch.setattr(bc, "LOCK", tmp_path / "data" / ".lock")
    monkeypatch.setattr(bc, "AUDIT", tmp_path / "logs" / "audit.jsonl")
    calls = []
    row = bc.request_generation(cooldown_s=0,
                                runner=_runner(["active\n"], calls))
    assert row["outcome"] == "rejected"
    assert row["reason"] == "already_running"
    assert len(calls) == 1


def test_request_enforces_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "DATA", tmp_path / "data")
    monkeypatch.setattr(bc, "LOCK", tmp_path / "data" / ".lock")
    monkeypatch.setattr(bc, "AUDIT", tmp_path / "logs" / "audit.jsonl")
    bc.AUDIT.parent.mkdir(parents=True)
    from datetime import datetime
    bc.AUDIT.write_text(json.dumps({"outcome": "accepted",
                                    "requested_at": datetime.now(bc.ET).isoformat()}) + "\n")
    calls = []
    row = bc.request_generation(cooldown_s=300,
                                runner=_runner(["inactive\n"], calls))
    assert row["reason"] == "cooldown"
    assert not calls


def test_status_is_display_safe(tmp_path, monkeypatch):
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps({"state": "running", "stage": "redteam",
                                "secret": "must-not-leak"}))
    monkeypatch.setattr(bc, "PIPELINE_STATUS", path)
    monkeypatch.setattr(bc, "AUDIT", tmp_path / "none")
    value = bc.status()
    assert value["state"] == "running"
    assert value["stage"] == "redteam"
    assert "secret" not in value
