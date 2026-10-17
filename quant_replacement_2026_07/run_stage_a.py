"""Stage-A equation screen: isolate entry-signal quality at fixed 1-1-1 size."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07"),
                str(ROOT / "adaptive_rotation_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from run_adaptive_rotation import PRODUCTION, START, SYMS, maximum_exposure  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402


HOURS_PER_YEAR = 24 * 252


def roll_clean_log_returns(bars: pd.DataFrame) -> pd.Series:
    returns = np.log(bars["close"]).diff()
    # The current completed bar's roll flag is known.  The NEXT bar's flag is
    # not: masking the pre-roll return with shift(-1) would leak one bar.
    return returns.mask(bars["roll"].astype(bool), 0.0)


def normalized_returns(bars: pd.DataFrame) -> pd.Series:
    returns = roll_clean_log_returns(bars)
    # Today's return is normalized only by volatility known before the bar.
    vol = returns.ewm(halflife=48, adjust=False,
                      min_periods=240).std().shift(1)
    return returns.div(vol.replace(0.0, np.nan))


def normalized_ema_score(bars: pd.DataFrame) -> pd.Series:
    y = normalized_returns(bars)
    components = []
    for halflife in (24, 96, 384):
        alpha = 1.0 - np.exp(np.log(0.5) / halflife)
        raw = y.ewm(alpha=alpha, adjust=False,
                    min_periods=4 * halflife).mean()
        iid_sd = np.sqrt(alpha / (2.0 - alpha))
        components.append(raw / iid_sd)
    return pd.concat(components, axis=1).mean(axis=1, skipna=False)


def rolling_t_score(returns: pd.Series, horizon: int) -> pd.Series:
    mean = returns.rolling(horizon, min_periods=horizon).mean()
    sd = returns.rolling(horizon, min_periods=horizon).std(ddof=1)
    return mean.div(sd.replace(0.0, np.nan)) * np.sqrt(horizon)


def bayesian_drift_score(bars: pd.DataFrame) -> pd.Series:
    returns = roll_clean_log_returns(bars)
    z = [rolling_t_score(returns, horizon)
         for horizon in (24, 120, 480)]
    # Stouffer combination of three standardized pieces of evidence.
    return pd.concat(z, axis=1).mean(axis=1, skipna=False) * np.sqrt(3.0)


def efficiency_ratio(returns: pd.Series, horizon: int) -> pd.Series:
    displacement = returns.rolling(horizon, min_periods=horizon).sum().abs()
    path = returns.abs().rolling(horizon, min_periods=horizon).sum()
    return displacement.div(path.replace(0.0, np.nan)).clip(0.0, 1.0)


def path_efficient_score(bars: pd.DataFrame) -> pd.Series:
    returns = roll_clean_log_returns(bars)
    return bayesian_drift_score(bars) * np.sqrt(efficiency_ratio(returns, 120))


def hidden_state_score(bars: pd.DataFrame, mean: float,
                       expected_length: int) -> pd.Series:
    """Causal three-state Gaussian filter; output bull minus bear posterior."""
    y = normalized_returns(bars)
    q = 1.0 / expected_length
    transition = np.full((3, 3), q / 2.0)
    np.fill_diagonal(transition, 1.0 - q)
    state_means = np.array([-mean, 0.0, mean])
    posterior = np.array([0.25, 0.50, 0.25])
    values = np.full(len(y), np.nan)
    for i, observation in enumerate(y.to_numpy(dtype=float)):
        if not np.isfinite(observation):
            continue
        prior = posterior @ transition
        log_likelihood = -0.5 * (observation - state_means) ** 2
        likelihood = np.exp(log_likelihood - log_likelihood.max())
        posterior = prior * likelihood
        posterior /= posterior.sum()
        values[i] = posterior[2] - posterior[0]
    return pd.Series(values, index=bars.index)


def breakout_votes(bars: pd.DataFrame) -> pd.Series:
    votes = [bars["close"].gt(
        bars["close"].rolling(horizon).max().shift(1)).astype(int)
        for horizon in (10, 20, 40, 80)]
    return pd.concat(votes, axis=1).sum(axis=1)


def first_passage(score: pd.Series, boundary: float,
                  reset: float = 0.0) -> pd.Series:
    """Fire once above boundary, then require a reset before re-arming."""
    out = pd.Series(0, index=score.index, dtype=np.int8)
    armed = True
    for i, value in enumerate(score.to_numpy(dtype=float)):
        if not np.isfinite(value):
            continue
        if not armed and value < reset:
            armed = True
        elif armed and value > boundary:
            out.iloc[i] = 1
            armed = False
    return out


def candidate_directions(bars: pd.DataFrame,
                         config: dict = PRODUCTION) -> dict[str, pd.Series]:
    gates = strategy.signal_frame(bars, config)
    allowed = gates["vol_ok"] & gates["session_ok"] & gates["atr"].notna()
    nema = normalized_ema_score(bars)
    bmd = bayesian_drift_score(bars)
    peb = path_efficient_score(bars)
    hmm120 = hidden_state_score(bars, 0.10, 120)
    hmm480 = hidden_state_score(bars, 0.10, 480)
    votes = breakout_votes(bars)

    def long(score: pd.Series, threshold: float) -> pd.Series:
        return (score.gt(threshold) & allowed).astype(np.int8)

    def asymmetric(score: pd.Series, long_threshold: float,
                   short_threshold: float = -1.5) -> pd.Series:
        out = pd.Series(0, index=bars.index, dtype=np.int8)
        out.loc[allowed & score.gt(long_threshold)] = 1
        out.loc[allowed & score.lt(short_threshold)] = -1
        return out

    return {
        "donchian20": gates["signal"].astype(np.int8),
        "nema_t0": long(nema, 0.0),
        "nema_t05": long(nema, 0.5),
        "bmd_p75": long(bmd, 0.674),
        "bmd_p90": long(bmd, 1.282),
        "peb_t05": long(peb, 0.5),
        "peb_t10": long(peb, 1.0),
        "hmm_l120": long(hmm120, 0.20),
        "hmm_l480": long(hmm480, 0.20),
        "mbv_2of4": long(votes, 1.5),
        "mbv_3of4": long(votes, 2.5),
        "nema_asym": asymmetric(nema, 0.5),
        "bmd_asym": asymmetric(bmd, 0.674),
        "nema_cross": first_passage(nema, 0.5).where(allowed, 0),
        "bmd_cross": first_passage(bmd, 0.674).where(allowed, 0),
        "peb_cross": first_passage(peb, 0.5).where(allowed, 0),
        "hmm_cross": first_passage(hmm120, 0.20).where(allowed, 0),
    }


def simulate_direction(bars: pd.DataFrame, symbol: str,
                       direction: pd.Series, config: dict = PRODUCTION,
                       quantity=1) -> pd.DataFrame:
    """Production-parity next-open and K2 stop FSM for an arbitrary signal."""
    frame = strategy.signal_frame(bars, config)
    atr = frame["atr"].to_numpy(dtype=float)
    signal = direction.reindex(bars.index).fillna(0).to_numpy(dtype=np.int8)
    opens, highs, lows, closes = (
        bars[column].to_numpy(dtype=float)
        for column in ("open", "high", "low", "close"))
    rolls = bars["roll"].to_numpy(dtype=int)
    times = bars.index

    rows = []
    position = pending = pending_qty = trade_qty = 0
    entry = stop = extreme = np.nan
    entry_time = None
    for i in range(len(bars)):
        if pending and rolls[i]:
            pending = pending_qty = 0
        if pending:
            position, pending = pending, 0
            trade_qty, pending_qty = pending_qty, 0
            entry = extreme = opens[i]
            entry_time = times[i]
            if position > 0:
                stop = entry - config["k_trail"] * atr[i - 1]
            else:
                stop = entry + config["k_trail"] * atr[i - 1]
            if not np.isfinite(stop):
                position = 0
        if position and rolls[i]:
            exit_px = closes[i - 1] if i else opens[i]
            points = (exit_px - entry) * position
            rows.append((entry_time, times[i], symbol, entry, exit_px,
                         points, "roll", position, trade_qty))
            position = 0
        if position > 0:
            if lows[i] <= stop:
                exit_px = min(stop, opens[i])
                rows.append((entry_time, times[i], symbol, entry, exit_px,
                             exit_px - entry, "stop", 1, trade_qty))
                position = 0
            else:
                extreme = max(extreme, highs[i])
                stop = max(stop, extreme - config["k_trail"] * atr[i])
        elif position < 0:
            if highs[i] >= stop:
                exit_px = max(stop, opens[i])
                rows.append((entry_time, times[i], symbol, entry, exit_px,
                             entry - exit_px, "stop", -1, trade_qty))
                position = 0
            else:
                extreme = min(extreme, lows[i])
                stop = min(stop, extreme + config["k_trail"] * atr[i])
        if not position and not pending and signal[i]:
            requested = (int(quantity(times[i])) if callable(quantity)
                         else int(quantity))
            if requested > 0:
                pending, pending_qty = int(signal[i]), requested

    trades = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "dir", "qty",
    ])
    return trades[trades["entry_time"] >= START].reset_index(drop=True)


def pnl_stats(trades: pd.DataFrame, bars: dict[str, pd.DataFrame],
              friction: float = 3.5) -> tuple[pd.Series, dict]:
    pnl = hourly_mtm(trades, bars, friction)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    daily = pnl.groupby(pnl.index.normalize()).sum()
    yearly = pnl.groupby(pnl.index.year).sum()
    sd = daily.std(ddof=1)
    return pnl, {
        "net": float(pnl.sum()),
        "maxDD": float(drawdown.min()),
        "worst": float(yearly.min()),
        "worst_year": int(yearly.idxmin()),
        "sharpe": float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan,
        "p7": float(hourly_mtm(trades, bars, 7.0).sum()),
        "p14": float(hourly_mtm(trades, bars, 14.0).sum()),
        "train_2011_20": float(pnl[pnl.index.year <= 2020].sum()),
        "test_2021_end": float(pnl[pnl.index.year >= 2021].sum()),
        "y2011_15": float(pnl[pnl.index.year <= 2015].sum()),
        "y2016_20": float(pnl[(pnl.index.year >= 2016)
                               & (pnl.index.year <= 2020)].sum()),
        "y2021_end": float(pnl[pnl.index.year >= 2021].sum()),
        "contracts": int(trades["qty"].sum()),
        "trades": int(len(trades)),
        "short_trades": int((trades["dir"] < 0).sum()),
        "max_core": maximum_exposure(trades),
    }


def run():
    rg.START, rg.SCFG = START, dict(PRODUCTION)
    bars = {symbol: rg.load_60m(symbol) for symbol in SYMS}
    directions = {symbol: candidate_directions(frame)
                  for symbol, frame in bars.items()}
    names = tuple(directions[SYMS[0]])
    rows, pnl_paths = [], {}
    for name in names:
        pieces = [simulate_direction(bars[symbol], symbol,
                                     directions[symbol][name])
                  for symbol in SYMS]
        trades = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
        pnl, stats = pnl_stats(trades, bars)
        pnl_paths[name] = pnl
        rows.append({"candidate": name, **stats})
        print(name, stats, flush=True)
    result = pd.DataFrame(rows).sort_values("net", ascending=False)
    result.to_csv(HERE / "stage_a_results.csv", index=False)
    daily = pd.concat({name: pnl.groupby(pnl.index.normalize()).sum()
                       for name, pnl in pnl_paths.items()}, axis=1).fillna(0.0)
    daily.to_parquet(HERE / "stage_a_daily_pnl.parquet")
    print("\n", result.to_string(index=False,
          float_format=lambda value: f"{value:,.3f}"))
    return result, daily


if __name__ == "__main__":
    run()
