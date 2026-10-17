"""Causality regression for the core-priority bear overlay."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(ROOT/"trend_bot"),str(ROOT/"trend_bot"/"research")]

from run_bear_profit_overlay import core_priority_short  # noqa: E402


class CorePriorityTest(unittest.TestCase):
    def bars(self):
        ix=pd.date_range("2026-07-01",periods=30,freq="h",tz="UTC")
        B=pd.DataFrame({"open":100.0,"high":101.0,"low":89.0,
                        "close":100.0,"roll":0},index=ix)
        B.iloc[5:,B.columns.get_loc("open")]=90.0
        B.iloc[5:,B.columns.get_loc("high")]=90.5
        B.iloc[5:,B.columns.get_loc("close")]=90.0
        return B

    def frame(self,B):
        return pd.DataFrame({"atr":1.0,"vol_ok":True,"session_ok":True},
                            index=B.index)

    def test_future_core_start_closes_existing_short_at_that_open(self):
        B=self.bars(); gate=np.ones(len(B),dtype=bool)
        longs=pd.DataFrame({"entry_time":[B.index[8]],
                            "exit_time":[B.index[12]]})
        with patch("run_bear_profit_overlay.strategy.signal_frame",
                   return_value=self.frame(B)):
            T=core_priority_short(B,gate,longs,donch=1,k=2.0)
        first=T.iloc[0]
        self.assertEqual(first.entry_time,B.index[6])
        self.assertEqual(first.exit_time,B.index[8])
        self.assertEqual(first.reason,"core_long")
        self.assertEqual(first.exit_px,B.iloc[8].open)

    def test_pending_short_is_cancelled_when_core_starts(self):
        B=self.bars(); gate=np.zeros(len(B),dtype=bool); gate[7]=True
        longs=pd.DataFrame({"entry_time":[B.index[8]],
                            "exit_time":[B.index[-1]]})
        with patch("run_bear_profit_overlay.strategy.signal_frame",
                   return_value=self.frame(B)):
            T=core_priority_short(B,gate,longs,donch=1,k=2.0)
        self.assertTrue(T.empty)


if __name__=="__main__":unittest.main()
