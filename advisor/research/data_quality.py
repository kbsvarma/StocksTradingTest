"""Fail-closed checks for the price panel used by the recommendation engine.

The gate separates dataset-level failures (which abort publication) from
symbol-level anomalies (which are quarantined from ranking).  A quarantine is
deliberately conservative: unexplained adjusted-price jumps should be reviewed,
not converted into momentum signals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping


def assess(panels: Mapping[str, object], requested: int, now=None) -> dict:
    import numpy as np
    import pandas as pd

    now = pd.Timestamp(now or datetime.now(timezone.utc)).tz_localize(None)
    errors: list[str] = []
    warnings: list[str] = []
    quarantine: dict[str, list[str]] = {}
    required = ("Close", "High", "Low", "Volume")

    for field in required:
        p = panels.get(field)
        if p is None or getattr(p, "empty", True):
            errors.append(f"{field}: missing or empty panel")
            continue
        if not p.index.is_unique:
            errors.append(f"{field}: duplicate dates")
        if not p.columns.is_unique:
            errors.append(f"{field}: duplicate tickers")
        if not p.index.is_monotonic_increasing:
            errors.append(f"{field}: dates are not increasing")

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                "quarantine": quarantine}

    close, high, low, volume = (panels[x] for x in required)
    common_dates = close.index
    common_cols = close.columns
    for field, panel in (("High", high), ("Low", low), ("Volume", volume)):
        if not panel.index.equals(common_dates):
            errors.append(f"{field}: date index is not aligned with Close")
        if not panel.columns.equals(common_cols):
            errors.append(f"{field}: ticker columns are not aligned with Close")

    if "SPY" not in common_cols:
        errors.append("Close: required benchmark SPY is absent")
    if "^VIX" not in common_cols:
        errors.append("Close: required benchmark ^VIX is absent")

    latest = pd.Timestamp(common_dates.max()).tz_localize(None)
    business_lag = int(len(pd.bdate_range(latest.normalize(), now.normalize())) - 1)
    if business_lag > 2:
        errors.append(f"Close: latest market date {latest.date()} is {business_lag} business days old")

    latest_coverage = float(close.loc[common_dates.max()].notna().mean())
    expected_coverage = len(common_cols) / max(int(requested), 1)
    if latest_coverage < 0.65:
        errors.append(f"Close: latest-row coverage is only {latest_coverage:.1%}")
    if expected_coverage < 0.70:
        errors.append(f"Close: only {len(common_cols)}/{requested} requested tickers survived")

    opens = panels.get("Open")
    if opens is not None:
        if not opens.index.equals(common_dates) or not opens.columns.equals(common_cols):
            errors.append("Open: dates/tickers not aligned with Close")
        opens = opens.reindex(index=common_dates, columns=common_cols)
    nonpositive = ((close <= 0) | (high <= 0) | (low <= 0)).any(axis=0)
    if opens is not None:
        nonpositive |= (opens <= 0).any(axis=0)
    negative_volume = (volume < 0).any(axis=0)
    ohlc_bad = ((low > high) | (close < low) | (close > high)).any(axis=0)
    if opens is not None:
        ohlc_bad |= ((opens < low) | (opens > high) | opens.isna()).any(axis=0)
    returns = close.pct_change(fill_method=None).tail(90)
    extreme = (returns.abs() > 0.50).any(axis=0)
    stale = close.tail(10).notna().sum(axis=0) < 5

    def mark(mask, reason: str) -> None:
        for ticker in mask.index[mask.fillna(False)]:
            quarantine.setdefault(str(ticker), []).append(reason)

    mark(nonpositive, "nonpositive OHLC value")
    mark(negative_volume, "negative volume")
    mark(ohlc_bad, "OHLC relationship violation")
    mark(extreme, "unexplained >50% adjusted one-day move in trailing 90 sessions")
    mark(stale, "fewer than 5 observations in trailing 10 sessions")

    # Market benchmarks cannot merely be quarantined: regime inference is a
    # portfolio-wide input, so questionable benchmark data aborts publication.
    for benchmark in ("SPY", "^VIX"):
        if benchmark in quarantine:
            errors.append(f"benchmark {benchmark} failed quality checks: " +
                          "; ".join(quarantine[benchmark]))

    finite_share = float(np.isfinite(close.to_numpy(dtype=float, na_value=np.nan)).mean())
    if finite_share < 0.80:
        warnings.append(f"Close: full-panel finite coverage is {finite_share:.1%}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "quarantine": quarantine,
        "latest_market_date": latest.date().isoformat(),
        "latest_coverage": round(latest_coverage, 4),
        "survival_coverage": round(expected_coverage, 4),
        "n_quarantined": len(quarantine),
    }
