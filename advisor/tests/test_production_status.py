from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime
import hashlib
import json

from advisor import production_status as ps


def test_runtime_service_check_requires_every_service(monkeypatch):
    monkeypatch.setattr(ps.sys, "platform", "linux")
    monkeypatch.setattr(ps.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="active\nfailed\nactive\n", returncode=3))
    level, detail = ps._check_runtime_services()
    assert level == "block"
    assert "advisor-quoted.service=failed" in detail


def test_runtime_service_check_requires_enabled_timer(monkeypatch):
    monkeypatch.setattr(ps.sys, "platform", "linux")
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if 'is-active' in args:return SimpleNamespace(stdout="active\nactive\nactive\nactive\n",returncode=0)
        return SimpleNamespace(stdout="disabled\n",returncode=1)
    monkeypatch.setattr(ps.subprocess,"run",run)
    level,detail=ps._check_runtime_services()
    assert level=='block' and 'survive restart' in detail


def test_investigator_runtime_must_be_configured(monkeypatch):
    from advisor.investigator import runtime
    monkeypatch.setattr(runtime,'status',lambda:{'configured':False,'provider':'unconfigured','model':'unconfigured'})
    assert ps._check_investigator_runtime()[0]=='block'
    monkeypatch.setattr(runtime,'status',lambda:{'configured':True,'provider':'fixture','model':'model-v1'})
    assert ps._check_investigator_runtime()[0]=='pass'


def test_research_acceptance_requires_independent_quality_set(tmp_path,monkeypatch):
    root=tmp_path/'intelligence';root.mkdir()
    monkeypatch.setattr(ps,'DATA',tmp_path)
    assert ps._check_research_acceptance()[0]=='warn'
    (root/'research_acceptance.json').write_text(json.dumps({
        'cases':40,'critical_errors':0,'material_claim_accuracy':.97,
        'independent_reviewer':'review-firm'}))
    assert ps._check_research_acceptance()[0]=='pass'


def test_ranking_approval_is_bound_to_exact_evaluation(tmp_path,monkeypatch):
    root=tmp_path/'research';root.mkdir()
    monkeypatch.setattr(ps,'DATA',tmp_path)
    evaluation={'by_scoring_version':{'3':{'n_nonoverlapping_windows':30}}}
    (root/'ranking_evaluation.json').write_text(json.dumps(evaluation))
    assert ps._check_ranking_evidence()[0]=='warn'
    fingerprint=hashlib.sha256(json.dumps(evaluation,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    (root/'ranking_approval.json').write_text(json.dumps({
        'approved':True,'evaluation_sha256':fingerprint,'scoring_version':3,
        'independent_reviewer':'model-risk'}))
    assert ps._check_ranking_evidence()[0]=='pass'
    evaluation['by_scoring_version']['3']['n_nonoverlapping_windows']=31
    (root/'ranking_evaluation.json').write_text(json.dumps(evaluation))
    assert ps._check_ranking_evidence()[0]=='warn'


def test_publication_day_reuses_session_issue_until_today_is_published(tmp_path,monkeypatch):
    monkeypatch.setattr(ps,'DATA',tmp_path)
    now=datetime.fromisoformat('2026-09-14T08:00:00-04:00')
    assert ps._publication_day(now,'2026-09-11')=='2026-09-11'
    today=tmp_path/'context'/'2026-09-14';today.mkdir(parents=True)
    (today/'brief.json').write_text('{}')
    assert ps._publication_day(now,'2026-09-11')=='2026-09-14'


def test_malformed_acceptance_and_ranking_artifacts_fail_closed(tmp_path,monkeypatch):
    (tmp_path/'intelligence').mkdir();(tmp_path/'research').mkdir()
    monkeypatch.setattr(ps,'DATA',tmp_path)
    (tmp_path/'intelligence'/'research_acceptance.json').write_text(json.dumps({'cases':'many'}))
    (tmp_path/'research'/'ranking_evaluation.json').write_text(json.dumps({'by_scoring_version':{'3':{'n_nonoverlapping_windows':'many'}}}))
    assert ps._check_research_acceptance()[0]=='warn'
    assert ps._check_ranking_evidence()[0]=='warn'


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


def test_panel_freshness_uses_latest_completed_exchange_session(tmp_path, monkeypatch):
    payload = b"panel"
    (tmp_path / "close.parquet").write_bytes(payload)
    monkeypatch.setattr(ps, "current_build_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "current_meta", lambda: {
        "built_unix": datetime.fromisoformat("2026-09-11T06:00:00-04:00").timestamp(),
        "price_bar": "2026-09-11", "quality": {"ok": True},
        "sha256": {"close.parquet": hashlib.sha256(payload).hexdigest()}})
    level, detail = ps._check_panel(datetime.fromisoformat("2026-09-12T14:00:00-04:00"))
    assert level == "pass" and "2026-09-11" in detail


def test_panel_blocks_prior_session_even_when_build_is_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "current_build_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "current_meta", lambda: {
        "built_unix": datetime.fromisoformat("2026-09-12T13:59:00-04:00").timestamp(),
        "price_bar": "2026-09-10", "quality": {"ok": True}, "sha256": {}})
    level, detail = ps._check_panel(datetime.fromisoformat("2026-09-12T14:00:00-04:00"))
    assert level == "block" and "required completed session 2026-09-11" in detail


def test_signals_use_market_bar_not_wall_clock_age(tmp_path, monkeypatch):
    research = tmp_path / "research"; research.mkdir()
    monkeypatch.setattr(ps, "DATA", tmp_path)
    now = datetime.fromisoformat("2026-09-12T14:00:00-04:00")
    (research / "signals_latest.json").write_text(json.dumps({
        "as_of": "2026-09-11T18:00:00-04:00",
        "data_quality": {"latest_market_date": "2026-09-11"}}))
    assert ps._check_signals(now)[0] == "pass"
    (research / "signals_latest.json").write_text(json.dumps({
        "as_of": "2026-09-12T13:00:00-04:00",
        "data_quality": {"latest_market_date": "2026-09-10"}}))
    assert ps._check_signals(now)[0] == "block"


def test_worker_must_be_recent_and_healthy(tmp_path, monkeypatch):
    root = tmp_path / "intelligence"; root.mkdir()
    monkeypatch.setattr(ps, "DATA", tmp_path)
    now = datetime.fromisoformat("2026-09-12T14:00:00-04:00")
    (root / "worker_status.json").write_text(json.dumps({
        "as_of": "2026-09-12T13:58:00-04:00", "status": "healthy"}))
    assert ps._check_worker(now)[0] == "pass"
    (root / "worker_status.json").write_text(json.dumps({
        "as_of": "2026-09-12T13:40:00-04:00", "status": "healthy"}))
    assert ps._check_worker(now)[0] == "block"
