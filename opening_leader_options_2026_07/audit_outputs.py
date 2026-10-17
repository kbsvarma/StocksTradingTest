#!/usr/bin/env python3
"""Mechanical causality and accounting checks for the four option waves."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ET = "America/New_York"


def target_clock(frame: pd.DataFrame, column: str) -> pd.Series:
    dates = pd.to_datetime(frame["date"])
    clocks = frame[column].str.split(":", expand=True).astype(int)
    return (
        dates.dt.tz_localize(ET)
        + pd.to_timedelta(clocks[0], unit="h")
        + pd.to_timedelta(clocks[1], unit="m")
    )


def audit_timing(filename: str, entry_window_minutes: int) -> list[str]:
    frame = pd.read_csv(HERE / filename)
    entry = pd.to_datetime(frame["actual_entry"], utc=True).dt.tz_convert(ET)
    entry_target = target_clock(frame, "entry_time")
    assert (entry >= entry_target).all(), f"{filename}: pre-decision entry"
    assert (
        entry < entry_target + pd.Timedelta(minutes=entry_window_minutes)
    ).all(), f"{filename}: entry outside registered window"

    timed = frame["exit_rule"] == "TIME"
    actual_exit = pd.to_datetime(
        frame.loc[timed, "actual_exit"], utc=True
    ).dt.tz_convert(ET)
    exit_target = target_clock(frame.loc[timed], "exit_time")
    assert (actual_exit <= exit_target).all(), f"{filename}: late timed exit"
    assert (
        actual_exit >= exit_target - pd.Timedelta(minutes=3)
    ).all(), f"{filename}: stale timed exit"
    return [
        f"{filename}: {len(frame)} entries causal",
        f"{filename}: {int(timed.sum())} timed exits causal",
    ]


def audit_wave4() -> list[str]:
    frame = pd.read_csv(HERE / "wave4_trades.csv")
    entry = pd.to_datetime(frame["actual_entry"], utc=True).dt.tz_convert(ET)
    date = pd.to_datetime(frame["date"]).dt.tz_localize(ET)
    entry_target = date + pd.Timedelta(hours=10)
    assert (entry >= entry_target).all(), "wave4: entry before formation ends"
    assert (entry < entry_target + pd.Timedelta(minutes=2)).all(), (
        "wave4: entry outside registered window"
    )
    exit_at = pd.to_datetime(frame["actual_exit"], utc=True).dt.tz_convert(ET)
    exit_target = target_clock(frame, "exit_time")
    assert (exit_at <= exit_target).all(), "wave4: late exit"
    assert (exit_at >= exit_target - pd.Timedelta(minutes=3)).all(), (
        "wave4: stale exit"
    )
    pair = frame["expression"] == "REVERSAL_PAIR"
    singles = frame.loc[~pair, "pnl"].sum()
    assert np.isfinite(singles)
    return [
        f"wave4_trades.csv: {len(frame)} post-formation entries causal",
        f"wave4_trades.csv: {len(frame)} exits causal",
    ]


def audit_accounting() -> list[str]:
    messages = []
    for filename in (
        "trades.csv",
        "wave2_trades.csv",
        "wave3_trades.csv",
        "wave4_trades.csv",
    ):
        frame = pd.read_csv(HERE / filename)
        assert np.isfinite(frame["pnl"]).all(), f"{filename}: non-finite P&L"
        assert np.isfinite(frame["stress_pnl"]).all(), (
            f"{filename}: non-finite stressed P&L"
        )
        assert (frame["stress_pnl"] <= frame["pnl"] + 1e-9).all(), (
            f"{filename}: stress improves an execution"
        )
        messages.append(f"{filename}: stress/accounting monotonic")
    return messages


def main() -> None:
    messages = []
    messages += audit_timing("wave2_trades.csv", 3)
    messages += audit_timing("wave3_trades.csv", 2)
    messages += audit_wave4()
    messages += audit_accounting()
    print("\n".join(messages))
    print("ALL OUTPUT AUDITS PASSED")


if __name__ == "__main__":
    main()
