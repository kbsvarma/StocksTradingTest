#!/usr/bin/env python3

import unittest

from study_protected_profit import BASELINE, Config, Market, simulate_path


def candle(bid_open: float, bid_high: float, bid_low: float, bid_close: float, t: int = 100):
    return {
        "t": t,
        "bo": str(bid_open),
        "bc": str(bid_close),
        "bl": str(bid_low),
        "bh": str(bid_high),
        "ao": str(min(1, bid_open + 0.01)),
        "ac": str(min(1, bid_close + 0.01)),
        "al": str(min(1, bid_low + 0.01)),
        "ah": str(min(1, bid_high + 0.01)),
    }


def reference() -> Config:
    return Config(0.60, 0.70, 0.45, 0.06, 0.13, 1, 12, 0.02)


def market(candles, result="yes") -> Market:
    return Market("TEST", "KXBTC15M", 900, result, 1000.0, candles)


def run(candles, result="yes"):
    return simulate_path(
        market(candles, result),
        "yes",
        0.65,
        0,
        reference(),
        BASELINE,
        "high_low",
        60,
        10,
    )


class ProtectedProfitStateMachineTests(unittest.TestCase):
    def test_full_winner_is_not_sold_at_ideal_first_touch(self):
        trade = run([candle(0.65, 0.85, 0.79, 0.84)])
        self.assertEqual(trade.exit_reason, "settlement")
        self.assertIsNone(trade.exit_price)
        self.assertTrue(trade.ideal_armed)
        self.assertAlmostEqual(trade.pnl, 1 - 0.65 - (0.07 * 0.65 * 0.35))

    def test_ideal_reversal_exits_at_ideal(self):
        trade = run(
            [
                candle(0.65, 0.85, 0.79, 0.84),
                candle(0.84, 0.88, 0.77, 0.78, t=160),
            ]
        )
        self.assertEqual(trade.exit_reason, "ideal_protection")
        self.assertAlmostEqual(trade.exit_price, 0.78)

    def test_safe_reversal_before_ideal_exits_at_safe(self):
        trade = run(
            [
                candle(0.65, 0.75, 0.72, 0.74),
                candle(0.74, 0.76, 0.70, 0.71, t=160),
            ]
        )
        self.assertEqual(trade.exit_reason, "safe_protection")
        self.assertAlmostEqual(trade.exit_price, 0.71)

    def test_direct_loss_uses_absolute_45_cent_stop(self):
        trade = run([candle(0.65, 0.66, 0.44, 0.46)])
        self.assertEqual(trade.exit_reason, "absolute_stop")
        self.assertAlmostEqual(trade.exit_price, 0.45)
        self.assertTrue(trade.stopped)


if __name__ == "__main__":
    unittest.main()
