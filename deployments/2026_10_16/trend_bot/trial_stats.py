"""Dependency-free statistics used by the preregistered trial reports."""
from __future__ import annotations

import math

import numpy as np


def welch_t(left, right) -> float:
    """Two-sample unequal-variance t statistic (left minus right)."""
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if len(a) < 2 or len(b) < 2 or not (
            np.isfinite(a).all() and np.isfinite(b).all()):
        return float("nan")
    variance = a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)
    if variance <= 0 or not math.isfinite(float(variance)):
        return float("nan")
    return float((a.mean() - b.mean()) / math.sqrt(float(variance)))


def newey_west_indicator_t(outcomes, indicator, *, lags: int = 3) -> float:
    """HAC t statistic for an indicator coefficient with an intercept.

    Observations must be supplied in event-time order. The coefficient is the
    mean outcome for indicator=1 minus indicator=0. Bartlett weights and the
    requested fixed lag count implement the preregistered Newey-West read.
    """
    y = np.asarray(outcomes, dtype=float)
    d = np.asarray(indicator, dtype=float)
    if (len(y) != len(d) or len(y) < 3 or lags < 0 or
            not (np.isfinite(y).all() and np.isfinite(d).all()) or
            not set(np.unique(d)).issubset({0.0, 1.0}) or
            len(np.unique(d)) != 2):
        return float("nan")
    x = np.column_stack((np.ones(len(y)), d))
    xtx_inv = np.linalg.inv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    residual = y - x @ beta
    score = x * residual[:, None]
    meat = score.T @ score
    max_lag = min(int(lags), len(y) - 1)
    for lag in range(1, max_lag + 1):
        weight = 1.0 - lag / (max_lag + 1.0)
        cross = score[lag:].T @ score[:-lag]
        meat += weight * (cross + cross.T)
    covariance = xtx_inv @ meat @ xtx_inv
    variance = float(covariance[1, 1])
    if variance <= 0 or not math.isfinite(variance):
        return float("nan")
    return float(beta[1] / math.sqrt(variance))


def normal_approx_two_sided_p(t_stat: float) -> float:
    """Large-sample two-sided normal approximation, clearly labeled by UI."""
    if not math.isfinite(float(t_stat)):
        return float("nan")
    return math.erfc(abs(float(t_stat)) / math.sqrt(2.0))
