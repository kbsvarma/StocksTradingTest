#!/usr/bin/env python3

import unittest

from study_band_stop import BASELINE, SEVERE, Config, Market, side_quotes, simulated_exit


def candle(*, bid_low: float, bid_high: float, bid_close: float = 0.60, ask_close: float = 0.61):
    return {
        "t": 100,
        "bo": str(bid_close),
        "bc": str(bid_close),
        "bl": str(bid_low),
        "bh": str(bid_high),
        "ao": str(ask_close),
        "ac": str(ask_close),
        "al": str(max(0.0, bid_low + 0.01)),
        "ah": str(min(1.0, bid_high + 0.01)),
    }


def config(*, anchor: str = "band_low", policy: str = "fixed_take_10") -> Config:
    return Config(
        band_low=0.55,
        band_high=0.65,
        min_remaining=3,
        max_remaining=12,
        spread_cap=0.02,
        stop_distance=0.10,
        stop_anchor=anchor,
        exit_policy=policy,
    )


class QuoteConversionTests(unittest.TestCase):
    def test_no_quotes_are_exact_complements(self):
        source = candle(bid_low=0.39, bid_high=0.65, bid_close=0.55, ask_close=0.57)
        source.update({"bo": "0.54", "ao": "0.56", "al": "0.40", "ah": "0.70"})
        quote = side_quotes(source, "no")
        self.assertIsNotNone(quote)
        self.assertAlmostEqual(quote["bid_open"], 0.44)
        self.assertAlmostEqual(quote["bid_close"], 0.43)
        self.assertAlmostEqual(quote["bid_low"], 0.30)
        self.assertAlmostEqual(quote["bid_high"], 0.60)
        self.assertAlmostEqual(quote["ask_open"], 0.46)
        self.assertAlmostEqual(quote["ask_close"], 0.45)


class ExitTests(unittest.TestCase):
    def market(self, candles):
        return Market(
            ticker="TEST",
            family="KXBTC15M",
            close_ts=900,
            result="yes",
            volume=1000.0,
            candles=candles,
        )

    def test_band_low_anchor_reproduces_45_cent_stop(self):
        market = self.market([candle(bid_low=0.44, bid_high=0.75)])
        exit_price, reason, stopped, floor = simulated_exit(
            market, "yes", 0.60, 0, config(anchor="band_low"), BASELINE
        )
        self.assertAlmostEqual(exit_price, 0.45)
        self.assertEqual(reason, "stop")
        self.assertTrue(stopped)
        self.assertFalse(floor)

    def test_same_minute_target_and_stop_is_scored_as_stop(self):
        market = self.market([candle(bid_low=0.44, bid_high=0.75)])
        exit_price, reason, _, _ = simulated_exit(
            market, "yes", 0.60, 0, config(policy="fixed_take_10"), BASELINE
        )
        self.assertAlmostEqual(exit_price, 0.45)
        self.assertEqual(reason, "stop")

    def test_severe_floor_failure_is_not_hidden(self):
        market = self.market([candle(bid_low=0.30, bid_high=0.50)])
        exit_price, reason, stopped, floor = simulated_exit(
            market, "yes", 0.60, 0, config(), SEVERE
        )
        self.assertAlmostEqual(exit_price, 0.35)
        self.assertEqual(reason, "emergency_floor")
        self.assertTrue(stopped)
        self.assertTrue(floor)

    def test_entry_relative_anchor_remains_available_for_old_design(self):
        market = self.market([candle(bid_low=0.49, bid_high=0.55)])
        exit_price, reason, _, _ = simulated_exit(
            market, "yes", 0.60, 0, config(anchor="entry"), BASELINE
        )
        self.assertAlmostEqual(exit_price, 0.50)
        self.assertEqual(reason, "stop")


if __name__ == "__main__":
    unittest.main()
