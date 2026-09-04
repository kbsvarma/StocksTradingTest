"""Regressions for analyst-coverage conditioning.

  Hong, Lim & Stein (2000) (JF) — momentum is strongest in low-analyst-coverage
  stocks, consistent with slow information diffusion. Bernard & Thomas
  (1989, 1990) find the same concentration for post-earnings drift.

`numberOfAnalystOpinions` was in INFO_FIELDS and used for nothing. It is
present for ~1,498 of 1,515 names with a median of 12.
"""
import numpy as np
import pandas as pd
import pytest

from advisor.research.factors_fundamental import (COVERAGE_TILT_FLOOR,
                                                  coverage_tilt)


def _cov(values):
    return pd.Series(values, index=[f"T{i}" for i in range(len(values))],
                     dtype=float)


def _spread(n=200):
    return _cov(list(np.linspace(1, 60, n)))


def test_median_coverage_is_undamped():
    t = coverage_tilt(_spread())
    med = _spread().median()
    nearest = (_spread() - med).abs().idxmin()
    assert t[nearest] == pytest.approx(1.0, abs=0.02)


def test_heavily_covered_names_are_damped():
    """Damping is relative to the MEDIAN, so assert against it rather than an
    absolute level — a uniform fixture has median 30 where the real universe
    has median 12, and the absolute tilt differs accordingly (0.71 vs 0.45)."""
    cov = _spread()
    t = coverage_tilt(cov)
    assert t[cov.idxmax()] < 0.8
    assert t[cov.idxmax()] < t[cov.idxmin()]


def test_thinly_covered_names_are_not_amplified():
    """The house rule: nothing is ever pushed above 1.0."""
    cov = _spread()
    t = coverage_tilt(cov)
    assert t.max() <= 1.0
    assert t[cov.idxmin()] == pytest.approx(1.0)


def test_tilt_is_monotone_decreasing_in_coverage():
    t = coverage_tilt(_spread())
    assert (t.diff().dropna() <= 1e-12).all()


def test_nothing_is_muted_entirely():
    t = coverage_tilt(_cov([1] * 100 + [5000] * 100))
    assert t.min() >= COVERAGE_TILT_FLOOR


def test_zero_coverage_does_not_divide_by_zero():
    t = coverage_tilt(_cov([0] * 60 + list(range(1, 61))))
    assert np.isfinite(t).all()
    assert t.max() <= 1.0


def test_sparse_coverage_disables_the_tilt():
    """Below 50 observations there is no reliable median to tilt against."""
    assert coverage_tilt(_cov([5, 10, 20])) is None
    assert coverage_tilt(None) is None


def test_all_nan_coverage_disables_the_tilt():
    assert coverage_tilt(_cov([np.nan] * 200)) is None


def test_floor_is_configurable():
    t = coverage_tilt(_cov([1] * 100 + [5000] * 100), floor=0.8)
    assert t.min() == pytest.approx(0.8)
