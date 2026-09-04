"""Regressions for the generator registry — the common currency that lets a
fundamental candidate compete with a momentum one.

Guards the specific defect this layer was built to fix: `picks.py` ranked on
`detail.score`, a field only the three price generators populated, so 50.8% of
every slate scored ~0.09 against a ~0.60 cutoff and 200/200 published picks
carried a price bucket.
"""
import pytest

from advisor.research import generators as gen


# --- the currency itself ---------------------------------------------------

def test_pct_rank_is_a_percentile_not_a_raw_value():
    pop = {f"T{i}": float(i) for i in range(100)}
    assert gen.pct_rank(pop, "T99") > 0.98
    assert gen.pct_rank(pop, "T0") < 0.02
    assert 0.45 < gen.pct_rank(pop, "T50") < 0.55


def test_pct_rank_inverts_for_short_side():
    pop = {"A": 10.0, "B": 5.0, "C": 1.0}
    assert gen.pct_rank(pop, "C", higher_is_stronger=False) > \
           gen.pct_rank(pop, "A", higher_is_stronger=False)


def test_pct_rank_handles_ties_without_manufacturing_separation():
    pop = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0}
    vals = {gen.pct_rank(pop, t) for t in pop}
    assert len(vals) == 1          # identical metrics -> identical percentile


def test_pct_rank_degenerate_inputs_return_none():
    assert gen.pct_rank({}, "A") is None
    assert gen.pct_rank({"A": 1.0}, "A") is None        # population of one
    assert gen.pct_rank({"A": 1.0, "B": 2.0}, "Z") is None
    assert gen.pct_rank({"A": None, "B": 2.0}, "A") is None


# --- the crowding fix ------------------------------------------------------

def test_price_buckets_collapse_to_one_family():
    """Three views of one price series must not read as three confirmations."""
    assert gen.families(["tactical_long", "new_entrant", "technical_setup"]) \
        == ["price_momentum"]
    assert gen.confluence(["tactical_long", "new_entrant", "technical_setup"]) == 0.0


def test_confluence_rewards_independent_families_only():
    assert gen.confluence(["tactical_long"]) == 0.0
    assert gen.confluence(["tactical_long", "revision_leader"]) == 0.5
    assert gen.confluence(["tactical_long", "revision_leader",
                           "insider_cluster"]) == 1.0
    # saturates — a fourth family cannot buy more than the third
    assert gen.confluence(["tactical_long", "revision_leader",
                           "insider_cluster", "cheap_quality"]) == 1.0


def test_every_bucket_has_a_family_direction_and_standalone_entry():
    for b in gen.ALL_BUCKETS:
        assert b in gen.FAMILY
        assert b in gen.DIRECTION
        assert b in gen.STANDALONE


# --- direction discipline --------------------------------------------------

def test_signed_generator_reads_direction_from_its_metric():
    assert gen.resolve_direction("pead_fresh", 2.4) == "long"
    assert gen.resolve_direction("pead_fresh", -2.4) == "short"
    assert gen.resolve_direction("pead_fresh", 0) is None
    assert gen.resolve_direction("pead_fresh", None) is None


def test_non_directional_generators_claim_no_direction():
    for b in ("squeeze_flag", "technical_setup", "new_entrant"):
        assert gen.resolve_direction(b, 99) is None


# --- scoring ---------------------------------------------------------------

def _g(rank_pct, direction, **kw):
    return {"rank_pct": rank_pct, "direction": direction, **kw}


def test_fundamental_only_candidate_is_now_pickable():
    """THE REGRESSION. Under v1 this scored 0.0875 against a ~0.60 cutoff."""
    sel = gen.score_candidate({"revision_leader": _g(0.99, "long")})
    assert sel is not None
    assert sel["lead_bucket"] == "revision_leader"
    assert sel["direction"] == "long"
    assert sel["score"] > 0.70


