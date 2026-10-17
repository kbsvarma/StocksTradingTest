"""Learning-loop v1 tests: journal v2 semantics, outcome derivation,
redteam contract. All filesystem-isolated via ADVISOR_DATA_DIR; no network.
"""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


@pytest.fixture()
def iso_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("ADVISOR_DATA_DIR", str(tmp_path))
    import advisor.journal as J
    importlib.reload(J)
    yield J
    monkeypatch.delenv("ADVISOR_DATA_DIR")
    importlib.reload(J)


def _view(J, **kw):
    base = {"type": "view", "instrument": "XLE", "yf_ticker": "XLE",
            "direction": "long", "conviction": "high", "thesis": "t",
            "entry": "92", "target": "101", "stop": "88",
            "target_px": 101.0, "stop_px": 88.0, "p_win": 0.7,
            "source": "factor_long"}
    base.update(kw)
    return J.add(base)


def test_rejected_defaults_to_rejected_status(iso_journal):
    J = iso_journal
    r = J.add({"type": "rejected", "instrument": "QQQ", "killed_by": "loop D"})
    assert J.effective()[r["id"]]["status"] == "rejected"


def test_decision_key_makes_publish_retry_idempotent(iso_journal):
    J = iso_journal
    payload = {"type": "view", "decision_key": "2026-07-16:XLE:long",
               "instrument": "XLE", "direction": "long", "thesis": "same"}
    first = J.add(payload)
    replay = J.add(payload)
    assert replay["id"] == first["id"]
    assert len(J.read_all()) == 1
    with pytest.raises(ValueError, match="decision_key conflict"):
        J.add({**payload, "thesis": "changed after publication"})


def test_batch_conflict_appends_nothing(iso_journal):
    J = iso_journal
    existing = {"type": "view", "decision_key": "d:existing",
                "instrument": "XLE", "thesis": "original"}
    J.add(existing)
    before = list(J.read_all())
    with pytest.raises(ValueError, match="decision_key conflict"):
        J.add_batch([
            {"type": "view", "decision_key": "d:new",
             "instrument": "SPY", "thesis": "new"},
            {**existing, "thesis": "mutated"},
        ])
    assert J.read_all() == before


def test_research_language_gate_blocks_imperative_transaction_phrases():
    from advisor.brief_check import _unsafe_research_language
    assert _unsafe_research_language({"thesis": "Buy now before the catalyst"})
    assert _unsafe_research_language({"entry": "sell at 95"})
    assert _unsafe_research_language({"thesis": "You should allocate $500"})
    assert _unsafe_research_language({"thesis": "Scenario tests demand resilience"}) is None


def test_stamp_row_merges_data_only(iso_journal):
    J = iso_journal
    v = _view(J)
    J.append_raw({"id": v["id"], "ts": J._now().isoformat(),
                  "type": "stamp", "ref_px": 93.1, "ref_src": "test"})
    e = J.effective()[v["id"]]
    assert e["ref_px"] == 93.1
    assert e["type"] == "view" and e["status"] == "open"


def test_generated_ids_are_long_and_unique(iso_journal):
    J = iso_journal
    ids = {_view(J, instrument=f"T{i}")["id"] for i in range(300)}
    assert len(ids) == 300
    assert all(len(eid.rsplit("-", 1)[-1]) == 12 for eid in ids)


def test_explicit_id_collision_is_refused(iso_journal):
    J = iso_journal
    _view(J, id="V-20260702-FIXED")
    with pytest.raises(ValueError, match="collision"):
        _view(J, id="V-20260702-FIXED", instrument="OTHER")


def test_stamp_age_uses_origin_not_later_resolve_timestamp(iso_journal, monkeypatch):
    J = iso_journal
    old = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=10)
    v = _view(J, ts=old.isoformat())
    J.resolve(v["id"], "closed", "resolved today")

    class NeverFetch:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("old origin must not fetch a late reference price")

    import types
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=NeverFetch))
    assert J.stamp_refs(verbose=False, max_age_days=2) == 0
    assert "ref_px" not in J.effective()[v["id"]]


