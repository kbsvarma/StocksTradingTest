"""Decision-path regressions for the SHADOW-ONLY adaptive-rotation lines."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ops"))

from shadow_report import graduated_qty  # noqa: E402


def _score(rows):
    return pd.DataFrame(rows, columns=["NQ", "ES", "YM"])


def test_small_gap_gives_111():
    q = graduated_qty(_score([[0.30, 0.10, 0.00]]))     # gap 0.20 < 0.35
    assert q.iloc[0].tolist() == [1, 1, 1]


def test_mid_gap_gives_210_leader_and_runner_up():
    q = graduated_qty(_score([[0.60, 0.10, 0.00]]))     # gap 0.50
    assert q.iloc[0].to_dict() == {"NQ": 2, "ES": 1, "YM": 0}


def test_large_gap_gives_300():
    q = graduated_qty(_score([[1.20, 0.10, 0.00]]))     # gap 1.10 >= 0.90
    assert q.iloc[0].to_dict() == {"NQ": 3, "ES": 0, "YM": 0}


def test_boundaries_exact():
    q = graduated_qty(_score([[0.35, 0.0, -1.0], [0.90, 0.0, -1.0]]))
    assert q.iloc[0].to_dict() == {"NQ": 2, "ES": 1, "YM": 0}   # 0.35 -> mid
    assert q.iloc[1].to_dict() == {"NQ": 3, "ES": 0, "YM": 0}   # 0.90 -> hard


def test_nan_rows_fall_back_to_111():
    q = graduated_qty(_score([[np.nan, 0.2, 0.1]]))
    assert q.iloc[0].tolist() == [1, 1, 1]


def test_every_row_allocates_exactly_three():
    rng = [[0.1 * i, -0.05 * i, 0.02 * i] for i in range(-10, 11)]
    q = graduated_qty(_score(rng))
    assert (q.sum(axis=1) == 3).all()
