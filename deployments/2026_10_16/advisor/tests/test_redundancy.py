"""Regressions for redundancy de-weighting (orthogonalization).

The composite summed correlated z-scores: mom_12_1, prox_52w and resid_mom are
three ways of measuring trend, so a six-factor composite could carry far less
independent information than its factor count implied — and nobody could say
how much, because the correlation was never reported.

  Green, Hand & Zhang (2017) — of ~94 characteristics only ~a dozen carry
  independent information once tested jointly.
"""
import numpy as np
import pandas as pd
import pytest

from advisor.research.factors import REDUNDANCY_FLOOR, redundancy_multipliers


def _z(n=400, seed=0, **cols):
    rng = np.random.default_rng(seed)
    base = rng.normal(size=n)
    out = {}
    for name, spec in cols.items():
        if spec == "independent":
            out[name] = rng.normal(size=n)
        elif spec == "duplicate":
            out[name] = base + rng.normal(size=n) * 0.01
        elif spec == "base":
            out[name] = base
        else:                                    # partial correlation
            out[name] = base * spec + rng.normal(size=n) * (1 - spec ** 2) ** 0.5
    return pd.DataFrame(out)


def test_independent_factors_keep_full_weight():
    z = _z(a="independent", b="independent", c="independent")
    m = redundancy_multipliers(z, ["a", "b", "c"])
    assert m["enabled"] is True
    for v in m["multipliers"].values():
        assert v > 0.95


def test_duplicated_factors_are_shrunk():
    z = _z(a="base", b="duplicate", c="independent")
    m = redundancy_multipliers(z, ["a", "b", "c"])["multipliers"]
    # near-perfect duplicates land ON the floor — it is a hard bound, and a
    # factor is never removed outright however redundant it is
    assert m["a"] == REDUNDANCY_FLOOR and m["b"] == REDUNDANCY_FLOOR
    assert m["c"] > 0.95                          # c is untouched


def test_shrinkage_is_monotone_in_correlation():
    weak = redundancy_multipliers(
        _z(a="base", b=0.3, seed=1), ["a", "b"])["multipliers"]["b"]
    strong = redundancy_multipliers(
        _z(a="base", b=0.9, seed=1), ["a", "b"])["multipliers"]["b"]
    assert strong < weak


def test_nothing_is_ever_switched_off():
    z = _z(a="base", b="duplicate", c="duplicate")
    for v in redundancy_multipliers(z, ["a", "b", "c"])["multipliers"].values():
        assert v >= REDUNDANCY_FLOOR


def test_nothing_is_ever_amplified():
    z = _z(a="independent", b="independent")
    for v in redundancy_multipliers(z, ["a", "b"])["multipliers"].values():
        assert v <= 1.0


def test_effective_factor_count_collapses_for_duplicates():
    """Three factors, two of which are the same, is worth about two.

    Exactly 1.8 for a perfect duplicate: eigenvalues [2, 1, 0] give
    (sum)^2 / sum(sq) = 9/5. That is the theoretical value, not a fudge.
    """
    z = _z(a="base", b="duplicate", c="independent")
    eff = redundancy_multipliers(z, ["a", "b", "c"])["effective_factor_count"]
    assert 1.75 <= eff <= 2.25


def test_effective_factor_count_matches_count_when_independent():
    z = _z(a="independent", b="independent", c="independent")
    eff = redundancy_multipliers(z, ["a", "b", "c"])["effective_factor_count"]
    assert eff > 2.8


def test_correlations_are_reported_for_audit():
    z = _z(a="base", b="duplicate")
    c = redundancy_multipliers(z, ["a", "b"])["correlations"]
    assert c["a"]["b"] > 0.95


# --- inert rather than fatal ----------------------------------------------

def test_single_active_factor_is_inert():
    z = _z(a="independent")
    assert redundancy_multipliers(z, ["a"])["enabled"] is False


def test_thin_cross_section_is_inert():
    z = _z(n=10, a="independent", b="independent")
    r = redundancy_multipliers(z, ["a", "b"])
    assert r["enabled"] is False and "rows" in r["reason"]


def test_missing_columns_are_ignored_not_fatal():
    z = _z(a="independent", b="independent")
    m = redundancy_multipliers(z, ["a", "b", "does_not_exist"])
    assert set(m["multipliers"]) == {"a", "b"}


def test_perfectly_collinear_factors_do_not_raise():
    """A singular correlation matrix must disable the layer, not crash the sheet."""
    n = 300
    base = np.random.default_rng(3).normal(size=n)
    z = pd.DataFrame({"a": base, "b": base})     # exactly identical
    r = redundancy_multipliers(z, ["a", "b"])
    assert r["enabled"] is False or all(
        v >= REDUNDANCY_FLOOR for v in r["multipliers"].values())
