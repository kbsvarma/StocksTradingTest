"""Causal adaptive-rotation equation screen.

This is research-only.  It imports the corrected reference execution engine
and never touches trend_bot/config.yaml, state, or a broker connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research"),
                str(ROOT / "strategy_extension_2026_07")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from rotation import monday_of_week, trailing_returns, weights_for  # noqa: E402
from run_policy_search import hourly_mtm  # noqa: E402


START = pd.Timestamp("2011-01-01", tz="UTC")
SYMS = ("NQ", "ES", "YM")
PRODUCTION = dict(donchian_n=20, atr_n=20, k_trail=2.0,
                  vol_gate_q=.95, vol_gate_window_bars=24 * 365,
                  vol_gate_min_bars=24 * 60, session_block_et=(2, 6))
D15 = dict(PRODUCTION, donchian_n=15, k_trail=7.0,
           vol_gate_q=.925, session_block_et=(2, 7))


def cross_z(frame: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z score, preserving missing rows as missing."""
    mean = frame.mean(axis=1)
    sd = frame.std(axis=1, ddof=0).replace(0, np.nan)
    return frame.sub(mean, axis=0).div(sd, axis=0).fillna(0.0)


def current_horizon_returns(
        bars: dict[str, pd.DataFrame], daily: dict[str, pd.Series],
        index: pd.DatetimeIndex, horizons=(3, 5, 10, 21, 35),
) -> dict[int, pd.DataFrame]:
    """Return from the h-th prior completed daily close to this hourly close.

    The timestamped hourly close is known when the strategy decides whether to
    schedule an entry at the next bar's open.  No forming-bar value is used.
    """
    closes = pd.concat({s: bars[s]["close"].reindex(index) for s in SYMS},
                       axis=1)
    out = {h: pd.DataFrame(index=index, columns=SYMS, dtype=float)
           for h in horizons}
    for day, locations in closes.groupby(closes.index.normalize()).groups.items():
        for sym in SYMS:
            prior = daily[sym][daily[sym].index < day]
            for h in horizons:
                if len(prior) >= h:
                    out[h].loc[locations, sym] = (
                        closes.loc[locations, sym] / prior.iloc[-h] - 1.0)
    return out


def frozen_monday_return(daily: dict[str, pd.Series],
                         index: pd.DatetimeIndex,
                         lookback: int = 35) -> pd.DataFrame:
    """Monday L35 score, held as a feature (not necessarily as allocation)."""
    rows = {}
    for mon in sorted(set(index.map(monday_of_week))):
        rows[mon] = trailing_returns(daily, mon, lookback)
    weekly = pd.DataFrame(rows).T[list(SYMS)]
    return weekly.reindex(index.map(monday_of_week)).set_axis(index)


def daily_volatility(daily: dict[str, pd.Series], index: pd.DatetimeIndex,
                     horizon: int) -> pd.DataFrame:
    rows = {}
    for day in sorted(set(index.normalize())):
        row = {}
        for sym in SYMS:
            r = daily[sym][daily[sym].index < day].pct_change().dropna().tail(horizon)
            row[sym] = float(r.std(ddof=1)) if len(r) >= horizon else np.nan
        rows[day] = row
    d = pd.DataFrame(rows).T
    return d.reindex(index.normalize()).set_axis(index)


