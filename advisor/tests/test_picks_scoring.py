"""End-to-end regressions for pick selection.

The defect: `picks.build` ranked on `detail.score`, populated only by
tactical_long/tactical_short/new_entrant. Measured over 20 archived slates,
346/681 candidates (50.8%) could never be picked and 200/200 published picks
carried a price bucket — the stratified slate collapsed into a momentum list.

These tests drive the real `build()` against a synthetic panel and slate.
"""
import json

import pandas as pd
import pytest

from advisor.research import picks


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point picks at a scratch data dir and a deterministic price panel."""
    research = tmp_path / "research"
    research.mkdir(parents=True)
    monkeypatch.setattr(picks, "RESEARCH_DIR", research)
    monkeypatch.setattr(picks, "OUT", research / "picks_latest.json")
    monkeypatch.setattr(picks, "LEDGER", tmp_path / "picks_ledger.jsonl")
    monkeypatch.setattr(picks, "CALIBRATION", research / "pick_calibration.json")
    monkeypatch.setattr(picks, "PRIORS", research / "generator_priors.json")

    tickers = ["MOMO", "FUND", "BOTH", "NODIR"]
    idx = pd.bdate_range("2026-01-01", periods=60)
    close = pd.DataFrame(100.0, index=idx, columns=tickers)
    high = close + 2.0
    low = close - 2.0

    def fake_panel(field):
        return {"close": close, "high": high, "low": low}[field]

    import advisor.research.datastore as ds
    monkeypatch.setattr(ds, "load_panel", fake_panel)
    return research


def _slate(research, entries, **extra):
    doc = {"as_of": "2026-09-03T08:00:00-04:00", "n": len(entries),
           "slate": entries, **extra}
    (research / "candidates_latest.json").write_text(json.dumps(doc))
    return doc


def _entry(ticker, gens, detail=None):
    return {"ticker": ticker, "buckets": list(gens), "generators": gens,
            "detail": detail or {}}


# --- the headline regression ----------------------------------------------

def test_fundamental_only_candidate_gets_published(env):
    """Under v1 this name scored 0.0875 and could never clear the cut."""
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long",
                            "metric": "est_revision", "value": 7.1,
                            "rank_basis": "within 812 covered names"}})])
    res = picks.build(top_n=10)
    assert [p["ticker"] for p in res["picks"]] == ["FUND"]
    p = res["picks"][0]
    assert p["direction"] == "long"
    assert p["selection"]["lead_bucket"] == "revision_leader"
    assert p["scoring_version"] == picks.SCORING_VERSION


def test_fundamental_outranks_weaker_momentum(env):
    """Equal footing means a 98th-pctile revision beats a 60th-pctile trend."""
    _slate(env, [
        _entry("MOMO", {"tactical_long": {"rank_pct": 0.60, "direction": "long"}},
               detail={"score": 1.9}),
        _entry("FUND", {"revision_leader": {"rank_pct": 0.98, "direction": "long"}}),
    ])
    res = picks.build(top_n=10)
    assert [p["ticker"] for p in res["picks"]] == ["FUND", "MOMO"]


def test_cross_family_confluence_beats_a_single_family(env):
    _slate(env, [
        _entry("MOMO", {"tactical_long": {"rank_pct": 0.90, "direction": "long"},
                        "new_entrant": {"rank_pct": 0.90, "direction": None},
                        "technical_setup": {"rank_pct": 0.90, "direction": None}}),
        _entry("BOTH", {"tactical_long": {"rank_pct": 0.90, "direction": "long"},
                        "revision_leader": {"rank_pct": 0.90, "direction": "long"}}),
    ])
    res = picks.build(top_n=10)
    assert [p["ticker"] for p in res["picks"]] == ["BOTH", "MOMO"]
    assert res["picks"][0]["selection"]["n_families"] == 2
    assert res["picks"][1]["selection"]["n_families"] == 1


# --- refusing to guess -----------------------------------------------------

def test_directionless_candidate_is_excluded_not_defaulted_long(env):
    _slate(env, [
        _entry("NODIR", {"squeeze_flag": {"rank_pct": 0.99, "direction": None}}),
        _entry("FUND", {"revision_leader": {"rank_pct": 0.5, "direction": "long"}}),
    ])
    res = picks.build(top_n=10)
    assert [p["ticker"] for p in res["picks"]] == ["FUND"]
    assert res["n_unpickable"] == 1
    assert res["unpickable"][0]["ticker"] == "NODIR"


def test_short_direction_inverts_the_levels(env):
    _slate(env, [_entry("MOMO", {
        "tactical_short": {"rank_pct": 0.97, "direction": "short"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["direction"] == "short"
    assert p["stop"] > p["ref_px"] > p["target"]


def test_long_direction_orders_the_levels_normally(env):
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.97, "direction": "long"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["stop"] < p["ref_px"] < p["target"]


# --- version separation ----------------------------------------------------

def test_archived_v1_slate_replays_on_the_v1_formula(env):
    """Backfill must stay faithful to what was actually published then."""
    _slate(env, [{"ticker": "MOMO", "buckets": ["tactical_long"],
                  "detail": {"score": 1.9}}])
    res = picks.build(top_n=10)
    assert res["scoring_version"] == 1
    assert res["picks"][0]["selection"]["lead_bucket"] is None
    assert "v1 legacy" in res["picks"][0]["selection"]["formula"]


def test_ledger_row_carries_attribution_keys(env):
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    picks.build(top_n=10)
    row = json.loads(picks.LEDGER.read_text().splitlines()[0])
    assert row["lead_bucket"] == "revision_leader"
    assert row["lead_rank_pct"] == 0.98
    assert row["scoring_version"] == picks.SCORING_VERSION
    assert row["n_families"] == 1
    assert row["selection"]["contributions"]["revision_leader"]["standalone"]


# --- health passthrough ----------------------------------------------------

def test_generator_health_and_breadth_warning_reach_the_pick_file(env):
    _slate(env,
           [_entry("MOMO", {"tactical_long": {"rank_pct": 0.9, "direction": "long"}})],
           generator_health={"revision_leader": {"live": False, "n": 0,
                                                 "reason": "estimates throttled"}},
           generators_dark={"revision_leader": "estimates throttled"},
           families_live=["price_momentum"],
           breadth_warning="SINGLE-FAMILY SLATE — price_momentum only")
    res = picks.build(top_n=10)
    assert res["generators_dark"] == {"revision_leader": "estimates throttled"}
    assert "SINGLE-FAMILY" in res["breadth_warning"]


def test_lead_bucket_mix_summarises_the_day(env):
    _slate(env, [
        _entry("MOMO", {"tactical_long": {"rank_pct": 0.9, "direction": "long"}}),
        _entry("BOTH", {"tactical_long": {"rank_pct": 0.8, "direction": "long"}}),
        _entry("FUND", {"revision_leader": {"rank_pct": 0.95, "direction": "long"}}),
    ])
    res = picks.build(top_n=10)
    assert res["lead_bucket_mix"] == {"tactical_long": 2, "revision_leader": 1}


# --- priors ---------------------------------------------------------------

def test_fitted_priors_change_the_ordering(env):
    slate = [
        _entry("MOMO", {"tactical_long": {"rank_pct": 0.90, "direction": "long"}}),
        _entry("FUND", {"revision_leader": {"rank_pct": 0.85, "direction": "long"}}),
    ]
    _slate(env, slate)
    assert [p["ticker"] for p in picks.build(top_n=10)["picks"]] == ["MOMO", "FUND"]

    (env / "generator_priors.json").write_text(json.dumps(
        {"priors": {"tactical_long": 0.7, "revision_leader": 1.3}}))
    _slate(env, slate)
    res = picks.build(top_n=10)
    assert [p["ticker"] for p in res["picks"]] == ["FUND", "MOMO"]
    assert res["picks"][0]["selection"]["lead_prior"] == 1.3


def test_uncalibrated_score_is_never_shown_as_a_probability(env):
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["confidence_pct"] is None
    assert p["confidence_basis"] == "uncalibrated"


# --- calibration must not cross scoring versions ---------------------------

def test_v1_calibration_is_refused_for_v2_scores(env):
    """A v1-fitted score->hit mapping applied to a v2 score is a fabrication."""
    (env / "pick_calibration.json").write_text(json.dumps({
        "scoring_version": 1, "usable": True,
        "buckets": [{"lo": 0.0, "hi": 1.01, "hit_rate": 0.243, "n": 177}],
        "calibration_id": "deadbeef"}))
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["confidence_pct"] is None
    assert "scoring_version=1" in p["confidence_basis"]


def test_matching_version_calibration_is_applied(env):
    (env / "pick_calibration.json").write_text(json.dumps({
        "scoring_version": picks.SCORING_VERSION, "usable": True,
        "buckets": [{"lo": 0.0, "hi": 1.01, "hit_rate": 0.31, "n": 60}],
        "calibration_id": "abc123"}))
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["confidence_pct"] == 31.0


# --- portfolio construction -----------------------------------------------

def _e(ticker, gens, sector=None, detail=None):
    d = dict(detail or {})
    if sector:
        d["sector"] = sector
    return _entry(ticker, gens, d)


def test_sector_cap_limits_concentration(env, monkeypatch):
    from advisor.research import picks as P
    monkeypatch.setattr(P, "MAX_PAIR_CORR", 1.01)     # isolate the sector rule
    _slate(env, [
        _e(t, {"tactical_long": {"rank_pct": 0.99 - i * 0.01, "direction": "long"}},
           sector="Information Technology")
        for i, t in enumerate(["MOMO", "FUND", "BOTH", "NODIR"])])
    res = P.build(top_n=10)
    assert len(res["picks"]) == P.MAX_PER_SECTOR
    assert res["n_constrained_out"] == 1
    assert "sector cap" in res["constrained_out"][0]["reason"]


def test_generator_cap_limits_one_generator(env, monkeypatch):
    from advisor.research import picks as P
    monkeypatch.setattr(P, "MAX_PAIR_CORR", 1.01)
    monkeypatch.setattr(P, "MAX_PER_SECTOR", 99)
    monkeypatch.setattr(P, "MAX_PER_LEAD", 2)
    _slate(env, [
        _e(t, {"tactical_long": {"rank_pct": 0.99 - i * 0.01, "direction": "long"}},
           sector=f"S{i}") for i, t in enumerate(["MOMO", "FUND", "BOTH", "NODIR"])])
    res = P.build(top_n=10)
    assert len(res["picks"]) == 2
    assert "generator cap" in res["constrained_out"][0]["reason"]


def test_every_rejection_names_its_binding_constraint(env, monkeypatch):
    from advisor.research import picks as P
    monkeypatch.setattr(P, "MAX_PAIR_CORR", 1.01)
    _slate(env, [
        _e(t, {"tactical_long": {"rank_pct": 0.99 - i * 0.01, "direction": "long"}},
           sector="Energy") for i, t in enumerate(["MOMO", "FUND", "BOTH", "NODIR"])])
    for d in P.build(top_n=10)["constrained_out"]:
        assert d["reason"] and d["ticker"] and "lead_bucket" in d


def test_constraints_are_published_with_the_slate(env):
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.9, "direction": "long"}})])
    c = picks.build(top_n=10)["constraints"]
    assert c["max_per_sector"] == picks.MAX_PER_SECTOR
    assert c["max_pairwise_corr"] == picks.MAX_PAIR_CORR


# --- cost screen ----------------------------------------------------------

def test_uneconomic_pick_is_dropped_and_explained(env, monkeypatch):
    from advisor.research import picks as P
    # a 3*ATR target on a synthetic flat panel is tiny; force a huge cost
    monkeypatch.setattr("advisor.research.costs.estimate",
                        lambda tickers=None, **kw: {t: {"round_trip_bps": 50000.0}
                                                    for t in tickers})
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    res = P.build(top_n=10)
    assert res["n_picks"] == 0
    assert res["n_uneconomic"] == 1
    assert "round trip" in res["uneconomic"][0]["reason"]


def test_cost_is_attached_to_every_published_pick(env, monkeypatch):
    monkeypatch.setattr("advisor.research.costs.estimate",
                        lambda tickers=None, **kw: {t: {"round_trip_bps": 5.0}
                                                    for t in tickers})
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    p = picks.build(top_n=10)["picks"][0]
    assert p["cost"]["round_trip_bps"] == 5.0
    assert p["cost_screen"]["ok"] is True


def test_cost_model_failure_does_not_empty_the_slate(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("panel unavailable")
    monkeypatch.setattr("advisor.research.costs.estimate", boom)
    _slate(env, [_entry("FUND", {
        "revision_leader": {"rank_pct": 0.98, "direction": "long"}})])
    res = picks.build(top_n=10)
    assert res["n_picks"] == 1               # fails OPEN
