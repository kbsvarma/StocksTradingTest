"""Regressions for the fabrication audit.

For an LLM research product the existential risk is fabrication, not drawdown,
and the promise "every claim is independently re-verified" was an architecture
claim with no number behind it.

The tests that matter most here are the FALSE-POSITIVE ones. A naive version of
this detector reported a 26% fabrication rate on real data; every one of those
findings was a bug in the detector, not a lie by the model:

  * a `$\\d+\\.\\d{2}` pattern compared EVERY dollar figure to spot, so CAKE's
    "analyst mean target ($92.67) sits below spot ($108.57)" — a correct
    sentence — was flagged. 35 of 36 apparent contradictions.
  * a basket claim, "GSHD/KMPR/COIN/CRK ... score -2.2 to -2.4", was pinned to
    GSHD alone, whose actual -2.36 is inside the stated range.

A metric that cries wolf is worse than no metric, so the detector is
precision-first and anything ambiguous is `unverifiable`, never `contradicted`.
"""
import json

import pytest

from advisor.research import fabrication_audit as fa


TRUTH = {
    "by_ticker": {
        "CAKE": {"score": 1.87, "px": 108.57, "prox_52w_pct": 92.6},
        "GSHD": {"score": -2.36, "px": 50.0},
        "KMPR": {"score": -2.30, "px": 40.0},
    },
    "panel_build_id": "20260903T100152Z",
    "source": "test",
}


def _verdicts(text, ticker="CAKE"):
    return {(c["kind"], c["verdict"]) for c in fa._check(text, ticker, TRUTH)}


# --- the checks that work ---------------------------------------------------

def test_correct_score_is_verified():
    assert ("factor_score", "verified") in _verdicts("momentum flag (score 1.87)")


def test_wrong_score_is_contradicted():
    assert ("factor_score", "contradicted") in _verdicts("(score 1.42)")


def test_price_with_an_explicit_cue_is_verified():
    assert ("price", "verified") in _verdicts("price ($108.57) unchanged")
    assert ("price", "verified") in _verdicts("trading at $108.57")


def test_wrong_price_is_contradicted():
    assert ("price", "contradicted") in _verdicts("last close $95.00")


def test_panel_build_id_is_checked():
    assert ("panel_build_id", "verified") in _verdicts(
        "identical factor panel (panel_build_id 20260903T100152Z)")
    assert ("panel_build_id", "contradicted") in _verdicts(
        "panel_build_id 19990101T000000Z")


def test_pct_of_52w_high_is_checked():
    assert ("pct_of_52w_high", "verified") in _verdicts("at 92.6% of 52w high")


# --- the false positives that forced the design ----------------------------

def test_analyst_target_is_not_treated_as_the_price():
    """The bug that produced 35 of 36 bogus contradictions."""
    text = "analyst mean target ($92.67) sits below spot ($108.57)"
    v = fa._check(text, "CAKE", TRUTH)
    assert not any(c["verdict"] == "contradicted" for c in v), v
    assert ("price", "verified") in {(c["kind"], c["verdict"]) for c in v}


def test_bare_dollar_figures_are_left_alone():
    """No price cue, no assertion — silence beats a wrong accusation."""
    assert fa._check("raised $500.00 in financing", "CAKE", TRUTH) == []


def test_basket_claims_cannot_be_pinned_to_one_ticker():
    text = "Factor SHORT basket (GSHD/KMPR/COIN) — score -2.2 to -2.4"
    v = fa._check(text, "GSHD", TRUTH)
    assert v and all(c["verdict"] == "unverifiable" for c in v)
    assert "several tickers" in v[0].get("reason", "")


def test_a_stated_range_is_satisfied_from_inside():
    text = "score -2.2 to -2.4 across the sheet"
    v = fa._check(text, "GSHD", TRUTH)          # -2.36 is inside
    assert ("factor_score_range", "verified") in {(c["kind"], c["verdict"])
                                                  for c in v}


def test_a_stated_range_is_contradicted_from_outside():
    text = "score -1.0 to -1.2 across the sheet"
    v = fa._check(text, "GSHD", TRUTH)
    assert ("factor_score_range", "contradicted") in {(c["kind"], c["verdict"])
                                                      for c in v}


