"""Pass II: causal geometry, frequency and cross-system information."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (
    HERE, SYMS, eligible, gated_first_passage, load_bars, production_signal,
    run_directions, update_ledger,
)
from run_stage_a import normalized_returns, roll_clean_log_returns, rolling_t_score


def _rolling_dot(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    result = np.full(len(values), np.nan)
    if len(values) >= len(weights):
        result[len(weights) - 1:] = np.convolve(
            values, weights[::-1], mode="valid")
    return result


def polynomial_t_score(bars: pd.DataFrame, horizon: int) -> pd.Series:
    """Triangular WLS t-statistic of one-sided log-price slope."""
    y = np.log(bars["close"].to_numpy(dtype=float))
    x = np.arange(horizon, dtype=float)
    weights = np.arange(1, horizon + 1, dtype=float)
    weights /= weights.mean()
    sum_w = weights.sum()
    x_bar = float(np.dot(weights, x) / sum_w)
    centered = x - x_bar
    sxx = float(np.dot(weights, centered * centered))
    sum_y = _rolling_dot(y, weights)
    sum_xy = _rolling_dot(y, weights * x)
    sum_y2 = _rolling_dot(y * y, weights)
    slope = (sum_xy - x_bar * sum_y) / sxx
    intercept = (sum_y - slope * np.dot(weights, x)) / sum_w
    sse = np.maximum(sum_y2 - intercept * sum_y - slope * sum_xy, 0.0)
    standard_error = np.sqrt((sse / (horizon - 2)) / sxx)
    score = np.divide(slope, standard_error,
                      out=np.full(len(y), np.nan), where=standard_error > 0)
    return pd.Series(score, index=bars.index)


def _haar_coefficient(log_price: np.ndarray, horizon: int,
                      prior_vol: np.ndarray) -> np.ndarray:
    half = horizon // 2
    weights = np.concatenate((
        np.full(half, -1.0 / half),
        np.full(half, 1.0 / half),
    ))
    raw = _rolling_dot(log_price, weights)
    # Under an iid-return null, the difference between consecutive block
    # means has these exact return impulse coefficients.
    impulse = np.concatenate((
        np.arange(half, dtype=float) / half,
        np.arange(half, 0, -1, dtype=float) / half,
    ))
    null_scale = np.sqrt(np.dot(impulse, impulse))
    return np.divide(raw, prior_vol * null_scale,
                     out=np.full(len(raw), np.nan),
                     where=np.isfinite(prior_vol) & (prior_vol > 0))


def haar_coherence_score(bars: pd.DataFrame) -> pd.Series:
    returns = roll_clean_log_returns(bars)
    vol = returns.ewm(halflife=48, adjust=False,
                      min_periods=240).std().shift(1).to_numpy(dtype=float)
    log_price = np.log(bars["close"].to_numpy(dtype=float))
    coefficients = [_haar_coefficient(log_price, h, vol)
                    for h in (32, 128, 512)]
    return pd.Series(np.mean(coefficients, axis=0), index=bars.index)


def variance_ratios(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    returns = roll_clean_log_returns(bars)
    base_var = returns.rolling(512, min_periods=512).var(ddof=1)
    ratios = []
    for aggregation in (8, 32):
        aggregated = returns.rolling(
            aggregation, min_periods=aggregation).sum()
        agg_var = aggregated.rolling(512, min_periods=512).var(ddof=1)
        ratios.append(agg_var.div(aggregation * base_var.replace(0, np.nan)))
    return ratios[0], ratios[1]


def semivariance_scores(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    returns = roll_clean_log_returns(bars)
    squares = returns * returns
    downside = squares.where(returns < 0, 0.0).rolling(120).sum()
    upside = squares.where(returns > 0, 0.0).rolling(120).sum()
    imbalance = (downside - upside).div(
        (downside + upside).replace(0.0, np.nan))
    t120 = rolling_t_score(returns, 120)
    return t120 - imbalance, t120 - 0.5 * imbalance


def monthly_var_forecasts(
    bars: dict[str, pd.DataFrame], penalties=(10.0, 100.0),
) -> dict[float, dict[str, pd.Series]]:
    """Four-lag expanding ridge VAR, refit causally at each month start."""
    common_index = bars[SYMS[0]].index
    for symbol in SYMS[1:]:
        common_index = common_index.intersection(bars[symbol].index)
    returns = pd.concat({
        symbol: normalized_returns(bars[symbol]).reindex(common_index)
        for symbol in SYMS
    }, axis=1)
    features = pd.concat([
        returns.shift(lag).add_prefix(f"l{lag}_") for lag in range(4)
    ], axis=1)
    targets = returns.shift(-1)
    valid = features.notna().all(axis=1) & targets.notna().all(axis=1)
    values_x = features.to_numpy(dtype=float)
    values_y = targets.to_numpy(dtype=float)
    next_time = pd.Series(common_index, index=common_index).shift(-1)
    outputs = {
        penalty: pd.DataFrame(np.nan, index=common_index, columns=SYMS)
        for penalty in penalties
    }

    month_starts = common_index.to_period("M").unique()
    for period in month_starts:
        start = pd.Timestamp(period.start_time, tz=common_index.tz)
        in_month = common_index.to_period("M") == period
        train = (valid.to_numpy() & (common_index < start)
                 & next_time.lt(start).to_numpy())
        train_rows = np.flatnonzero(train)
        if len(train_rows) < 2000:
            continue
        train_rows = train_rows[-20000:]
        predict_rows = np.flatnonzero(in_month & features.notna().all(axis=1))
        if not len(predict_rows):
            continue
        x_train, y_train = values_x[train_rows], values_y[train_rows]
        x_mean = x_train.mean(axis=0)
        x_sd = x_train.std(axis=0, ddof=1)
        x_sd[x_sd <= 0] = 1.0
        xz = (x_train - x_mean) / x_sd
        y_mean = y_train.mean(axis=0)
        centered_y = y_train - y_mean
        gram = xz.T @ xz
        rhs = xz.T @ centered_y
        x_predict = (values_x[predict_rows] - x_mean) / x_sd
        for penalty in penalties:
            beta = np.linalg.solve(
                gram + penalty * np.eye(gram.shape[0]), rhs)
            outputs[penalty].iloc[predict_rows] = x_predict @ beta + y_mean

    return {
        penalty: {
            symbol: frame[symbol].reindex(bars[symbol].index)
            for symbol in SYMS
        }
        for penalty, frame in outputs.items()
    }


def local_directions(bars: pd.DataFrame,
                     forecast10: pd.Series,
                     forecast100: pd.Series) -> dict[str, pd.Series]:
    allowed = eligible(bars)
    base = production_signal(bars)
    poly48 = polynomial_t_score(bars, 48)
    poly192 = polynomial_t_score(bars, 192)
    haar = haar_coherence_score(bars)
    vr8, vr32 = variance_ratios(bars)
    semivar1, semivar05 = semivariance_scores(bars)
    return {
        "production_fixed1": base,
        "poly_h48": gated_first_passage(poly48, allowed, 2.0, 0.0),
        "poly_h192": gated_first_passage(poly192, allowed, 2.0, 0.0),
        "haar_32_128_512": gated_first_passage(haar, allowed, .5, 0.0),
        "vr_geomean": (base.astype(bool)
                       & np.sqrt((vr8 * vr32).clip(lower=0)).gt(1.0)).astype(np.int8),
        "vr_both": (base.astype(bool) & vr8.gt(1.0)
                    & vr32.gt(1.0)).astype(np.int8),
        "semivar_c1": (base.astype(bool) & semivar1.gt(0)).astype(np.int8),
        "semivar_c05": (base.astype(bool) & semivar05.gt(0)).astype(np.int8),
        "var_ridge10": (base.astype(bool)
                        & forecast10.gt(0)).astype(np.int8),
        "var_ridge100": (base.astype(bool)
                         & forecast100.gt(0)).astype(np.int8),
    }


def run() -> pd.DataFrame:
    bars = load_bars()
    print("monthly causal VAR fits", flush=True)
    forecasts = monthly_var_forecasts(bars)
    directions = {}
    for symbol in SYMS:
        print("signals", symbol, flush=True)
        directions[symbol] = local_directions(
            bars[symbol], forecasts[10.0][symbol], forecasts[100.0][symbol])
    results, daily, trades = run_directions(bars, directions)
    results.to_csv(HERE / "pass2_results.csv", index=False)
    daily.to_parquet(HERE / "pass2_daily_pnl.parquet")
    pd.concat(trades, names=["candidate", "row"]).to_parquet(
        HERE / "pass2_trades.parquet")
    update_ledger("II", "pass2_results.csv")
    print(results.to_string(index=False), flush=True)
    return results


if __name__ == "__main__":
    run()
