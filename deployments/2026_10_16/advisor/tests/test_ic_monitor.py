"""Regressions for the live IC monitor.

This module had ZERO tests despite multiplying live factor weights through
`ic_weight_multipliers`. What it must get right:

  * PIT discipline — a snapshot may only be scored once its forward window
    has actually elapsed. Scoring early is look-ahead.
  * The overlap correction. Snapshots are DAILY but each scores a 21-day
    forward window, so consecutive rows share 20 of 21 days and the naive
    t-stat is inflated by roughly sqrt(21). The independent subset is the
    only figure fit for inference, and both must be reported.
  * Idempotence — re-running must not double-count a matured snapshot.
"""
import json

import numpy as np
import pandas as pd
import pytest

from advisor.research import ic_monitor as icm


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Synthetic factor snapshots plus a price panel with known forward returns."""
    hist = tmp_path / "factor_history"
    hist.mkdir()
    monkeypatch.setattr(icm, "HIST_DIR", hist)
    monkeypatch.setattr(icm, "OUT", tmp_path / "ic_live.json")

    tickers = [f"T{i}" for i in range(200)]
    dates = pd.bdate_range("2026-01-01", periods=120)

    def _setup(snapshots, fwd_map):
        """snapshots: {date_str: {ticker: z}}; fwd_map: {ticker: 21d return}."""
        close = pd.DataFrame(100.0, index=dates, columns=tickers)
        # make the return from row i to row i+21 equal fwd_map, for every i
        for t, r in fwd_map.items():
            close[t] = [100.0 * (1 + r) ** (j / icm.FWD) for j in range(len(dates))]
        monkeypatch.setattr("advisor.research.datastore.load_panel",
                            lambda field: close)
        for day, z in snapshots.items():
            pd.DataFrame({"factor_a": pd.Series(z),
                          "composite": pd.Series(z),
                          "px": 100.0, "sector": "X", "regime": "risk_on",
                          "shock": False}).to_parquet(hist / f"{day}.parquet")
        return close
    return _setup


def _perfect(tickers):
    """z ranks exactly with forward return -> IC = +1."""
    z = {t: float(i) for i, t in enumerate(tickers)}
    fwd = {t: i * 0.001 for i, t in enumerate(tickers)}
    return z, fwd


# --- point-in-time discipline ---------------------------------------------

def test_snapshot_is_not_scored_before_its_window_elapses(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    # 2026-06-10 is inside the last FWD rows of the panel -> not matured
    env({"2026-06-10": z}, fwd)
    res = icm.mature()
    assert res["n_matured"] == 0


def test_matured_snapshot_is_scored(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    env({"2026-01-05": z}, fwd)
    res = icm.mature()
    assert res["n_matured"] == 1
    assert res["series"][0]["ic"]["factor_a"] == pytest.approx(1.0, abs=1e-6)


def test_ic_sign_follows_the_factor(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    inverted = {t: -v for t, v in z.items()}
    env({"2026-01-05": inverted}, fwd)
    assert icm.mature()["series"][0]["ic"]["factor_a"] == pytest.approx(-1.0,
                                                                       abs=1e-6)


def test_rerun_does_not_double_count(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    env({"2026-01-05": z, "2026-01-06": z}, fwd)
    first = icm.mature()
    second = icm.mature()
    assert first["n_new_this_run"] == 2
    assert second["n_new_this_run"] == 0
    assert second["n_matured"] == first["n_matured"]


# --- the overlap correction -----------------------------------------------

def test_reports_both_overlapping_and_independent_statistics(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    env({f"2026-01-{d:02d}": z for d in range(5, 29)}, fwd)
    s = icm.mature()["factors"]["factor_a"]
    for key in ("t_stat_overlapping", "t_stat_independent",
                "n_obs", "n_independent", "mean_ic_independent"):
        assert key in s


def test_independent_subset_is_thinned_by_the_forward_window(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    env({f"2026-01-{d:02d}": z for d in range(5, 29)}, fwd)
    s = icm.mature()["factors"]["factor_a"]
    assert s["n_independent"] == pytest.approx(
        int(np.ceil(s["n_obs"] / icm.FWD)), abs=1)
    assert s["n_independent"] < s["n_obs"]


def test_inference_note_warns_the_overlapping_t_stat_is_invalid(env):
    tickers = [f"T{i}" for i in range(200)]
    z, fwd = _perfect(tickers)
    env({f"2026-01-{d:02d}": z for d in range(5, 29)}, fwd)
    note = icm.mature()["factors"]["factor_a"]["inference_note"]
    assert "NOT valid inference" in note
    assert "t_stat_independent" in note


# --- robustness ------------------------------------------------------------

def test_thin_cross_section_is_skipped_not_scored(env):
    """Fewer than 50 usable names cannot support a rank correlation."""
    tickers = [f"T{i}" for i in range(200)]
    _z, fwd = _perfect(tickers)
    env({"2026-01-05": {t: float(i) for i, t in enumerate(tickers[:20])}}, fwd)
    res = icm.mature()
    assert res["series"][0]["ic"] == {}


def test_no_snapshots_yields_an_empty_but_valid_result(env):
    env({}, {})
    res = icm.mature()
    assert res["n_matured"] == 0 and res["factors"] == {}
    assert json.loads(icm.OUT.read_text())["fwd_days"] == icm.FWD


# --- the weight multipliers this feeds -------------------------------------

@pytest.fixture
def live(tmp_path, monkeypatch):
    """Drive ic_weight_multipliers from a controlled ic_live.json.

    It reads REPO_ROOT/advisor/data/research/ic_live.json, so the repo root is
    what has to move — otherwise the test silently reads production and can
    pass without exercising anything.
    """
    from advisor.research import factors
    d = tmp_path / "advisor" / "data" / "research"
    d.mkdir(parents=True)
    monkeypatch.setattr(factors, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ADVISOR_IC_FEEDBACK", raising=False)

    def _write(factors_block):
        (d / "ic_live.json").write_text(json.dumps(
            {"as_of": "2026-09-04T00:00:00-04:00", "factors": factors_block}))
    return _write


def test_positive_ic_is_never_amplified(live):
    from advisor.research import factors
    live({"good": {"mean_ic_independent": 0.25, "n_independent": 50}})
    assert factors.ic_weight_multipliers()["multipliers"]["good"] == 1.0


def test_negative_ic_is_shrunk_but_never_switched_off(live):
    from advisor.research import factors
    live({"broken": {"mean_ic_independent": -0.99, "n_independent": 5000}})
    m = factors.ic_weight_multipliers()["multipliers"]["broken"]
    # confidence is n/(n+K), so the multiplier APPROACHES the floor
    # asymptotically and never quite reaches it — the floor is a hard bound,
    # not an attractor.
    assert factors.IC_FEEDBACK_FLOOR <= m < factors.IC_FEEDBACK_FLOOR + 0.01


def test_shrinkage_scales_with_independent_window_count(live):
    """The same bad IC on 2 windows must bite far less than on 200."""
    from advisor.research import factors
    live({"thin": {"mean_ic_independent": -0.20, "n_independent": 2}})
    thin = factors.ic_weight_multipliers()["multipliers"]["thin"]
    live({"thick": {"mean_ic_independent": -0.20, "n_independent": 200}})
    thick = factors.ic_weight_multipliers()["multipliers"]["thick"]
    assert thin > thick
    assert thin > 0.8          # 2 windows is almost no evidence


def test_factor_without_independent_windows_is_ignored(live):
    from advisor.research import factors
    live({"nada": {"mean_ic_independent": -0.5, "n_independent": 0}})
    assert "nada" not in factors.ic_weight_multipliers()["multipliers"]


def test_feedback_can_be_disabled_by_environment(live, monkeypatch):
    from advisor.research import factors
    live({"x": {"mean_ic_independent": -0.5, "n_independent": 50}})
    monkeypatch.setenv("ADVISOR_IC_FEEDBACK", "0")
    res = factors.ic_weight_multipliers()
    assert res["enabled"] is False and res["multipliers"] == {}


def test_missing_ic_live_is_inert_not_fatal(tmp_path, monkeypatch):
    from advisor.research import factors
    monkeypatch.setattr(factors, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ADVISOR_IC_FEEDBACK", raising=False)
    res = factors.ic_weight_multipliers()
    assert res["enabled"] is False and res["multipliers"] == {}
