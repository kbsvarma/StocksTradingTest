"""Synthetic production-path checks for the iteration-11 lockout."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

from run_episode_search import simulate  # noqa: E402


def frame(index, signals):
    return pd.DataFrame({"atr": 1.0, "signal": signals}, index=index)


def policy(index):
    mondays = pd.DatetimeIndex(index).normalize() \
        - pd.to_timedelta(pd.DatetimeIndex(index).dayofweek, unit="D")
    return pd.DataFrame({"L_NQ": 3}, index=mondays.unique())


class LossSessionPathTest(unittest.TestCase):
    def _bars(self):
        ix = pd.date_range("2026-07-01 00:00", periods=72, freq="h", tz="UTC")
        B = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                          "close": 101.0, "roll": 0}, index=ix)
        return B

    def test_losing_stop_blocks_until_next_cme_session(self):
        B = self._bars()
        sig = np.zeros(len(B), dtype=bool)
        sig[21:] = True
        # Signal i=21 -> entry i=22 at 100, initial stop 98. Stop in i=23.
        B.iloc[23, B.columns.get_loc("low")] = 97.0
        B.iloc[24:, B.columns.get_loc("low")] = 99.0
        with patch("run_episode_search.strategy.signal_frame",
                   return_value=frame(B.index, sig)):
            T = simulate(B, "NQ", policy(B.index),
                         loss_session_rearm=True, liquidate_end=True)
        entries = T.groupby("episode").entry_time.min().sort_values()
        self.assertGreaterEqual(len(entries), 2)
        first_exit = T[T.episode == entries.index[0]].exit_time.iloc[0]
        second_entry = entries.iloc[1]
        first_session = (first_exit.tz_convert("America/New_York").normalize()
                         + pd.Timedelta(days=int(first_exit.tz_convert(
                             "America/New_York").hour >= 18)))
        second_session = (second_entry.tz_convert("America/New_York").normalize()
                          + pd.Timedelta(days=int(second_entry.tz_convert(
                              "America/New_York").hour >= 18)))
        self.assertNotEqual(first_session, second_session)

    def test_profitable_stop_can_reenter_same_session(self):
        B = self._bars()
        sig = np.zeros(len(B), dtype=bool)
        sig[21:] = True
        # Entry at 100. First held bar reaches 110 -> next stop 108; following
        # bar stops profitably and its close may schedule a replacement.
        B.iloc[22, B.columns.get_loc("high")] = 110.0
        B.iloc[22, B.columns.get_loc("low")] = 99.0
        B.iloc[23, B.columns.get_loc("open")] = 109.0
        B.iloc[23, B.columns.get_loc("low")] = 107.0
        B.iloc[24:, B.columns.get_loc("low")] = 99.0
        with patch("run_episode_search.strategy.signal_frame",
                   return_value=frame(B.index, sig)):
            T = simulate(B, "NQ", policy(B.index),
                         loss_session_rearm=True, liquidate_end=True)
        entries = T.groupby("episode").entry_time.min().sort_values()
        self.assertGreaterEqual(len(entries), 2)
        self.assertEqual(entries.iloc[1], B.index[24])


if __name__ == "__main__":
    unittest.main()
