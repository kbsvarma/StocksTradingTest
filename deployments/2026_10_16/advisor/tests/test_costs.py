"""Regressions for the transaction-cost model.

The headline decision this guards: BOTH published daily-bar spread estimators
(Corwin-Schultz 2012, Abdi-Ranaldo 2017) fail a sanity check on modern
large-cap tape — CS put SPY at 22.9bps and AAPL at 65.3bps against a reality
under 2bps. They are kept as labelled diagnostics; the shipped spread is a
liquidity-anchored tier.
"""
import pandas as pd
import pytest

from advisor.research import costs


@pytest.fixture
def panels(monkeypatch):
    """Synthetic panels: one mega-cap, one mid, one micro."""
    idx = pd.bdate_range("2026-01-01", periods=90)
    close = pd.DataFrame({"BIG": 100.0, "MID": 50.0, "TINY": 5.0}, index=idx)
    # give each a little price motion so sigma is non-zero and ordered
    for i, (t, amp) in enumerate((("BIG", .004), ("MID", .012), ("TINY", .05))):
        close[t] = close[t] * (1 + pd.Series(
            [amp if j % 2 else -amp for j in range(len(idx))], index=idx)).cumprod()
    high, low = close * 1.01, close * 0.99
    volume = pd.DataFrame({"BIG": 5e7, "MID": 1e6, "TINY": 2e4}, index=idx)

    def fake(field):
        return {"close": close, "high": high, "low": low, "volume": volume}[field]
    import advisor.research.datastore as ds
    monkeypatch.setattr(ds, "load_panel", fake)


def test_spread_falls_with_liquidity(panels):
    e = costs.estimate()
    assert e["BIG"]["spread_bps"] < e["MID"]["spread_bps"] < e["TINY"]["spread_bps"]


def test_megacap_spread_is_plausible_not_tens_of_bps(panels):
    """The specific failure of the bar estimators: CS put SPY at 22.9bps."""
    assert costs.estimate()["BIG"]["spread_bps"] <= 5.0


def test_round_trip_includes_spread_and_two_way_impact(panels):
    c = costs.estimate()["MID"]
    assert c["round_trip_bps"] == pytest.approx(
        c["spread_bps"] + 2 * c["impact_bps"], abs=0.15)


def test_diagnostics_are_reported_not_used(panels):
    c = costs.estimate()["BIG"]
    assert "diagnostic_corwin_schultz_bps" in c
    assert "diagnostic_abdi_ranaldo_bps" in c
    assert "MODELLED" in c["method"]


def test_impact_scales_with_participation(panels):
    small = costs.estimate(participation=0.001)["MID"]["impact_bps"]
    large = costs.estimate(participation=0.10)["MID"]["impact_bps"]
    assert large > small
    # square-root law: 100x participation -> ~10x impact
    assert large == pytest.approx(small * 10, rel=0.05)


def test_spread_is_floored_and_capped(panels):
    for c in costs.estimate().values():
        assert costs.MIN_SPREAD_BPS <= c["spread_bps"] <= costs.MAX_SPREAD_BPS


# --- the screen -----------------------------------------------------------

def test_screen_passes_a_wide_target():
    v = costs.screen("X", 100.0, 130.0, {"round_trip_bps": 50.0})
    assert v["ok"] is True and v["ratio"] == pytest.approx(60.0)


def test_screen_rejects_a_target_inside_the_cost():
    v = costs.screen("X", 100.0, 100.4, {"round_trip_bps": 60.0})
    assert v["ok"] is False
    assert "round trip" in v["reason"]


def test_screen_is_direction_agnostic():
    long_v = costs.screen("X", 100.0, 130.0, {"round_trip_bps": 50.0})
    short_v = costs.screen("X", 100.0, 70.0, {"round_trip_bps": 50.0})
    assert long_v["ratio"] == short_v["ratio"]


def test_screen_fails_open_without_a_cost_estimate():
    """Absence of a cost figure must not silently kill every pick."""
    v = costs.screen("X", 100.0, 130.0, None)
    assert v["ok"] is True and v["ratio"] is None
