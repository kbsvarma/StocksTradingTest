"""Pass I: causal latent-state and sequential-evidence models."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import gammaln
from scipy.stats import t as student_t

from common import (
    HERE, SYMS, eligible, gated_first_passage, load_bars,
    production_signal, run_directions, update_ledger,
)
from run_stage_a import normalized_returns


def kalman_score(bars: pd.DataFrame, half_life: int) -> pd.Series:
    """Posterior z-score for an AR(1) latent normalized drift."""
    y = normalized_returns(bars).to_numpy(dtype=float)
    phi = 2.0 ** (-1.0 / half_life)
    stationary_var = 0.10 ** 2
    process_var = stationary_var * (1.0 - phi * phi)
    mean, variance = 0.0, stationary_var
    out = np.full(len(y), np.nan)
    for i, observation in enumerate(y):
        predicted_mean = phi * mean
        predicted_var = phi * phi * variance + process_var
        if np.isfinite(observation):
            gain = predicted_var / (predicted_var + 1.0)
            mean = predicted_mean + gain * (observation - predicted_mean)
            variance = (1.0 - gain) * predicted_var
            out[i] = mean / math.sqrt(max(variance, 1e-16))
        else:
            mean, variance = predicted_mean, predicted_var
    return pd.Series(out, index=bars.index)


def _student_logpdf(x: float, mu: np.ndarray, kappa: np.ndarray,
                    alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
    df = 2.0 * alpha
    scale2 = beta * (kappa + 1.0) / (alpha * kappa)
    z2 = (x - mu) ** 2 / scale2
    return (gammaln((df + 1.0) / 2.0) - gammaln(df / 2.0)
            - 0.5 * np.log(df * np.pi * scale2)
            - 0.5 * (df + 1.0) * np.log1p(z2 / df))


def bocpd_positive_probability(bars: pd.DataFrame,
                               expected_run: int) -> pd.Series:
    """Student-t BOCPD with exact NIG conjugate run-length messages."""
    observations = normalized_returns(bars).to_numpy(dtype=float)
    hazard = 1.0 / expected_run
    max_run = 1500
    mu0, kappa0, alpha0, beta0 = 0.0, 1.0, 3.0, 2.0

    probs = np.array([1.0])
    run_lengths = np.array([0], dtype=np.int32)
    mu = np.array([mu0])
    kappa = np.array([kappa0])
    alpha = np.array([alpha0])
    beta = np.array([beta0])
    output = np.full(len(observations), np.nan)

    for i, x in enumerate(observations):
        if not np.isfinite(x):
            continue

        log_pred = _student_logpdf(x, mu, kappa, alpha, beta)
        prior_log_pred = float(_student_logpdf(
            x, np.array([mu0]), np.array([kappa0]),
            np.array([alpha0]), np.array([beta0]))[0])
        stable = max(float(log_pred.max()), prior_log_pred)
        growth = probs * (1.0 - hazard) * np.exp(log_pred - stable)
        changepoint = hazard * math.exp(prior_log_pred - stable)
        new_probs = np.concatenate(([changepoint], growth))
        new_run_lengths = np.concatenate((
            np.array([0], dtype=np.int32), run_lengths + 1))

        # r=0 starts from the prior and observes x; growth states update each
        # prior run length with x.  Truncation retains r=0..1499.
        prior_kappa = np.array([kappa0])
        prior_mu = np.array([mu0])
        prior_alpha = np.array([alpha0])
        prior_beta = np.array([beta0])
        old_mu = np.concatenate((prior_mu, mu))
        old_kappa = np.concatenate((prior_kappa, kappa))
        old_alpha = np.concatenate((prior_alpha, alpha))
        old_beta = np.concatenate((prior_beta, beta))
        within_limit = new_run_lengths < max_run
        new_run_lengths = new_run_lengths[within_limit]
        new_probs = new_probs[within_limit]
        old_mu, old_kappa = old_mu[within_limit], old_kappa[within_limit]
        old_alpha, old_beta = old_alpha[within_limit], old_beta[within_limit]

        next_kappa = old_kappa + 1.0
        next_mu = (old_kappa * old_mu + x) / next_kappa
        next_alpha = old_alpha + 0.5
        next_beta = (old_beta + old_kappa * (x - old_mu) ** 2
                     / (2.0 * next_kappa))

        # Discard only numerically irrelevant states, retaining their total
        # decision effect far below the protocol's 1e-8 tolerance.
        new_probs /= new_probs.sum()
        keep = new_probs >= 1e-12
        keep[0] = True
        probs = new_probs[keep]
        probs /= probs.sum()
        run_lengths = new_run_lengths[keep]
        mu, kappa = next_mu[keep], next_kappa[keep]
        alpha, beta = next_alpha[keep], next_beta[keep]

        mean_scale = np.sqrt(beta / (kappa * alpha))
        p_positive = student_t.cdf(mu / mean_scale, df=2.0 * alpha)
        output[i] = float(np.dot(probs, p_positive))

    return pd.Series(output, index=bars.index)


def cusum_direction(bars: pd.DataFrame, threshold: float,
                    allowed: pd.Series) -> pd.Series:
    """Positive likelihood-ratio alarm, rearmed by a negative alarm."""
    y = normalized_returns(bars).to_numpy(dtype=float)
    can_trade = allowed.to_numpy(dtype=bool)
    delta = 0.10
    g_pos = g_neg = 0.0
    armed = True
    out = np.zeros(len(y), dtype=np.int8)
    for i, observation in enumerate(y):
        if not np.isfinite(observation):
            continue
        g_pos = max(0.0, g_pos + delta * observation - delta * delta / 2.0)
        g_neg = max(0.0, g_neg - delta * observation - delta * delta / 2.0)
        if not armed and g_neg > threshold:
            armed = True
            g_pos = g_neg = 0.0
        elif armed and can_trade[i] and g_pos > threshold:
            out[i] = 1
            armed = False
            g_pos = g_neg = 0.0
    return pd.Series(out, index=bars.index)


def candidate_directions(bars: pd.DataFrame) -> dict[str, pd.Series]:
    allowed = eligible(bars)
    kalman120 = kalman_score(bars, 120)
    kalman480 = kalman_score(bars, 480)
    print(f"  BOCPD {bars.index.name or ''} hazard240", flush=True)
    bocpd240 = bocpd_positive_probability(bars, 240)
    print(f"  BOCPD {bars.index.name or ''} hazard720", flush=True)
    bocpd720 = bocpd_positive_probability(bars, 720)
    return {
        "production_fixed1": production_signal(bars),
        "kalman_L120": gated_first_passage(kalman120, allowed, .5, 0.0),
        "kalman_L480": gated_first_passage(kalman480, allowed, .5, 0.0),
        "bocpd_H240": gated_first_passage(bocpd240, allowed, .65, .50),
        "bocpd_H720": gated_first_passage(bocpd720, allowed, .65, .50),
        "cusum_h3": cusum_direction(bars, 3.0, allowed),
        "cusum_h5": cusum_direction(bars, 5.0, allowed),
    }


def run() -> pd.DataFrame:
    bars = load_bars()
    directions = {}
    for symbol in SYMS:
        print("signals", symbol, flush=True)
        directions[symbol] = candidate_directions(bars[symbol])
    results, daily, trades = run_directions(bars, directions)
    results.to_csv(HERE / "pass1_results.csv", index=False)
    daily.to_parquet(HERE / "pass1_daily_pnl.parquet")
    pd.concat(trades, names=["candidate", "row"]).to_parquet(
        HERE / "pass1_trades.parquet")
    update_ledger("I", "pass1_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
