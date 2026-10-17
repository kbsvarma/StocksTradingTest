"""End-to-end publication test on a FROZEN FIXTURE (audit finding F8).

Everything else in this suite is a unit test around a piece. On 2026-09-04 the
pipeline failed closed after all three model stages had succeeded and $4.19 had
been spent, and NOT ONE existing test caught it — because both bugs lived in
the seam between components:

  1. `view.update(verdict["amended"])` is SHALLOW, so a red-team amendment
     carrying {quantity, capital_usd, max_loss_usd} replaced the whole sizing
     block and destroyed portfolio_capital_after_usd, instrument_type,
     slippage_bps and method.
  2. portfolio_capital_after_usd is DERIVED. Nothing recomputed it after code
     changed capital_usd, so it no longer reconciled.

Both are only reachable on the `amend` path — the red-team almost always kills
outright, which is why they survived.

The fixture in tests/fixtures/pipeline_e2e/ is the REAL 2026-09-04 draft and
red-team output, prose trimmed, field shapes untouched. It walks
assemble() -> brief_check.validate() exactly as the orchestrator does, so this
class of seam bug cannot come back silently.
"""
import copy
import json
import shutil
from pathlib import Path

import pytest

from advisor.brief_check import validate as validate_brief
from advisor.publication_assembly import assemble

FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_e2e"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """A context dir seeded from the frozen fixture."""
    d = tmp_path / "2026-09-04"
    d.mkdir()
    for f in FIXTURE.iterdir():
        shutil.copy(f, d / f.name)
    # no open positions: the derived exposure is then just what this brief
    # deploys, which keeps the reconciliation arithmetic checkable by hand
    import advisor.brief_check as bc
    monkeypatch.setattr(bc, "_existing_open_capital", lambda keys: 0.0)
    return d


def _load(ctx_dir):
    return json.loads((ctx_dir / "brief.pending.json").read_text())


# --- the whole point --------------------------------------------------------

def test_pipeline_assembles_and_validates_end_to_end(ctx):
    """The exact run that failed in production must now publish."""
    out = assemble(ctx)
    assert out.name == "brief.pending.json"
    errs, warns = validate_brief(out)
    assert errs == [], errs


def test_amended_view_survives_with_its_full_sizing_contract(ctx):
    assemble(ctx)
    view = _load(ctx)["views"][0]
    assert view["yf_ticker"] == "DELL"
    for key in ("capital_usd", "max_loss_usd", "portfolio_capital_after_usd",
                "quantity", "instrument_type", "slippage_bps", "method"):
        assert key in view["sizing"], f"{key} lost in the merge"


def test_the_amendment_actually_applied(ctx):
    """Preserving the block must not mean ignoring the red-team."""
    assemble(ctx)
    sizing = _load(ctx)["views"][0]["sizing"]
    assert sizing["quantity"] == 15          # amended down from 20
    assert sizing["capital_usd"] == 7710


def test_derived_exposure_reconciles_after_the_amendment(ctx):
    assemble(ctx)
    views = _load(ctx)["views"]
    deployed = sum(v["sizing"]["capital_usd"] for v in views)
    for v in views:
        assert v["sizing"]["portfolio_capital_after_usd"] == pytest.approx(deployed)


def test_red_team_amendment_note_is_recorded_on_the_thesis(ctx):
    assemble(ctx)
    assert "red-team amended" in _load(ctx)["views"][0]["thesis"]


# --- the two regressions, driven from the seam ------------------------------

def test_shallow_merge_would_fail_this_test(ctx, monkeypatch):
    """Proof the fixture reproduces bug 1 — swap the deep merge for the old
    shallow update and publication must break exactly as it did."""
    import advisor.publication_assembly as pa
    monkeypatch.setattr(pa, "_deep_update",
                        lambda base, patch: base.update(copy.deepcopy(patch)) or base)
    with pytest.raises(ValueError, match="sizing requires"):
        assemble(ctx)


def test_stale_derived_exposure_would_fail_this_test(ctx, monkeypatch):
    """Proof the fixture reproduces bug 2 — stop recomputing the derived
    field and the brief must fail to reconcile."""
    import advisor.publication_assembly as pa
    monkeypatch.setattr(pa, "_recompute_after_capital", lambda views: None)
    with pytest.raises(ValueError, match="does not reconcile"):
        assemble(ctx)


# --- structural guarantees --------------------------------------------------

def test_killed_ideas_are_carried_into_rejected_not_dropped(ctx):
    assemble(ctx)
    brief = _load(ctx)
    draft = json.loads((ctx / "views_draft.json").read_text())
    assert len(brief["rejected"]) >= len(draft.get("rejected") or [])


def test_assembly_refuses_a_verdict_it_has_no_view_for(ctx):
    rt = json.loads((ctx / "redteam.json").read_text())
    rt["verdicts"] = []
    (ctx / "redteam.json").write_text(json.dumps(rt))
    with pytest.raises(Exception):
        assemble(ctx)


def test_assembly_is_deterministic(ctx):
    assemble(ctx)
    first = (ctx / "brief.pending.json").read_text()
    assemble(ctx)
    assert (ctx / "brief.pending.json").read_text() == first


def test_no_brief_is_written_when_validation_fails(ctx, monkeypatch):
    """Fail CLOSED: a rejected brief must not leave a partial artifact that a
    later stage could mistake for a published one."""
    import advisor.publication_assembly as pa
    monkeypatch.setattr(pa, "_recompute_after_capital", lambda views: None)
    with pytest.raises(ValueError):
        assemble(ctx)
    assert not (ctx / "brief.json").exists()
