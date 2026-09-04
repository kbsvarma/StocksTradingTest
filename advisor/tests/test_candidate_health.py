"""Regressions for generator health reporting.

Every generator in `candidates.py` was wrapped in a bare `except: pass`, so a
throttled ingest produced an empty bucket with no error, no warning and no log
line. The fundamental half of the slate was dark from 2026-07-31 to 2026-09-03
(yfinance `YFRateLimitError`, estimates 0/80 rows, info 904/1516) and the slate
silently degraded to momentum-only.

A day's picks must be readable against what was actually generating that day.
"""
import json

import pandas as pd
import pytest

from advisor.research import candidates


@pytest.fixture
def env(tmp_path, monkeypatch):
    research = tmp_path / "research"
    research.mkdir(parents=True)
    monkeypatch.setattr(candidates, "RESEARCH_DIR", research)
    monkeypatch.setattr(candidates, "OUT", research / "candidates_latest.json")

    # fundamental stores absent -> empty frame, exactly as on a throttled night
    import advisor.research.factors_fundamental as ff
    monkeypatch.setattr(ff, "compute_scores", lambda: (
        pd.DataFrame(),
        {"input_quality": {
            "estimates": {"usable": False, "reason": None},
            "info": {"usable": False,
                     "reason": "broad snapshot below 80% coverage"}},
         "scope": {}}))
    return research


def _signals(research, longs=("AAA",), shorts=("ZZZ",)):
    def row(t, score, rank_pct):
        return {"ticker": t, "sector": "Industrials", "px": 100.0,
                "score": score, "rank_pct": rank_pct,
                "rank_basis": "composite percentile within 1440 liquid names",
                "raw": {"mom_12_1_pct": 50.0}}
    doc = {"longs": [row(t, 1.9, 0.99) for t in longs],
           "shorts": [row(t, -1.9, 0.99) for t in shorts],
           "n_liquid": 1440, "n_universe": 1502}
    (research / "signals_latest.json").write_text(json.dumps(doc))


def test_dark_generators_name_their_upstream_cause(env):
    _signals(env)
    res = candidates.build()
    dark = res["generators_dark"]
    for b in ("revision_leader", "pead_fresh", "cheap_quality", "squeeze_flag"):
        assert b in dark
        # the reason must be actionable, not "unavailable"
        assert "estimates" in dark[b] or "info" in dark[b] or "no ingest" in dark[b]


def test_live_generators_are_reported_live(env):
    _signals(env)
    res = candidates.build()
    assert "tactical_long" in res["generators_live"]
    assert "tactical_short" in res["generators_live"]
    assert res["generator_health"]["tactical_long"]["n"] == 1


def test_single_family_slate_raises_a_breadth_warning(env):
    """The exact condition that held for a month and nothing said so."""
    _signals(env)
    res = candidates.build()
    assert res["families_live"] == ["price_momentum"]
    assert res["breadth_warning"] is not None
    assert "SINGLE-FAMILY" in res["breadth_warning"]


def test_no_breadth_warning_when_two_families_are_live(env):
    _signals(env)
    (env / "positioning").mkdir()
    (env / "positioning" / "insider_clusters.json").write_text(json.dumps(
        {"clusters": [{"ticker": "AAA", "n_buys": 4, "net_value_usd": 2_500_000},
                      {"ticker": "BBB", "n_buys": 3, "net_value_usd": 900_000}]}))
    res = candidates.build()
    assert "insider_cluster" in res["generators_live"]
    assert set(res["families_live"]) == {"price_momentum", "positioning"}
    assert res["breadth_warning"] is None


def test_missing_signals_marks_the_price_generators_dark(env):
    res = candidates.build()          # no signals_latest.json at all
    for b in ("tactical_long", "tactical_short", "new_entrant"):
        assert not res["generator_health"][b]["live"]
        assert "signals_latest.json" in res["generators_dark"][b]


def test_slate_entries_carry_the_common_currency(env):
    _signals(env)
    e = candidates.build()["slate"][0]
    g = e["generators"]["tactical_long"]
    assert g["rank_pct"] == 0.99
    assert g["direction"] == "long"
    assert "1440 liquid names" in g["rank_basis"]
    assert e["selection"]["lead_bucket"] == "tactical_long"
    assert e["pickable"] is True


def test_short_side_keeps_its_own_direction(env):
    _signals(env)
    short = [e for e in candidates.build()["slate"] if e["ticker"] == "ZZZ"][0]
    assert short["generators"]["tactical_short"]["direction"] == "short"
    assert short["selection"]["direction"] == "short"


def test_rank_basis_says_when_a_percentile_is_taken_over_a_nominated_set(env):
    """A percentile over 2 clusters must not read like one over 1,440 names."""
    _signals(env)
    (env / "positioning").mkdir()
    (env / "positioning" / "insider_clusters.json").write_text(json.dumps(
        {"clusters": [{"ticker": "CCC", "n_buys": 4, "net_value_usd": 2_500_000},
                      {"ticker": "DDD", "n_buys": 3, "net_value_usd": 900_000}]}))
    slate = {e["ticker"]: e for e in candidates.build()["slate"]}
    basis = slate["CCC"]["generators"]["insider_cluster"]["rank_basis"]
    assert "2 detected clusters" in basis


def test_confluence_list_counts_families_not_buckets(env):
    """AAA appears in tactical_long AND new_entrant — one family, not two."""
    research = env
    def row(t, score, new):
        return {"ticker": t, "sector": "X", "px": 100.0, "score": score,
                "rank_pct": 0.99, "rank_basis": "b", "new_entrant": new,
                "raw": {"mom_12_1_pct": 1.0}}
    (research / "signals_latest.json").write_text(json.dumps(
        {"longs": [row("AAA", 1.9, True)], "shorts": [], "n_liquid": 1440}))
    res = candidates.build()
    aaa = res["slate"][0]
    assert set(aaa["buckets"]) == {"tactical_long", "new_entrant"}
    assert res["confluence"] == []      # one family -> no cross-family credit
