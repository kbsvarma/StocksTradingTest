import numpy as np
import pandas as pd

from engine import DayCell, simulate_day


def sample(day="2024-07-03", periods=6, roll_at=None):
    idx = pd.date_range(f"{day} 13:00", periods=periods, freq="h", tz="UTC")
    # July UTC 13:00 is 09:00 New York.
    close = np.arange(100.0, 100.0 + periods)
    frame = pd.DataFrame({
        "open": close - 0.25, "high": close + 0.5,
        "low": close - 0.5, "close": close,
        "roll": np.zeros(periods, dtype=int),
    }, index=idx)
    if roll_at is not None:
        frame.iloc[roll_at, frame.columns.get_loc("roll")] = 1
    return frame


def run(frame, close_hour=16):
    n = len(frame)
    return simulate_day(frame, "NQ", np.ones(n), np.ones(n, dtype=bool),
                        np.ones(n, dtype=bool),
                        DayCell(9, close_hour, 2.0, 2, 0.0),
                        pd.Timestamp("2020-01-01", tz="UTC"))


def test_flat_at_last_available_bar_on_short_session():
    trades = run(sample(periods=5))  # only labels 09:00 through 13:00 ET
    assert len(trades) == 1
    assert trades.iloc[0].reason == "eod"
    assert trades.iloc[0].exit_time == pd.Timestamp("2024-07-03 17:00", tz="UTC")


def test_no_trade_crosses_new_york_date():
    trades = run(sample(periods=9), close_hour=17)
    entry = trades.entry_time.dt.tz_convert("America/New_York").dt.date
    exit_ = trades.exit_time.dt.tz_convert("America/New_York").dt.date
    assert (entry == exit_).all()
    assert trades.exit_time.dt.tz_convert("America/New_York").dt.hour.max() <= 16


def test_roll_session_is_skipped():
    assert run(sample(periods=6, roll_at=3)).empty
