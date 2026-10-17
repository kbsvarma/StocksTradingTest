import unittest

import breakout_study
import execution_audit
import study


def candle(rem, bid, ask, bid_open=None, ask_open=None):
    return {
        "t": 1_000 - rem * 60,
        "bc": str(bid),
        "ac": str(ask),
        "bo": str(bid if bid_open is None else bid_open),
        "ao": str(ask if ask_open is None else ask_open),
    }


class StudyTests(unittest.TestCase):
    def test_no_favorite_quote_is_mapped_to_no_space(self):
        quote = study.favorite_quote(candle(8, 0.11, 0.12))
        self.assertEqual(quote[0], "no")
        self.assertAlmostEqual(quote[1], 0.88)
        self.assertAlmostEqual(quote[2], 0.89)
        self.assertAlmostEqual(quote[3], 0.885)

    def test_entry_uses_worse_of_signal_and_following_open(self):
        market = study.Market(
            "TEST", 1_000, "yes", None, None, 0,
            {
                8: candle(8, 0.86, 0.87),
                7: candle(7, 0.88, 0.89, 0.88, 0.89),
            },
        )
        self.assertAlmostEqual(study.executable_entry(market, 8, "yes"), 0.89)

    def test_breakout_requires_crossing_from_below(self):
        crossed = study.Market(
            "CROSS", 1_000, "yes", None, None, 0,
            {8: candle(8, 0.86, 0.87), 9: candle(9, 0.81, 0.82), 7: candle(7, 0.86, 0.87)},
        )
        already_high = study.Market(
            "HIGH", 1_000, "yes", None, None, 0,
            {8: candle(8, 0.86, 0.87), 9: candle(9, 0.85, 0.86), 7: candle(7, 0.86, 0.87)},
        )
        trades = breakout_study.breakout(
            [crossed, already_high], (8, 7), 0.85, 0.93, 0.03, 1
        )
        self.assertEqual([trade.ticker for trade in trades], ["CROSS"])

    def test_breakout_is_one_trade_per_market(self):
        market = study.Market(
            "ONCE", 1_000, "yes", None, None, 0,
            {
                9: candle(9, 0.80, 0.81),
                8: candle(8, 0.85, 0.86),
                7: candle(7, 0.90, 0.91),
                6: candle(6, 0.90, 0.91),
            },
        )
        trades = breakout_study.breakout(
            [market], (8, 7), 0.85, 0.93, 0.03, 1
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].signal_rem, 8)

    def test_variable_fractional_timestamp(self):
        timestamp = execution_audit.trade_timestamp("2026-07-27T01:52:05.56599Z")
        self.assertEqual(int(timestamp), 1785117125)


if __name__ == "__main__":
    unittest.main()
