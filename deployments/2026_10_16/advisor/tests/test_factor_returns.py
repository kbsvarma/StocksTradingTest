"""Regressions for volatility scaling and crash-state gating.

Guards the engine's measured failure: momentum kept its regime weight while
its realized IC ran -0.19 to -0.26, because `detect_regime` reads MARKET
regime (VIX, credit, 200dma) and those stayed benign through the factor
unwind. This layer reacts to the factor's OWN realized volatility, which is
measurable today rather than 21 days from now.
"""
import json

import pytest

from advisor.research import factor_returns as fr


@pytest.fixture
def series(tmp_path, monkeypatch):
    """Write a synthetic factor-return series and point the module at it."""
    def _write(per_factor):
        n = max(len(v) for v in per_factor.values())
        obs = [{"date": f"d{i}", "fwd_end": f"d{i+1}",
                "ret": {k: v[i] for k, v in per_factor.items() if i < len(v)}}
               for i in range(n)]
        p = tmp_path / "factor_returns.json"
        p.write_text(json.dumps({"schema_version": 1, "step_td": 1,
                                 "n_obs": len(obs), "series": obs}))
        monkeypatch.setattr(fr, "OUT", p)
        return p
    return _write


def _flat(v, n=240):
    return [v] * n


def _alternating(mag, n=240, drift=0.0):
    return [(mag if i % 2 == 0 else -mag) + drift for i in range(n)]


# --- the spread return itself ---------------------------------------------

def test_spread_return_is_long_top_minus_bottom():
    import pandas as pd
    z = pd.Series({f"T{i}": float(i) for i in range(200)})
    fwd = pd.Series({f"T{i}": (0.01 if i >= 100 else -0.01) for i in range(200)})
    assert fr._spread_return(z, fwd) == pytest.approx(0.02, abs=1e-9)


def test_spread_return_sign_flips_with_the_factor():
    import pandas as pd
    z = pd.Series({f"T{i}": float(-i) for i in range(200)})
    fwd = pd.Series({f"T{i}": (0.01 if i >= 100 else -0.01) for i in range(200)})
    assert fr._spread_return(z, fwd) == pytest.approx(-0.02, abs=1e-9)


def test_spread_return_refuses_a_thin_cross_section():
    import pandas as pd
    z = pd.Series({f"T{i}": float(i) for i in range(20)})
    fwd = pd.Series({f"T{i}": 0.01 for i in range(20)})
    assert fr._spread_return(z, fwd) is None


# --- the layer stays inert without evidence -------------------------------

def test_layer_is_inert_below_the_observation_floor(series):
    series({"mom_12_1": _alternating(0.01, n=10),
            "rev_1m": _alternating(0.01, n=10)})
    m = fr.vol_weight_multipliers()
    assert m["enabled"] is False
    assert "observations" in m["reason"]
    assert m["multipliers"] == {}


def test_missing_file_is_inert_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "OUT", tmp_path / "does_not_exist.json")
    m = fr.vol_weight_multipliers()
    assert m["enabled"] is False


# --- inverse-volatility scaling -------------------------------------------

def test_higher_volatility_gets_a_lower_multiplier(series):
    series({"calm": _alternating(0.002), "wild": _alternating(0.02),
            "mid": _alternating(0.01)})
    monkey_factors(("calm", "wild", "mid"))
    m = fr.vol_weight_multipliers()
    assert m["enabled"] is True
    assert m["multipliers"]["wild"] < m["multipliers"]["mid"] <= m["multipliers"]["calm"]


def test_no_factor_is_ever_amplified(series):
    series({"calm": _alternating(0.001), "wild": _alternating(0.05),
            "mid": _alternating(0.01)})
    monkey_factors(("calm", "wild", "mid"))
    for v in fr.vol_weight_multipliers()["multipliers"].values():
        assert v <= 1.0


def test_no_factor_is_ever_switched_off(series):
    series({"calm": _alternating(0.0005), "wild": _alternating(0.5),
            "mid": _alternating(0.01)})
    monkey_factors(("calm", "wild", "mid"))
    for v in fr.vol_weight_multipliers()["multipliers"].values():
        assert v >= fr.FLOOR


def test_median_anchor_stops_one_calm_factor_flooring_everything(series):
    """A min anchor put 5 of 6 real factors on the floor. Median must not."""
    series({"calm": _alternating(0.001),          # the outlier
            "a": _alternating(0.010), "b": _alternating(0.011),
            "c": _alternating(0.012), "d": _alternating(0.013)})
    monkey_factors(("calm", "a", "b", "c", "d"))
    mults = fr.vol_weight_multipliers()["multipliers"]
    typical = [mults[k] for k in ("a", "b", "c", "d")]
    assert all(v > fr.FLOOR + 0.3 for v in typical), mults


# --- Daniel-Moskowitz crash state -----------------------------------------

def test_crash_state_needs_both_drawdown_and_elevated_vol(series):
    # steadily positive, low vol -> not a crash
    series({"x": _flat(0.001), "y": _alternating(0.01)})
    monkey_factors(("x", "y"))
    assert fr.stats()["x"]["crash_state"] is False


def test_drawdown_alone_is_not_a_crash(series):
    """Vol must be MEANINGFULLY elevated; a bare `vol > full_vol` fires ~half
    the time by construction and discriminates nothing."""
    series({"x": _flat(-0.001), "y": _alternating(0.01)})
    monkey_factors(("x", "y"))
    st = fr.stats()["x"]
    assert st["trailing_return"] < 0
    assert st["crash_state"] is False          # zero vol, so not elevated


def test_crash_state_applies_the_extra_shrink(series):
    # recent window far more volatile than the full sample, and negative
    calm = [0.0005 if i % 2 == 0 else -0.0005 for i in range(200)]
    storm = [0.03 if i % 2 == 0 else -0.05 for i in range(60)]
    series({"crashing": calm + storm, "steady": _alternating(0.01)})
    monkey_factors(("crashing", "steady"))
    d = fr.vol_weight_multipliers()["detail"]["crashing"]
    assert d["crash_state"] is True
    assert "crash" in d["reason"]


def monkey_factors(names):
    """Point the module's FACTORS tuple at the synthetic names."""
    fr.FACTORS = tuple(names)


@pytest.fixture(autouse=True)
def _restore_factors():
    original = fr.FACTORS
    yield
    fr.FACTORS = original
