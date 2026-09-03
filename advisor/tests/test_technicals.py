from __future__ import annotations

import numpy as np
import pandas as pd

from advisor.research.technicals import compute_frame


def test_technical_frame_computes_bounded_interpretable_state():
    dates = pd.bdate_range("2025-01-02", periods=280)
    base = np.linspace(100, 180, len(dates))
    close = pd.DataFrame({"AAA": base, "BBB": base[::-1],
                          "SPY": np.linspace(100, 130, len(dates))}, index=dates)
    high, low = close * 1.01, close * .99
    volume = pd.DataFrame(2_000_000.0, index=dates, columns=close.columns)
    frame = compute_frame(close, high, low, volume,
                          {"stocks": {"AAA": "Tech", "BBB": "Tech"}})
    assert set(frame.index) == {"AAA", "BBB"}
    assert frame.loc["AAA", "trend_stack"] == 3
    assert frame.loc["BBB", "trend_stack"] == 0
    assert 0 <= frame.loc["AAA", "state_confidence"] <= 100
    assert 0 <= frame.loc["AAA", "rsi14"] <= 100
    assert frame.loc["AAA", "rs_spy_63d_pct"] > 0


def test_technical_frame_requires_sufficient_history():
    dates = pd.bdate_range("2026-01-02", periods=50)
    close = pd.DataFrame({"AAA": np.arange(50) + 100.0,
                          "SPY": np.arange(50) + 100.0}, index=dates)
    volume = pd.DataFrame(1_000_000.0, index=dates, columns=close.columns)
    frame = compute_frame(close, close * 1.01, close * .99, volume,
                          {"stocks": {"AAA": "Tech"}})
    assert frame.empty