def test_equal_percentiles_score_equally_across_families():
    a = gen.score_candidate({"tactical_long": _g(0.97, "long")})
    b = gen.score_candidate({"revision_leader": _g(0.97, "long")})
    assert a["score"] == pytest.approx(b["score"])


def test_no_standalone_generator_means_not_pickable():
    """We refuse to guess a direction rather than defaulting to long."""
    assert gen.score_candidate({"squeeze_flag": _g(0.99, None)}) is None
    assert gen.score_candidate({"technical_setup": _g(0.99, None)}) is None
    assert gen.score_candidate({}) is None


def test_standalone_bucket_without_a_percentile_cannot_lead():
    assert gen.score_candidate({"revision_leader": _g(None, "long")}) is None


def test_lead_is_the_strongest_eligible_generator():
    sel = gen.score_candidate({
        "tactical_long": _g(0.80, "long"),
        "revision_leader": _g(0.95, "long"),
        "squeeze_flag": _g(0.99, None),      # strongest, but may not lead
    })
    assert sel["lead_bucket"] == "revision_leader"
    assert sel["direction_source"] == "revision_leader"


def test_direction_comes_from_the_lead_generator():
    sel = gen.score_candidate({
        "tactical_short": _g(0.99, "short"),
        "cheap_quality": _g(0.50, "long"),
    })
    assert sel["lead_bucket"] == "tactical_short"
    assert sel["direction"] == "short"


def test_contributions_record_every_generator_including_non_leaders():
    sel = gen.score_candidate({
        "tactical_long": _g(0.80, "long", metric="composite_z", value=1.7,
                            rank_basis="within 1440 liquid names"),
        "squeeze_flag": _g(0.99, None),
    })
    c = sel["contributions"]
    assert set(c) == {"tactical_long", "squeeze_flag"}
    assert c["squeeze_flag"]["eligible_to_lead"] is False
    assert c["tactical_long"]["rank_basis"] == "within 1440 liquid names"
    assert c["tactical_long"]["family"] == "price_momentum"


def test_score_never_exceeds_one():
    sel = gen.score_candidate(
        {"tactical_long": _g(1.0, "long"), "revision_leader": _g(1.0, "long"),
         "insider_cluster": _g(1.0, "long")},
        priors={"tactical_long": 1.4, "revision_leader": 1.4})
    assert sel["score"] <= 1.0


# --- priors ----------------------------------------------------------------

def test_priors_are_neutral_below_the_sample_floor():
    p = gen.fit_priors({"revision_leader": {"n": 12, "hit_rate": 0.9}}, 0.25)
    assert p["revision_leader"] == 1.0


def test_priors_are_shrunk_and_clipped_above_the_floor():
    p = gen.fit_priors({"x": {"n": 200, "hit_rate": 0.50}}, 0.25)
    assert 1.0 < p["x"] <= gen.PRIOR_HI       # rewarded but bounded
    q = gen.fit_priors({"y": {"n": 200, "hit_rate": 0.01}}, 0.25)
    assert gen.PRIOR_LO <= q["y"] < 1.0       # penalised but never switched off


def test_prior_shrinkage_increases_with_sample_size():
    small = gen.fit_priors({"x": {"n": 30, "hit_rate": 0.50}}, 0.25)["x"]
    large = gen.fit_priors({"x": {"n": 500, "hit_rate": 0.50}}, 0.25)["x"]
    assert abs(large - 1.0) > abs(small - 1.0)


def test_priors_neutral_when_baseline_missing():
    assert gen.fit_priors({"x": {"n": 999, "hit_rate": 0.9}}, 0.0)["x"] == 1.0


def test_eligible_to_lead_is_a_boolean_not_the_direction_string():
    sel = gen.score_candidate({"revision_leader": _g(0.9, "long")})
    assert sel["contributions"]["revision_leader"]["eligible_to_lead"] is True
    sel2 = gen.score_candidate({"revision_leader": _g(0.9, "long"),
                                "squeeze_flag": _g(0.99, None)})
    assert sel2["contributions"]["squeeze_flag"]["eligible_to_lead"] is False
