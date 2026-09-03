from __future__ import annotations

from advisor import orchestrator


def test_bash_allow_rules_use_current_prefix_glob_grammar():
    rules = [rule for stage in orchestrator.STAGES.values()
             for rule in stage["tools"] if rule.startswith("Bash(")]
    assert rules
    assert all(":*" not in rule for rule in rules)
    assert any(f"Bash({orchestrator.PY} -m advisor.brief_check *)" == rule
               for rule in rules)


def test_noninteractive_pipeline_never_waits_for_permission_prompt(monkeypatch,
                                                                    tmp_path):
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    monkeypatch.setattr(orchestrator, "LOGS", tmp_path)
    assert orchestrator._claude("synthesis", "2026-09-03") == 0
    cmd = captured["cmd"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    prompt = cmd[cmd.index("-p") + 1]
    assert f"ADVISOR_PYTHON: {orchestrator.PY}" in prompt


def test_no_model_publish_stage_exists():
    assert "publish" not in orchestrator.STAGES


def test_synthesis_edit_scope_expands_to_exact_owned_file(monkeypatch, tmp_path):
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
    monkeypatch.setattr(orchestrator, "LOGS", tmp_path)
    assert orchestrator._claude("synthesis", "2026-09-03") == 0
    allowed = captured["cmd"][captured["cmd"].index("--allowedTools") + 1:]
    edits = [rule for rule in allowed if rule.startswith("Edit(")]
    assert edits == [
        "Edit(advisor/data/context/2026-09-03/views_draft.json)",
    ]
    assert not any("**" in rule for rule in edits)
