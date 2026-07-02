"""Learning-loop v1 tests: journal v2 semantics, outcome derivation,
redteam contract. All filesystem-isolated via ADVISOR_DATA_DIR; no network.
"""
import importlib
import json
import os
import sys
from pathlib import Path

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


def test_stamp_row_merges_data_only(iso_journal):
    J = iso_journal
    v = _view(J)
    J.append_raw({"id": v["id"], "ts": "2026-07-02T09:40:00-04:00",
                  "type": "stamp", "ref_px": 93.1, "ref_src": "test"})
    e = J.effective()[v["id"]]
    assert e["ref_px"] == 93.1
    assert e["type"] == "view" and e["status"] == "open"


def test_resolve_pending_sets_flag_not_status(iso_journal):
    J = iso_journal
    v = _view(J)
    J.append_raw({"id": v["id"], "ts": "x", "type": "resolve_pending",
                  "hit_level": "target", "hit_px": 101.2, "hit_ts": "x"})
    e = J.effective()[v["id"]]
    assert e["status"] == "open" and e["type"] == "view"
    assert e["resolve_pending"] is True and e["hit_px"] == 101.2


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
    J.append_raw({"id": v2["id"], "ts": "x", "type": "stamp", "ref_px": 93.0})
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


def test_brief_check_v2_fields(tmp_path):
    from advisor.brief_check import validate
    p = tmp_path / "brief.json"
    view = {"instrument": "XLE", "direction": "long", "conviction": "high",
            "thesis": "t", "entry": "92", "target": "101", "stop": "88",
            "target_px": 101.0, "stop_px": 88.0, "yf_ticker": "XLE",
            "evidence": [{"claim": "c", "url": "https://x"}]}
    # p_win out of range = error
    p.write_text(json.dumps({"views": [{**view, "p_win": 0.95}]}))
    errs, _ = validate(p)
    assert any("p_win" in e for e in errs)
    # missing v2 fields warn, don't error (week-1 rollout)
    p.write_text(json.dumps({"views": [view]}))
    errs, warns = validate(p)
    assert not errs
    assert any("source" in w for w in warns) and any("p_win" in w for w in warns)
    # rejected entries need idea+killed_by
    p.write_text(json.dumps({"views": [], "rejected": [{"idea": "x"}]}))
    errs, _ = validate(p)
    assert any("killed_by" in e for e in errs)
