"""Decision-path regressions for the SHADOW-ONLY slow+fast override line.

The override may only redirect NEW entries (engine binds weights at entry,
so open positions are structurally untouched); these tests pin the three
decision paths of the pure helper.
"""
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

from shadow_report import override_entry_weights  # noqa: E402


def _sure_week():
    return pd.Series({"YM": 3, "ES": 0, "NQ": 0})


def test_override_fires_at_threshold():
    # NQ beats the slow leader (YM) by exactly 2pp -> 1 slow / 2 fast / 0
    fast = {"YM": 0.00, "ES": -0.01, "NQ": 0.02}
    w = override_entry_weights(_sure_week(), fast)
    assert w == {"YM": 1, "ES": 0, "NQ": 2}


def test_no_override_below_threshold():
    # best challenger edge is 1.99pp -> weekly 3-0-0 stands
    fast = {"YM": 0.00, "ES": 0.0199, "NQ": -0.03}
    w = override_entry_weights(_sure_week(), fast)
    assert w == {"YM": 3, "ES": 0, "NQ": 0}


def test_unsure_week_keeps_111_regardless_of_fast_edge():
    week = pd.Series({"YM": 1, "ES": 1, "NQ": 1})
    fast = {"YM": -0.05, "ES": 0.00, "NQ": 0.10}   # huge fast edge
    w = override_entry_weights(week, fast)
    assert w == {"YM": 1, "ES": 1, "NQ": 1}


def test_strongest_challenger_wins_the_two_lot():
    # both challengers clear 2pp; the STRONGER one takes the 2 contracts
    fast = {"YM": 0.00, "ES": 0.025, "NQ": 0.04}
    w = override_entry_weights(_sure_week(), fast)
    assert w == {"YM": 1, "ES": 0, "NQ": 2}