def test_unknown_ticker_yields_unverifiable_not_contradicted():
    v = fa._check("(score 1.00)", "NOPE", TRUTH)
    assert v and all(c["verdict"] == "unverifiable" for c in v)


# --- the metric itself ------------------------------------------------------

def test_unverifiable_is_excluded_from_the_denominator(tmp_path, monkeypatch):
    ctx = tmp_path / "context" / "2026-09-03"
    ctx.mkdir(parents=True)
    (ctx / "candidates.json").write_text(json.dumps(
        {"slate": [{"ticker": "CAKE", "detail": {"score": 1.87, "px": 108.57}}]}))
    (ctx / "views_draft.json").write_text(json.dumps({"views": [], "rejected": [
        {"yf_ticker": "CAKE", "idea": "flag (score 1.87)"},
        {"yf_ticker": "ZZZZ", "idea": "flag (score 9.99)"}]}))
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    r = fa.audit_date("2026-09-03")
    assert r["verified"] == 1 and r["contradicted"] == 0
    assert r["unverifiable"] == 1
    assert r["decidable_denominator"] == 1        # the unknown one is excluded
    assert r["fabrication_rate"] == 0.0


def test_rate_is_none_when_nothing_is_decidable(tmp_path, monkeypatch):
    ctx = tmp_path / "context" / "2026-09-03"
    ctx.mkdir(parents=True)
    (ctx / "candidates.json").write_text(json.dumps(
        {"slate": [{"ticker": "AAA", "detail": {"score": 1.0}}]}))
    (ctx / "views_draft.json").write_text(json.dumps(
        {"views": [], "rejected": [{"yf_ticker": "ZZZZ", "idea": "(score 9.99)"}]}))
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    assert fa.audit_date("2026-09-03")["fabrication_rate"] is None


def test_missing_context_is_reported_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    r = fa.audit_date("1999-01-01")
    assert r["usable"] is False


def test_ground_truth_comes_from_the_context_snapshot(tmp_path, monkeypatch):
    """The rule that makes the audit valid at all: the live research artifact
    is rewritten by later rebuilds; the context copy is what the model read."""
    ctx = tmp_path / "context" / "2026-09-03"
    ctx.mkdir(parents=True)
    (ctx / "candidates.json").write_text(json.dumps(
        {"slate": [{"ticker": "DELL", "detail": {"score": 1.99}}]}))
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    t = fa.ground_truth(ctx)
    assert t["by_ticker"]["DELL"]["score"] == 1.99


def test_pending_publication_can_be_audited_exactly(tmp_path, monkeypatch):
    ctx = tmp_path / "context" / "2026-09-03"
    ctx.mkdir(parents=True)
    (ctx / "candidates.json").write_text(json.dumps({"slate": [
        {"ticker": "CAKE", "detail": {"px": 108.57}}]}))
    (ctx / "brief.pending.json").write_text(json.dumps({"views": [], "rejected": [
        {"yf_ticker": "CAKE", "idea": "trading at $95.00"}]}))
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    result = fa.audit_date("2026-09-03", artifacts=("brief.pending.json",))
    assert result["contradicted"] == 1
    assert result["findings"][0]["artifact"] == "brief.pending.json"
    with pytest.raises(ValueError, match="contradicted"):
        fa.publication_gate("2026-09-03")


def test_draft_validator_surfaces_context_contradiction(tmp_path, monkeypatch):
    import advisor.brief_check as check
    ctx = tmp_path / "context" / "2026-09-03"
    ctx.mkdir(parents=True)
    (ctx / "candidates.json").write_text(json.dumps({"slate": [
        {"ticker": "CAKE", "detail": {"px": 108.57}}]}))
    draft = ctx / "views_draft.json"
    draft.write_text(json.dumps({"schema_version": 2, "views": [],
        "rejected": [{"yf_ticker": "CAKE", "idea": "trading at $95.00",
                      "killed_by": "valuation"}]}))
    monkeypatch.setattr(fa, "CONTEXT", tmp_path / "context")
    errors, _ = check.validate(draft)
    assert any("numeric claim contradicted" in error for error in errors)
