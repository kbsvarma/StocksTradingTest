"""Causality + mechanics regressions for the SHADOW-ONLY dynamic-K20 line."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

from shadow_report import (allocate_capacity, regime_entry_k,
                           wonham_bull)  # noqa: E402


def _bars(n=2000, seed=3):
    rng = np.random.default_rng(seed)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    ix = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"close": px, "roll": 0}, index=ix)


def test_wonham_is_forward_only():
    B = _bars()
    p_full = wonham_bull(B)
    # mutate ONLY the future half; every past posterior must be unchanged
    B2 = B.copy()
    B2.iloc[1000:, B2.columns.get_loc("close")] *= 1.5
    p_mut = wonham_bull(B2)
    pd.testing.assert_series_equal(p_full.iloc[:1000], p_mut.iloc[:1000])


def test_wonham_bounded():
    p = wonham_bull(_bars()).dropna()
    assert ((p >= 0) & (p <= 1)).all()


def test_entry_k_mapping():
    assert regime_entry_k(0.0) == 2.0          # bear -> production K2
    assert regime_entry_k(0.5) == 2.0          # neutral -> K2
    assert regime_entry_k(1.0) == 20.0         # full bull -> K20
    assert regime_entry_k(0.75) == 11.0        # linear midpoint
    assert regime_entry_k(float("nan")) is None


def test_allocate_capacity_largest_remainder():
    out = allocate_capacity([3, 3], 3)
    assert out.sum() == 3 and sorted(out.tolist()) == [1, 2]
    out = allocate_capacity([3, 1], 4)          # fits entirely
    assert out.tolist() == [3, 1]
    out = allocate_capacity([2, 2, 2], 0)
    assert out.sum() == 0