def test_effective_ignores_late_stamp_even_if_present(iso_journal):
    J = iso_journal
    old = datetime.now(ZoneInfo("America/New_York")) - timedelta(days=10)
    v = _view(J, ts=old.isoformat())
    # Simulate a legacy row written before the honesty-window fix.
    with J.journal_path().open("a") as f:
        f.write(json.dumps({"id": v["id"], "ts": datetime.now(
            ZoneInfo("America/New_York")).isoformat(), "type": "stamp",
            "ref_px": 999.0}) + "\n")
    e = J.effective()[v["id"]]
    assert "ref_px" not in e
    assert "outside 2-day" in e["ref_invalid_reason"]


def test_collision_repair_preserves_rows_and_routes_updates(iso_journal):
    J = iso_journal
    p = J.journal_path()
    rows = [
        {"id": "V-X", "ts": "2026-07-02T10:00:00-04:00", "type": "rejected",
         "instrument": "AAA", "status": "rejected"},
        {"id": "V-X", "ts": "2026-07-02T10:01:00-04:00", "type": "stamp",
         "ref_px": 10.0},
        {"id": "V-X", "ts": "2026-07-02T12:00:00-04:00", "type": "rejected",
         "instrument": "BBB", "status": "rejected"},
        {"id": "V-X", "ts": "2026-07-02T12:01:00-04:00", "type": "stamp",
         "ref_px": 20.0},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert not J.integrity_report()["ok"]
    result = J.repair_collisions()
    assert result["changed"] == 1 and result["backup"]
    repaired = J.read_all()
    assert len(repaired) == len(rows)
    eff = J.effective(repaired)
    assert sorted(e["instrument"] for e in eff.values()) == ["AAA", "BBB"]
    bbb = next(e for e in eff.values() if e["instrument"] == "BBB")
    assert bbb["ref_px"] == 20.0


def test_resolve_pending_sets_flag_not_status(iso_journal):
    J = iso_journal
    v = _view(J)
    J.append_raw({"id": v["id"], "ts": "x", "type": "resolve_pending",
                  "hit_level": "target", "hit_px": 101.2, "hit_ts": "x"})
    e = J.effective()[v["id"]]
    assert e["status"] == "open" and e["type"] == "view"
    assert e["resolve_pending"] is True and e["hit_px"] == 101.2


def test_entry_observation_is_data_only_and_enables_calibration(iso_journal):
    J = iso_journal
    import advisor.research.outcomes as O
    importlib.reload(O)
    v = _view(J)
    now = J._now().isoformat()
    J.append_raw({"id": v["id"], "ts": now, "type": "entry_observed",
                  "entry_observed_px": 93.0, "entry_observed_ts": now,
                  "entry_observed_src": "test quote"})
    J.resolve(v["id"], "hit_target", "", {"exit_px": 101.0})
    row = next(r for r in O.resolved_views() if r["id"] == v["id"])
    assert row["type"] == "resolve"  # origin is tracked separately by outcomes
    assert row["entry_observed"] is True
    assert row["calibration_eligible"] is True
    importlib.reload(O)


def test_resolve_with_v2_fields(iso_journal):
    J = iso_journal
    v = _view(J)
    J.resolve(v["id"], "hit_target", "n",
              {"exit_px": 101.2, "realized_r": 2.4,
               "outcome_tag": "thesis_right_win",
               "status": "MUST_NOT_OVERRIDE"})
    e = J.effective()[v["id"]]
    assert e["status"] == "hit_target"          # fields can't hijack status
    assert e["realized_r"] == 2.4 and e["outcome_tag"] == "thesis_right_win"


def test_outcomes_derivation(iso_journal, monkeypatch):
    J = iso_journal
    import advisor.research.outcomes as O
    importlib.reload(O)
    # explicit R
    v1 = _view(J)
    J.resolve(v1["id"], "hit_target", "", {"exit_px": 101.0, "realized_r": 2.0})
    # level-approximated stop: ref 93, stop 88 → -1R by construction
    v2 = _view(J, instrument="IWM", yf_ticker="IWM")
    J.append_raw({"id": v2["id"], "ts": J._now().isoformat(),
                  "type": "stamp", "ref_px": 93.0})
    J.resolve(v2["id"], "stopped", "")
    rows = {r["id"]: r for r in O.resolved_views()}
    assert rows[v1["id"]]["win"] == 1 and rows[v1["id"]]["realized_r"] == 2.0
    assert rows[v2["id"]]["win"] == 0
    assert rows[v2["id"]]["realized_r"] == -1.0
    assert rows[v2["id"]]["r_basis"] == "level-approximated"
    # ambiguous close (no numbers) excluded from win
    v3 = _view(J, instrument="GLD", yf_ticker="GLD")
    J.resolve(v3["id"], "closed", "manual")
    rows = {r["id"]: r for r in O.resolved_views()}
    assert rows[v3["id"]]["win"] is None
    importlib.reload(O)


def test_redteam_check_contract(tmp_path):
    from advisor.research.redteam_check import validate
    draft = tmp_path / "views_draft.json"
    draft.write_text(json.dumps({"views": [{"instrument": "XLE"}]}))
    rt = tmp_path / "redteam.json"
    # missing verdict blocks
    rt.write_text(json.dumps({"verdicts": []}))
    errs, _ = validate(rt, draft)
    assert any("no verdict" in e for e in errs)
    # amend without amended blocks
    rt.write_text(json.dumps({"verdicts": [
        {"instrument": "XLE", "verdict": "amend", "reason": "r"}]}))
    errs, _ = validate(rt, draft)
    assert any("non-empty 'amended'" in e for e in errs)
    # clean amend passes
    rt.write_text(json.dumps({"verdicts": [
        {"instrument": "XLE", "verdict": "amend", "reason": "r",
         "checks": {"evidence_audit": "pass"},
         "amended": {"stop_px": 87.0}}]}))
    errs, warns = validate(rt, draft)
    assert not errs


def test_redteam_check_rejects_prose_in_structured_sizing(tmp_path):
    from advisor.research.redteam_check import validate
    draft = tmp_path / "draft.json"
    rt = tmp_path / "redteam.json"
    draft.write_text(json.dumps({"views": [{"instrument": "MTRN"}]}))
    rt.write_text(json.dumps({"verdicts": [{"instrument": "MTRN",
        "verdict": "amend", "reason": "resize", "checks": ["risk"],
        "amended": {"sizing": "reduce to eleven shares"}}]}))
    errors, _ = validate(rt, draft)
    assert any("sizing must be an object" in error for error in errors)


def test_brief_check_production_fields(tmp_path):
    from advisor.brief_check import validate
    p = tmp_path / "brief.json"
    view = {"instrument": "XLE", "direction": "long", "conviction": "high",
            "thesis": "t", "entry": "92", "target": "101", "stop": "88",
            "entry_px_low": 92.0, "entry_px_high": 93.0,
            "target_px": 101.0, "stop_px": 88.0, "yf_ticker": "XLE",
            "evidence": [{"claim": "c", "url": "https://x.test/a",
                          "retrieved": "now", "primary": True},
                         {"claim": "d", "url": "https://y.test/b",
                          "retrieved": "now", "primary": False}]}
    # p_win out of range = error
    p.write_text(json.dumps({"schema_version": 3,
                             "views": [{**view, "p_win": 0.95}]}))
    errs, _ = validate(p)
    assert any("p_win" in e for e in errs)
    # Missing attribution fields fail closed under the production contract.
    p.write_text(json.dumps({"schema_version": 3,
                             "portfolio_context": {"status": "unavailable"},
                             "views": [view]}))
    errs, warns = validate(p)
    assert any("production contract" in e for e in errs)
    assert any("source" in e for e in errs) and any("p_win" in e for e in errs)
    # rejected entries need idea+killed_by
    p.write_text(json.dumps({"views": [], "rejected": [{"idea": "x"}]}))
    errs, _ = validate(p)
    assert any("killed_by" in e for e in errs)


def test_complete_production_view_passes(tmp_path):
    from advisor.brief_check import validate
    p = tmp_path / "brief.json"
    rr, p_win = 2.125, 0.60
    view = {
        "decision_key": "2026-07-16:XLE:long",
        "instrument": "XLE", "yf_ticker": "XLE", "direction": "long",
        "conviction": "high", "p_win": p_win, "source": "factor_long",
        "probability_basis": {"type": "analyst_judgment", "calibrated": False,
                              "sample_n": 0, "method": "scenario judgment"},
        "recommendation_class": "research_idea",
        "thesis_tags": ["momentum"], "data_as_of": "2026-07-16T08:55:00-04:00",
        "thesis": "Current, falsifiable thesis.", "entry": "92-93",
        "entry_px_low": 92.0, "entry_px_high": 93.0, "target": "101",
        "target_px": 101.0, "stop": "88.5", "stop_px": 88.5,
        "time_stop": "2026-08-01", "reward_risk": rr,
        "expected_value_r": round(p_win * rr - (1 - p_win), 3),
        "sizing": {"capital_usd": 740, "max_loss_usd": 34,
                   "portfolio_capital_after_usd": 740, "quantity": 8,
                   "instrument_type": "etf", "slippage_bps": 25,
                   "method": "stop-risk"},
        "catalyst": {"event": "earnings", "date": "2026-07-25",
                     "mechanism": "revision cycle"},
        "disconfirmers": ["revisions reverse", "relative strength breaks"],
        "evidence": [
            {"claim": "filing", "url": "https://sec.gov/a",
             "retrieved": "2026-07-16T08:50:00-04:00",
             "primary": True},
            {"claim": "market", "url": "https://exchange.test/b",
             "retrieved": "2026-07-16T08:51:00-04:00",
             "primary": False}],
    }
    p.write_text(json.dumps({"schema_version": 3,
                             "portfolio_context": {"status": "unavailable"},
                             "views": [view]}))
    errs, _ = validate(p)
    assert not errs


def test_uncalibrated_probability_cannot_be_actionable(tmp_path):
    from advisor.brief_check import validate
    p = tmp_path / "brief.json"
    rr, p_win = 2.125, 0.60
    view = {
        "decision_key": "2026-07-16:XLE:long", "instrument": "XLE",
        "yf_ticker": "XLE", "direction": "long", "conviction": "high",
        "p_win": p_win, "source": "other", "thesis_tags": ["catalyst"],
        "data_as_of": "2026-07-16T08:55:00-04:00", "thesis": "Test thesis",
        "entry": "92-93", "entry_px_low": 92.0, "entry_px_high": 93.0,
        "target": "101", "target_px": 101.0, "stop": "88.5", "stop_px": 88.5,
        "time_stop": "2026-08-01", "reward_risk": rr,
        "expected_value_r": round(p_win * rr - (1 - p_win), 3),
        "probability_basis": {"type": "analyst_judgment", "calibrated": False,
                              "sample_n": 0, "method": "scenario"},
        "recommendation_class": "actionable_idea",
        "sizing": {"capital_usd": 740, "max_loss_usd": 34,
                   "portfolio_capital_after_usd": 740, "quantity": 8,
                   "instrument_type": "etf", "slippage_bps": 25,
                   "method": "stop-risk"},
        "catalyst": {"event": "earnings", "date": "2026-07-25",
                     "mechanism": "revision cycle"},
        "disconfirmers": ["revisions reverse", "relative strength breaks"],
        "evidence": [{"claim": "filing", "url": "https://sec.gov/a",
                      "retrieved": "2026-07-16T08:50:00-04:00", "primary": True},
                     {"claim": "market", "url": "https://exchange.test/b",
                      "retrieved": "2026-07-16T08:51:00-04:00", "primary": False}],
    }
    p.write_text(json.dumps({"schema_version": 3, "views": [view]}))
    errs, _ = validate(p)
    assert any("actionable_idea requires a calibrated probability" in e for e in errs)


def test_brief_check_rejects_incoherent_entry_levels(tmp_path):
    from advisor.brief_check import validate
    p = tmp_path / "brief.json"
    base = {"instrument": "XLE", "direction": "long", "conviction": "high",
            "thesis": "t", "entry": "100", "target": "120", "stop": "110",
            "entry_px_low": 100.0, "entry_px_high": 101.0,
            "target_px": 120.0, "stop_px": 110.0, "yf_ticker": "XLE",
            "evidence": [{"claim": "c", "url": "https://x.test/a",
                          "retrieved": "now", "primary": True},
                         {"claim": "d", "url": "https://y.test/b",
                          "retrieved": "now", "primary": False}]}
    p.write_text(json.dumps({"schema_version": 3, "views": [base]}))
    errs, _ = validate(p)
    assert any("stop < entry_low" in e for e in errs)

    short = {**base, "direction": "short", "entry": "100",
             "target": "90", "stop": "95", "target_px": 90.0, "stop_px": 95.0}
    p.write_text(json.dumps({"schema_version": 3, "views": [short]}))
    errs, _ = validate(p)
    assert any("entry_high < stop" in e for e in errs)
