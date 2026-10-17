"""Causality + mechanics regressions for the SHADOW-ONLY ridge filter."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

from shadow_report import (RIDGE_FEATURES, ridge_fit, ridge_predict,
                           ridge_train_slice)  # noqa: E402


def _teachers(n=800, signal=1.0, seed=7):
    rng = np.random.default_rng(seed)
    rows = {f: rng.normal(size=n) for f in RIDGE_FEATURES}
    frame = pd.DataFrame(rows)
    frame["sym"] = np.where(np.arange(n) % 3 == 0, "NQ",
                            np.where(np.arange(n) % 3 == 1, "ES", "YM"))
    frame["y"] = signal * frame["overshoot"] + rng.normal(scale=0.01, size=n)
    frame["exit_time"] = pd.date_range("2015-01-01", periods=n, freq="6h",
                                       tz="UTC")
    return frame


def test_fit_recovers_planted_sign():
    frame = _teachers()
    model = ridge_fit(frame)
    pred = ridge_predict(model, frame)
    hi = frame["overshoot"] > 1.0
    lo = frame["overshoot"] < -1.0
    assert pred[hi.to_numpy()].mean() > 0 > pred[lo.to_numpy()].mean()


def test_train_slice_excludes_trades_exiting_in_prediction_month():
    frame = _teachers(n=10)
    month = pd.Timestamp("2015-01-02", tz="UTC")
    train = ridge_train_slice(frame, month)
    assert (train["exit_time"] < month).all()
    # a trade exiting exactly AT the month boundary must be excluded
    frame.loc[0, "exit_time"] = month
    assert 0 not in ridge_train_slice(frame, month).index


def test_embargo_pushes_cutoff_back():
    frame = _teachers(n=100)
    month = pd.Timestamp("2015-01-20", tz="UTC")
    plain = ridge_train_slice(frame, month)
    embargoed = ridge_train_slice(frame, month, embargo_days=31)
    assert len(embargoed) < len(plain)
    assert (embargoed["exit_time"]
            < month - pd.Timedelta(days=31)).all()


def test_prediction_uses_train_standardization():
    frame = _teachers()
    model = ridge_fit(frame)
    one = frame.iloc[[0]]
    a = ridge_predict(model, one)
    # shifting the PREDICTION frame's distribution must not change the
    # single row's prediction (means/scales are frozen from training)
    b = ridge_predict(model, pd.concat([one] * 50, ignore_index=True))
    assert np.isclose(a[0], b[0])
