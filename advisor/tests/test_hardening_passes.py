"""Regression cases for production-hardening findings, isolated from live data."""
import fcntl
import json

from advisor import orchestrator
from advisor.research import picks, pick_tracker


def test_duplicate_does_not_overwrite_running_status(tmp_path, monkeypatch):
    path = tmp_path / "pipeline.lock"
    monkeypatch.setattr(orchestrator, "PIPELINE_LOCK", str(path))
    monkeypatch.setattr(orchestrator, "_heartbeat", lambda *a: None)
    statuses = []
    monkeypatch.setattr(orchestrator, "write_status", lambda **kw: statuses.append(kw))
    with path.open("w") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert orchestrator.run_pipeline("2026-09-04") == 75
    assert statuses == []


def test_macro_quota_stops_downstream_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "PIPELINE_LOCK", str(tmp_path / "lock"))
    monkeypatch.setattr(orchestrator, "_heartbeat", lambda *a: None)
    monkeypatch.setattr(orchestrator, "write_status", lambda **kw: None)
    monkeypatch.setattr(orchestrator, "classify_failure", lambda *a: "provider_quota")
    calls = []
    monkeypatch.setattr(orchestrator, "run_stage", lambda name, *a: calls.append(name) or 1)
    assert orchestrator.run_pipeline("2026-09-04", skip_preflight=True, force=True) == 1
    assert calls == ["macro"]


def test_calibration_checks_actual_scoring_version():
    cal = {"usable": True, "scoring_version": 2,
           "buckets": [{"lo": 0, "hi": 1, "hit_rate": .8, "n": 100}]}
    assert picks.apply_calibration(.7, cal, scoring_version=1)[0] is None


def test_backfill_cannot_activate_live_probability(tmp_path, monkeypatch):
    rows = {str(i): {"status": "resolved", "calibration_eligible": True,
                     "win": True, "score": .3 if i < 50 else .8,
                     "scoring_version": 2, "source": "backfill"} for i in range(100)}
    monkeypatch.setattr(pick_tracker, "effective", lambda: rows)
    monkeypatch.setattr(pick_tracker, "CALIBRATION", tmp_path / "cal.json")
    result = pick_tracker.calibrate(False)
    assert result["n_resolved"] == 0
    assert result["usable"] is False


def test_spy_and_stop_comparisons_only_use_matched_rows(monkeypatch):
    monkeypatch.setattr(pick_tracker, "effective", lambda: {
        "a": {"status": "resolved", "return_pct": 10},
        "b": {"status": "resolved", "return_pct": 2,
              "bench_return_pct": 1, "unstopped_return_pct": 3}})
    result = pick_tracker.baselines()
    assert result["vs_spy_pp"] == 1
    assert result["stop_cost_pp"] == -1
    assert result["n_spy_paired"] == result["n_stop_paired"] == 1


def test_quota_reset_is_explicit_and_bounded(tmp_path):
    from advisor.pipeline_status import quota_retry_at
    log = tmp_path / "log"
    log.write_text(json.dumps({"rate_limit_info": {
        "status": "rejected", "resetsAt": 2000}}))
    assert quota_retry_at(log, 1000) == 2000
    assert quota_retry_at(log, 3000) is None
    log.write_text("resets tomorrow, trust me")
    assert quota_retry_at(log, 1000) is None


def test_quota_cooldown_never_calls_systemd(tmp_path, monkeypatch):
    from datetime import datetime
    from advisor import brief_control as bc
    monkeypatch.setattr(bc, "DATA", tmp_path)
    monkeypatch.setattr(bc, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(bc, "AUDIT", tmp_path / "audit")
    status = tmp_path / "status"
    status.write_text(json.dumps({"reason": "provider_quota",
        "provider_retry_at": datetime.now(bc.ET).timestamp() + 600}))
    monkeypatch.setattr(bc, "PIPELINE_STATUS", status)
    def forbidden(*a, **k):
        raise AssertionError("must not invoke systemd")
    result = bc.request_generation(runner=forbidden)
    assert result["reason"] == "provider_quota"
    assert result["outcome"] == "rejected"


def test_invalid_costs_never_pass_screen():
    from advisor.research.costs import screen
    for bad in [float("nan"), float("inf"), -1, "bad", 0]:
        assert screen("X", 100, 120, {"round_trip_bps": bad})["ok"] is False


def test_unproven_priors_are_not_loaded(tmp_path, monkeypatch):
    path = tmp_path / "priors.json"
    monkeypatch.setattr(picks, "PRIORS", path)
    path.write_text(json.dumps({"priors": {"tactical_long": 1.3}}))
    assert picks._priors() == {}
    path.write_text(json.dumps({"source": "backfill", "scoring_version": 2,
                                "priors": {"tactical_long": 1.3}}))
    assert picks._priors() == {}


def test_missing_ohlc_does_not_shift_resolution_dates(tmp_path, monkeypatch):
    import pandas as pd
    from advisor.research import datastore
    idx = pd.bdate_range("2026-08-03", periods=4)
    close = pd.DataFrame({"X": [100., 101., 102., 103.]}, index=idx)
    high, low = close + 1, close - 1
    high.iloc[1, 0] = float("nan")
    monkeypatch.setattr(datastore, "load_panel", lambda field: {
        "close": close, "high": high, "low": low}[field])
    monkeypatch.setattr(pick_tracker, "effective", lambda: {"x": {
        "status": "open", "ticker": "X", "price_bar": "2026-08-03",
        "horizon_td": 3, "stop": 95, "target": 110, "ref_px": 100}})
    monkeypatch.setattr(pick_tracker, "LEDGER", tmp_path / "ledger")
    result = pick_tracker.resolve(False)
    assert result["skipped"] == 1
    assert result["resolved"] == 0
