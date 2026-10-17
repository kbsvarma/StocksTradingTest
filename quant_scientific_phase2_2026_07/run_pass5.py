"""Pass V: L1 geometry, robust states, spectra and common eigenmodes."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal.windows import dpss
from scipy.sparse import diags, eye
from scipy.sparse.linalg import factorized

from common import (
    HERE, SYMS, load_bars, production_signal, run_directions, update_ledger,
)
from run_stage_a import normalized_returns


def _soft_threshold(values: np.ndarray, threshold: float) -> np.ndarray:
    return np.sign(values) * np.maximum(np.abs(values) - threshold, 0.0)


def l1_gate(bars: pd.DataFrame, penalty: float,
            window: int = 192) -> pd.Series:
    """Evaluate the fixed causal trend-filter equation only at breakouts."""
    base = production_signal(bars).to_numpy(dtype=bool)
    returns = normalized_returns(bars).to_numpy(dtype=float)
    output = np.zeros(len(bars), dtype=np.int8)
    second_difference = diags(
        (np.ones(window - 2), -2.0 * np.ones(window - 2),
         np.ones(window - 2)),
        offsets=(0, 1, 2), shape=(window - 2, window), format="csc")
    solve = factorized((eye(window, format="csc")
                        + second_difference.T @ second_difference).tocsc())
    for i in np.flatnonzero(base):
        if i + 1 < window:
            continue
        sample = returns[i - window + 1:i + 1]
        if not np.isfinite(sample).all():
            continue
        path = np.cumsum(sample)
        path -= path[0]
        fitted = path.copy()
        auxiliary = second_difference @ fitted
        dual = np.zeros(window - 2)
        for _ in range(50):
            fitted = solve(path + second_difference.T @ (auxiliary - dual))
            curvature = second_difference @ fitted
            auxiliary = _soft_threshold(curvature + dual, penalty)
            dual += curvature - auxiliary
        output[i] = int(np.diff(fitted[-9:]).mean() > 0.0)
    return pd.Series(output, index=bars.index)


def student_hmm_probability(bars: pd.DataFrame,
                            duration: int) -> pd.Series:
    observations = normalized_returns(bars).to_numpy(dtype=float)
    q = 1.0 / duration
    transition = np.array([[1.0 - q, q], [q, 1.0 - q]])
    means = np.array([-0.10, 0.10])
    posterior = np.array([0.5, 0.5])
    output = np.full(len(observations), np.nan)
    for i, observation in enumerate(observations):
        prior = posterior @ transition
        if not np.isfinite(observation):
            posterior = prior
            continue
        likelihood = (1.0 + (observation - means) ** 2 / 5.0) ** -3.0
        posterior = prior * likelihood
        posterior /= posterior.sum()
        output[i] = posterior[1]
    return pd.Series(output, index=bars.index)


def multitaper_gate(bars: pd.DataFrame, window: int) -> pd.Series:
    base = production_signal(bars).to_numpy(dtype=bool)
    returns = normalized_returns(bars).to_numpy(dtype=float)
    output = np.zeros(len(bars), dtype=np.int8)
    tapers = dpss(window, NW=3.0, Kmax=5, sym=False)
    frequency = np.fft.rfftfreq(window)
    low = frequency <= 1.0 / 16.0
    for i in np.flatnonzero(base):
        if i + 1 < window:
            continue
        sample = returns[i - window + 1:i + 1]
        if not np.isfinite(sample).all():
            continue
        spectrum = np.mean(
            np.abs(np.fft.rfft(tapers * sample[None, :], axis=1)) ** 2,
            axis=0,
        )
        total = spectrum.sum()
        share = spectrum[low].sum() / total if total else 0.0
        output[i] = int(share >= .20)
    return pd.Series(output, index=bars.index)


def eigenmode_gates(
    bars: dict[str, pd.DataFrame], window: int,
) -> dict[str, pd.Series]:
    common = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common = common.intersection(bars[symbol].index)
    returns = pd.concat({
        symbol: normalized_returns(bars[symbol]).reindex(common)
        for symbol in SYMS
    }, axis=1)
    bases = {symbol: production_signal(bars[symbol]) for symbol in SYMS}
    common_base = pd.concat({
        symbol: bases[symbol].reindex(common).fillna(0).astype(bool)
        for symbol in SYMS
    }, axis=1).any(axis=1).to_numpy()
    accepted = pd.Series(False, index=common)
    values = returns.to_numpy(dtype=float)
    for i in np.flatnonzero(common_base):
        if i + 1 < window:
            continue
        sample = values[i - window + 1:i + 1]
        if not np.isfinite(sample).all():
            continue
        covariance = np.cov(sample, rowvar=False, ddof=1)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        vector = eigenvectors[:, -1]
        coherent = np.all(vector > 0) or np.all(vector < 0)
        share = eigenvalues[-1] / eigenvalues.sum()
        basket_positive = sample.mean(axis=1).sum() > 0
        accepted.iloc[i] = coherent and share > .70 and basket_positive
    return {
        symbol: (bases[symbol].astype(bool)
                 & accepted.reindex(bars[symbol].index).fillna(False)).astype(np.int8)
        for symbol in SYMS
    }


def run() -> pd.DataFrame:
    bars = load_bars()
    eigen120 = eigenmode_gates(bars, 120)
    eigen480 = eigenmode_gates(bars, 480)
    directions = {}
    for symbol in SYMS:
        print("signals", symbol, flush=True)
        base = production_signal(bars[symbol])
        hmm240 = student_hmm_probability(bars[symbol], 240)
        hmm720 = student_hmm_probability(bars[symbol], 720)
        directions[symbol] = {
            "production_fixed1": base,
            "l1_lambda5": l1_gate(bars[symbol], 5.0),
            "l1_lambda20": l1_gate(bars[symbol], 20.0),
            "student_hmm_d240": (base.astype(bool)
                                 & hmm240.gt(.50)).astype(np.int8),
            "student_hmm_d720": (base.astype(bool)
                                 & hmm720.gt(.50)).astype(np.int8),
            "multitaper_w256": multitaper_gate(bars[symbol], 256),
            "multitaper_w1024": multitaper_gate(bars[symbol], 1024),
            "eigenmode_w120": eigen120[symbol],
            "eigenmode_w480": eigen480[symbol],
        }
    results, daily, trades = run_directions(bars, directions)
    results.to_csv(HERE / "pass5_results.csv", index=False)
    daily.to_parquet(HERE / "pass5_daily_pnl.parquet")
    pd.concat(trades, names=["candidate", "row"]).to_parquet(
        HERE / "pass5_trades.parquet")
    update_ledger("V", "pass5_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
