"""Regressions for artifact <-> code binding.

The panel serving every factor computation on 2026-09-03 was built at 06:01 by
code replaced at 11:56. Its meta.json was missing `provider`,
`price_adjustment` and `intended_use` — fields the current datastore writes —
while still declaring `schema_version: 2`, so a version check could not catch
it. Nothing invalidated a stale artifact after a code change, and nothing
recorded which code had produced it. It was also the fastest finding for an
auditor to reach: visible in one `cat`.
"""
import json
import subprocess

import pytest

from advisor.research import datastore


def test_code_version_reports_the_production_checkout():
    v = datastore.code_version()
    assert set(v) == {"git_sha", "git_dirty", "deployed_from"}


def test_code_version_reads_a_real_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "c"], check=True)
    monkeypatch.setattr(datastore, "REPO_ROOT", tmp_path)
    v = datastore.code_version()
    assert v["git_sha"] and len(v["git_sha"]) == 40
    assert v["git_dirty"] is False


def test_dirty_tree_is_reported(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "c"], check=True)
    (tmp_path / "f.txt").write_text("changed")
    monkeypatch.setattr(datastore, "REPO_ROOT", tmp_path)
    assert datastore.code_version()["git_dirty"] is True


def test_deploy_stamp_is_picked_up(tmp_path, monkeypatch):
    (tmp_path / ".deployed_from").write_text("abc123def\n")
    monkeypatch.setattr(datastore, "REPO_ROOT", tmp_path)
    assert datastore.code_version()["deployed_from"] == "abc123def"


def test_missing_git_is_not_fatal(tmp_path, monkeypatch):
    """An artifact must still build on a box without git."""
    monkeypatch.setattr(datastore, "REPO_ROOT", tmp_path)
    v = datastore.code_version()
    assert v["git_sha"] is None and v["deployed_from"] is None
