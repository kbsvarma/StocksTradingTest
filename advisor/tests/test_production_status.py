from __future__ import annotations

from types import SimpleNamespace

from advisor import production_status as ps


def test_runtime_service_check_requires_every_service(monkeypatch):
    monkeypatch.setattr(ps.sys, "platform", "linux")
    monkeypatch.setattr(ps.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="active\nfailed\nactive\n", returncode=3))
    level, detail = ps._check_runtime_services()
    assert level == "block"
    assert "advisor-quoted.service=failed" in detail


def test_execution_isolation_fails_closed(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "advisor").mkdir()
    config = repo / "advisor" / "config_advisor.yaml"
    monkeypatch.setattr(ps, "REPO", repo)
    monkeypatch.delenv("ADVISOR_EXECUTION_ENABLED", raising=False)
    config.write_text("execution:\n  enabled: false\n")
    assert ps._check_execution_isolation()[0] == "pass"
    config.write_text("execution:\n  enabled: true\n")
    assert ps._check_execution_isolation()[0] == "block"
    config.write_text("execution:\n  enabled: false\n")
    monkeypatch.setenv("ADVISOR_EXECUTION_ENABLED", "1")
    assert ps._check_execution_isolation()[0] == "block"
