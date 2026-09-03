from __future__ import annotations

import advisor.orchestrator as orchestrator


def test_dry_run_does_not_write_production_status(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "write_status",
                        lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(orchestrator, "_heartbeat",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    assert orchestrator.run_pipeline("2026-09-03", dry_run=True) == 0
    assert calls == []
