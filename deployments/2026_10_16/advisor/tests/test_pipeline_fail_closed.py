from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from advisor import orchestrator


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "REPO", tmp_path)
    monkeypatch.setattr(orchestrator, "PIPELINE_LOCK", str(tmp_path / "pipeline.lock"))
    monkeypatch.setattr(orchestrator, "_heartbeat", lambda *a, **k: None)
    # orchestrator imports write_status directly; moving REPO alone does not
    # redirect pipeline_status.STATUS. Never let a test touch operational state.
    statuses = []
    monkeypatch.setattr(orchestrator, "write_status",
                        lambda **kwargs: statuses.append(kwargs))
    alerts = []
    monkeypatch.setattr(orchestrator, "_telegram", alerts.append)
    return alerts


def test_synthesis_failure_never_invokes_legacy_fallback(monkeypatch, tmp_path):
    alerts = _isolate(monkeypatch, tmp_path)
    called = []

    def stage(name, *_args):
        called.append(name)
        return 3 if name == "synthesis" else 0

    monkeypatch.setattr(orchestrator, "run_stage", stage)
    rc = orchestrator.run_pipeline("2026-07-16", skip_preflight=True)
    assert rc == 3
    assert called == ["macro", "synthesis"]
    assert "legacy" not in orchestrator.STAGES
    assert any("NO RECOMMENDATIONS" in message for message in alerts)


def test_redteam_failure_blocks_actionable_draft(monkeypatch, tmp_path):
    alerts = _isolate(monkeypatch, tmp_path)
    ctx = tmp_path / "advisor/data/context/2026-07-16"
    ctx.mkdir(parents=True)
    (ctx / "views_draft.json").write_text(json.dumps({"views": [{"instrument": "X"}]}))
    called = []

    def stage(name, *_args):
        called.append(name)
        return 4 if name == "redteam" else 0

    monkeypatch.setattr(orchestrator, "run_stage", stage)
    rc = orchestrator.run_pipeline("2026-07-16", skip_preflight=True)
    assert rc == 4
    assert "publish" not in called
    assert any("red-team" in message for message in alerts)
