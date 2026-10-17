"""Weekly rotation layer — the 'moving organism'.

Every Monday (UTC calendar, matching the reference engine's W-MON grid) the
three core indices are ranked by trailing 35-trading-day return computed from
daily closes THROUGH THE PRIOR DAY (shift 1 — no same-day information).

Modes (total 3 core contracts):
  defensive  : 1-1-1 always
  default    : 2-1-0, falls back to 1-1-1 when spread < unsure_spread_pp
  aggressive : 3-0-0, falls back to 1-1-1 when spread < unsure_spread_pp

Weights apply to trades at ENTRY time; open positions keep their entry weight.
"""
from __future__ import annotations

import math
import pandas as pd


def missing_expected_hourly_bars(index: pd.DatetimeIndex,
                                 market_open,
                                 until: pd.Timestamp | None = None
                                 ) -> list[pd.Timestamp]:
    """Return absent bar starts for hours when the session was open.

    The midpoint of each hour determines whether a bar should exist, which
    naturally excludes the daily maintenance break and the weekend while
    retaining every normal Globex bar. The caller supplies the frozen release
    calendar so live ingestion and nightly replay use the same schedule.

    ``until`` extends the expected grid PAST the last received bar (review
    2026-07-18 P1-2: a feed that dies and stays dead produced no expected
    hours after its last good bar, so the tail outage reconciled CLEAN).
    Gap checks that gate a soak verdict must pass the report time here.
    """
    if not isinstance(index, pd.DatetimeIndex) or index.empty:
        return []
    if index.tz is None:
        raise ValueError("hourly gap check requires timezone-aware timestamps")
    actual = pd.DatetimeIndex(index).tz_convert("UTC").floor("h").unique()
    end = actual.max()
    if until is not None:
        end = max(end, pd.Timestamp(until).tz_convert("UTC").floor("h")
                  - pd.Timedelta(hours=1))   # last COMPLETED hour
    expected = pd.date_range(actual.min(), end, freq="h", tz="UTC")
    actual_set = set(actual)
    return [stamp for stamp in expected
            if stamp not in actual_set and
            market_open((stamp + pd.Timedelta(minutes=30)).to_pydatetime())]


def validate_hourly_frame(frame: pd.DataFrame) -> str | None:
    """Return an error string unless every supplied OHLC row is sane."""
    required = {"open", "high", "low", "close"}
    if frame is None or frame.empty:
        return "empty refresh"
    if not required.issubset(frame.columns):
        return f"missing OHLC columns {sorted(required - set(frame.columns))}"
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        return "timestamps are not timezone-aware"
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        return "timestamps are unordered or duplicated"
    if not frame.index.equals(frame.index.floor("h")):
        return "timestamps are not aligned to hourly bar starts"
    for _, row in frame.iterrows():
        vals = [float(row[c]) for c in required]
        if not all(math.isfinite(v) and v > 0 for v in vals):
            return "non-finite/non-positive OHLC"
        if float(row["high"]) < max(float(row["open"]), float(row["close"])):
            return "high below open/close"
        if float(row["low"]) > min(float(row["open"]), float(row["close"])):
            return "low above open/close"
        if float(row["high"]) < float(row["low"]):
            return "high below low"
    return None


def validate_hourly_refresh(frame: pd.DataFrame,
                            expected_last: pd.Timestamp) -> str | None:
    """Require a sane refresh containing the expected completed endpoint."""
    invalid = validate_hourly_frame(frame)
    if invalid:
        return invalid
    completed = frame[frame.index < expected_last + pd.Timedelta(hours=1)]
    if completed.empty or completed.index[-1] != expected_last:
        got = completed.index[-1] if len(completed) else None
        return f"last completed bar {got}, expected {expected_last}"
    return None


def validate_rotation_inputs(daily_closes: dict[str, pd.Series],
                             asof_utc: pd.Timestamp,
                             lookback: int) -> list[str]:
    """Require aligned, positive, sufficiently deep completed daily inputs."""
    cutoff = asof_utc.normalize()
    prepared, errors = {}, []
    for sym, raw in daily_closes.items():
        s = raw[raw.index < cutoff].dropna()
        if not isinstance(s.index, pd.DatetimeIndex) or s.index.tz is None:
            errors.append(f"{sym}: timestamps not timezone-aware")
            continue
        if not s.index.is_monotonic_increasing or s.index.has_duplicates:
            errors.append(f"{sym}: dates unordered/duplicated")
        if len(s) < lookback + 1:
            errors.append(f"{sym}: only {len(s)} completed days")
        if len(s) and (not s.map(lambda v: math.isfinite(float(v)) and
                                  float(v) > 0).all()):
            errors.append(f"{sym}: invalid close")
        prepared[sym] = s
    if len(prepared) != len(daily_closes) or not prepared:
        return errors or ["no rotation inputs"]
    endpoints = {sym: s.index[-1] if len(s) else None
                 for sym, s in prepared.items()}
    if len(set(endpoints.values())) != 1:
        errors.append(f"unaligned endpoints: {endpoints}")
    common = None
    for s in prepared.values():
        common = s.index if common is None else common.intersection(s.index)
    if common is None or len(common) < lookback + 1:
        errors.append(
            f"only {0 if common is None else len(common)} common completed days")
    return errors


def trailing_returns(daily_closes: dict[str, pd.Series], asof_utc: pd.Timestamp,
                     lookback: int = 21) -> dict[str, float]:
    """21-trading-day return per symbol using closes strictly before asof's date."""
    out = {}
    cutoff = asof_utc.normalize()  # exclude today: use data through d-1
    for sym, s in daily_closes.items():
        s = s[s.index < cutoff].dropna()
        if len(s) < lookback + 1:
            out[sym] = float("nan")
        else:
            out[sym] = float(s.iloc[-1] / s.iloc[-1 - lookback] - 1.0)
    return out


def weights_for(rets: dict[str, float], mode: str, unsure_pp: float = 1.0,
                total: int = 3) -> dict:
    syms = sorted(rets, key=lambda s: rets[s], reverse=True)
    vals = [rets[s] for s in syms]
    valid = all(v == v for v in vals)  # no NaNs
    spread_pp = (vals[0] - vals[1]) * 100 if valid and len(vals) > 1 else float("nan")
    unsure = (not valid) or spread_pp < unsure_pp

    if mode == "defensive" or unsure:
        w = {s: 1 for s in syms}
        applied = "1-1-1"
    elif mode == "aggressive":
        w = {syms[0]: 3, syms[1]: 0, syms[2]: 0}
        applied = "3-0-0"
    else:  # default
        w = {syms[0]: 2, syms[1]: 1, syms[2]: 0}
        applied = "2-1-0"

    return {
        "weights": w,
        "ranking": syms,
        "returns": {s: rets[s] for s in syms},
        "spread_pp": None if spread_pp != spread_pp else round(spread_pp, 3),
        "unsure": bool(unsure),
        "applied": applied,
        "mode": mode,
    }


def monday_of_week(ts_utc: pd.Timestamp) -> pd.Timestamp:
    d = ts_utc.normalize()
    return d - pd.Timedelta(days=d.dayofweek)