def trend_quality(daily: dict[str, pd.Series], index: pd.DatetimeIndex,
                  horizon: int) -> pd.DataFrame:
    """Causal log-price slope divided by its regression standard error."""
    rows = {}
    x = np.arange(horizon, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    for day in sorted(set(index.normalize())):
        row = {}
        for sym in SYMS:
            values = daily[sym][daily[sym].index < day].tail(horizon)
            if len(values) < horizon:
                row[sym] = np.nan
                continue
            y = np.log(values.to_numpy(dtype=float))
            slope = float(np.dot(xc, y - y.mean()) / denom)
            resid = y - (y.mean() + slope * xc)
            se = np.sqrt(np.dot(resid, resid) / max(horizon - 2, 1) / denom)
            row[sym] = slope / se if se > 0 else 0.0
        rows[day] = row
    d = pd.DataFrame(rows).T
    return d.reindex(index.normalize()).set_axis(index)


def kalman_excess(daily: dict[str, pd.Series], index: pd.DatetimeIndex,
                  process_ratio: float) -> pd.DataFrame:
    """One-step-lagged scalar filters of return excess over the basket."""
    common = daily[SYMS[0]].index
    for sym in SYMS[1:]:
        common = common.intersection(daily[sym].index)
    prices = pd.concat({s: daily[s].reindex(common) for s in SYMS}, axis=1)
    returns = prices.pct_change()
    excess = returns.sub(returns.mean(axis=1), axis=0)
    state = np.zeros(len(SYMS)); covariance = np.ones(len(SYMS))
    rows = {}
    # Use a rolling, strictly-prior observation variance.  The state stored for
    # day d has only observed returns through d-1 and is therefore tradable.
    for i, day in enumerate(common):
        rows[day] = state.copy()
        if i == 0 or not np.isfinite(excess.iloc[i].to_numpy()).all():
            continue
        history = excess.iloc[max(1, i - 63):i]
        obs_var = history.var(ddof=1).to_numpy()
        finite_var = obs_var[np.isfinite(obs_var) & (obs_var > 0)]
        fallback = float(np.median(finite_var)) if len(finite_var) else 1e-4
        obs_var = np.where(np.isfinite(obs_var) & (obs_var > 0), obs_var,
                           fallback)
        process_var = process_ratio * obs_var
        pred_cov = covariance + process_var
        gain = pred_cov / (pred_cov + obs_var)
        state = state + gain * (excess.iloc[i].to_numpy() - state)
        covariance = (1.0 - gain) * pred_cov
    filtered = pd.DataFrame(rows, index=SYMS).T
    return filtered.reindex(index.normalize(), method="ffill").set_axis(index)


def allocation(score: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Convert a cross-sectional score to causal integer quantities."""
    values = score.to_numpy(dtype=float)
    order = np.argsort(-values, axis=1)
    valid = np.isfinite(values).all(axis=1)
    gap = (values[np.arange(len(values)), order[:, 0]]
           - values[np.arange(len(values)), order[:, 1]])
    qty = np.zeros_like(values, dtype=int)
    qty[~valid] = 1
    for i in np.flatnonzero(valid):
        a, b = order[i, 0], order[i, 1]
        if mode == "hard":
            qty[i, a] = 3
        elif mode == "conf":
            if gap[i] < .50:
                qty[i] = 1
            else:
                qty[i, a] = 3
        elif mode == "graduated":
            if gap[i] < .35:
                qty[i] = 1
            elif gap[i] < .90:
                qty[i, a], qty[i, b] = 2, 1
            else:
                qty[i, a] = 3
        elif mode == "softmax":
            p = np.exp(values[i] - values[i].max())
            p /= p.sum()
            raw = 3.0 * p
            base = np.floor(raw).astype(int)
            for j in np.argsort(-(raw - base))[:3 - base.sum()]:
                base[j] += 1
            qty[i] = base
        else:
            raise ValueError(mode)
    assert (qty.sum(axis=1) == 3).all()
    return pd.DataFrame(qty, index=score.index, columns=score.columns)


def baseline_policy(daily: dict[str, pd.Series],
                    index: pd.DatetimeIndex) -> pd.DataFrame:
    rows = {}
    for mon in sorted(set(index.map(monday_of_week))):
        result = weights_for(trailing_returns(daily, mon, 35),
                             "aggressive", 1.0, 3)
        rows[mon] = result["weights"]
    weekly = pd.DataFrame(rows).T[list(SYMS)].astype(int)
    return weekly.reindex(index.map(monday_of_week)).set_axis(index)


def feature_library(bars: dict[str, pd.DataFrame]):
    daily = {s: bars[s]["close"].resample("1D").last().dropna() for s in SYMS}
    index = bars[SYMS[0]].index
    for sym in SYMS[1:]:
        index = index.intersection(bars[sym].index)
    returns = current_horizon_returns(bars, daily, index)
    zret = {h: cross_z(frame) for h, frame in returns.items()}
    monday35 = frozen_monday_return(daily, index)
    zmonday = cross_z(monday35)
    vol21, vol35 = daily_volatility(daily, index, 21), daily_volatility(daily, index, 35)
    quality = {h: cross_z(trend_quality(daily, index, h)) for h in (10, 21, 35)}
    kalman = {q: cross_z(kalman_excess(daily, index, q))
              for q in (.001, .01, .10)}

    scores: dict[str, pd.DataFrame] = {
        "live_R35": zret[35],
        "voladj_R21": cross_z(returns[21].div(vol21)),
        "voladj_R35": cross_z(returns[35].div(vol35)),
    }
    for foundation, slow in (("week", zmonday), ("live", zret[35])):
        for h in (3, 5, 10, 21):
            for weight in (.25, .5, 1.0, 2.0):
                scores[f"{foundation}35_R{h}_W{weight:g}"] = (
                    slow + weight * zret[h])
    acceleration = cross_z(returns[5] - returns[21])
    for foundation, slow in (("week35", zmonday), ("live35", zret[35]),
                             ("live21", zret[21])):
        for weight in (.5, 1.0, 2.0):
            scores[f"{foundation}_accel_W{weight:g}"] = slow + weight * acceleration
    scores["volblend_21_35"] = (cross_z(returns[21].div(vol21))
                                 + cross_z(returns[35].div(vol35)))
    for h, qscore in quality.items():
        scores[f"quality_{h}"] = qscore
        for weight in (.25, .5, 1.0):
            scores[f"week35_quality{h}_W{weight:g}"] = zmonday + weight * qscore
    for ratio, kscore in kalman.items():
        scores[f"kalman_Q{ratio:g}"] = kscore
        scores[f"week35_kalman_Q{ratio:g}"] = zmonday + kscore

    # Deterministic regime adaptation: use a fast component only when its rank
    # is persistent and the three-index fast dispersion is above its rolling
    # one-year median; otherwise defer to the slow score.
    fast = zret[5]
    fast_leader = pd.Series(np.argmax(fast.to_numpy(), axis=1), index=index)
    persistent = fast_leader.eq(fast_leader.shift(1)) & fast_leader.eq(fast_leader.shift(2))
    dispersion = returns[5].max(axis=1) - returns[5].min(axis=1)
    high_dispersion = dispersion > dispersion.rolling(24 * 252,
                                                       min_periods=24 * 60).median().shift(1)
    adaptive_weight = (persistent & high_dispersion).astype(float)
    scores["regime_fast5"] = zmonday + fast.mul(adaptive_weight, axis=0)
    scores["regime_fast5_double"] = zmonday + fast.mul(2 * adaptive_weight,
                                                        axis=0)
    return daily, index, scores


def policy_quantity(policy: pd.DataFrame, sym: str):
    values = policy[sym]

    def lookup(stamp):
        return int(values.asof(stamp)) if stamp >= values.index[0] else 1
    return lookup


def maximum_exposure(trades: pd.DataFrame) -> int:
    events = []
    for trade in trades.itertuples():
        events.extend(((trade.entry_time, 1, trade.qty),
                       (trade.exit_time, 0, -trade.qty)))
    if not events:
        return 0
    frame = pd.DataFrame(events, columns=("time", "order", "change"))
    frame = frame.sort_values(["time", "order"])
    return int(frame["change"].cumsum().max())


def evaluate_policy(name: str, policy: pd.DataFrame,
                    bars: dict[str, pd.DataFrame]) -> dict:
    pieces = [rg.simulate_weighted(bars[sym], sym,
                                   policy_quantity(policy, sym)).assign(dir=1)
              for sym in SYMS]
    trades = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)
    pnl = hourly_mtm(trades, bars, rg.FR)
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0.0)
    yearly = pnl.groupby(pnl.index.year).sum()
    contracts = int(trades["qty"].sum())
    gross = float(pnl.sum() + rg.FR * contracts)
    return {
        "cell": name, "net": float(pnl.sum()), "maxDD": float(drawdown.min()),
        "worst": float(yearly.min()), "worst_year": int(yearly.idxmin()),
        "p7": gross - 7.0 * contracts, "p14": gross - 14.0 * contracts,
        "contracts": contracts, "trades": len(trades),
        "max_core": maximum_exposure(trades),
        "y2011_15": float(pnl[pnl.index.year <= 2015].sum()),
        "y2016_20": float(pnl[(pnl.index.year >= 2016)
                               & (pnl.index.year <= 2020)].sum()),
        "y2021_end": float(pnl[pnl.index.year >= 2021].sum()),
        "ex2026": float(pnl[pnl.index.year < 2026].sum()),
        "y2022": float(yearly.get(2022, 0.0)),
        "y2026": float(yearly.get(2026, 0.0)),
    }


def run_screen(config=PRODUCTION, label="production") -> pd.DataFrame:
    rg.START, rg.SCFG = START, dict(config)
    bars = {s: rg.load_60m(s) for s in SYMS}
    daily, index, scores = feature_library(bars)
    policies = {"weekly_L35": baseline_policy(daily, index)}
    for score_name, score in scores.items():
        for mode in ("hard", "conf", "graduated", "softmax"):
            policies[f"{score_name}__{mode}"] = allocation(score, mode)

    original_signal = strategy.signal_frame
    cached = {id(frame): original_signal(frame, rg.SCFG)
              for frame in bars.values()}
    strategy.signal_frame = lambda frame, ignored: cached[id(frame)]
    rows = []
    try:
        for number, (name, policy) in enumerate(policies.items(), 1):
            rows.append(evaluate_policy(name, policy, bars))
            if number % 25 == 0:
                print(f"{label}: {number}/{len(policies)}", flush=True)
    finally:
        strategy.signal_frame = original_signal
    result = pd.DataFrame(rows).sort_values("net", ascending=False)
    out = HERE / f"screen_{label}.parquet"
    result.to_parquet(out, index=False)
    print(f"wrote {out}")
    return result


def print_table(result: pd.DataFrame, limit=30):
    columns = ["cell", "net", "maxDD", "worst", "p7", "p14",
               "y2011_15", "y2016_20", "y2021_end", "ex2026",
               "contracts", "max_core"]
    print(result[columns].head(limit).to_string(index=False,
          formatters={c: (lambda x: f"{x:,.0f}") for c in columns[1:-1]}))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "production"
    cfg = PRODUCTION if which == "production" else D15
    print_table(run_screen(cfg, which))
