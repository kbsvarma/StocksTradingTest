"""Shared statistics helpers. Kept deliberately small and explicit."""
import numpy as np
import pandas as pd

IS_END = pd.Timestamp("2020-12-31", tz="America/New_York")
OOS_START = pd.Timestamp("2021-01-01", tz="America/New_York")

# Contract economics
ES_MULT = 50.0     # $/point full ES
MES_MULT = 5.0     # $/point micro ES
NQ_MULT = 20.0
MNQ_MULT = 2.0
# Friction per ROUND TRIP, micro contract, dollars (IBKR comm+fees + 0.5 tick/side slip)
# MES tick = 0.25 pt = $1.25; 0.5 tick = $0.625/side -> $1.25 slip RT; comm ~ $0.62/side -> ~$1.24 RT
MES_FRICTION_RT = 2.50
MNQ_FRICTION_RT = 2.50

def nw_tstat(x, lags=5):
    """Newey-West (HAC) t-stat for mean(x) != 0."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 10:
        return np.nan, n
    mu = x.mean()
    e = x - mu
    gamma0 = (e @ e) / n
    var = gamma0
    for k in range(1, lags + 1):
        w = 1.0 - k / (lags + 1)
        cov = (e[k:] @ e[:-k]) / n
        var += 2 * w * cov
    se = np.sqrt(var / n)
    if se == 0:
        return np.nan, n
    return mu / se, n

def ann_sharpe(daily_rets, periods_per_year=252):
    x = np.asarray(daily_rets, float)
    x = x[~np.isnan(x)]
    if len(x) < 10 or x.std() == 0:
        return np.nan
    return x.mean() / x.std() * np.sqrt(periods_per_year)

def split_is_oos(s):
    """s indexed by tz-aware ET timestamps -> (is_part, oos_part)."""
    return s[s.index <= IS_END], s[s.index >= OOS_START]

def worst_year_drop_tstat(series_by_date, lags=5):
    """Recompute t-stat after removing the single worst calendar year (by mean)."""
    df = pd.DataFrame({"v": series_by_date})
    df["yr"] = df.index.year
    yr_means = df.groupby("yr")["v"].mean()
    worst = yr_means.idxmin()
    kept = df[df["yr"] != worst]["v"]
    t, n = nw_tstat(kept.values, lags)
    return t, worst, yr_means[worst]

def summarize_dollar(net_dollars_by_date, label):
    """net_dollars_by_date: pd.Series of per-trade net $ P&L indexed by date."""
    x = net_dollars_by_date.dropna()
    t, n = nw_tstat(x.values)
    yrs = (x.index[-1] - x.index[0]).days / 365.25 if n else np.nan
    per_yr = x.sum() / yrs if yrs and yrs > 0 else np.nan
    sharpe = ann_sharpe(x.values, periods_per_year=n / yrs if yrs else 252)
    return {
        "label": label, "n": n, "mean_$": x.mean(), "t": t,
        "total_$": x.sum(), "$/yr": per_yr, "sharpe": sharpe,
        "%pos": (x > 0).mean() * 100, "worst_$": x.min(), "best_$": x.max(),
    }

def fmt(d):
    return (f"{d['label']:<26} n={d['n']:>4}  mean=${d['mean_$']:+7.2f}  t={d['t']:+5.2f}  "
            f"$/yr={d['$/yr']:+8.0f}  Sh={d['sharpe']:+4.2f}  %pos={d['%pos']:4.0f}  "
            f"worst=${d['worst_$']:+7.0f}")
