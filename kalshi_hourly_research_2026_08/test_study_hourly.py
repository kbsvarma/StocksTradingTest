import unittest

from study_hourly import candidate_snapshot, fee, student_t_survival


def candle(ts, bid, ask, volume=1.0, open_bid=None, open_ask=None):
    return {
        "t": ts,
        "bo": bid if open_bid is None else open_bid,
        "bc": bid,
        "ao": ask if open_ask is None else open_ask,
        "ac": ask,
        "v": volume,
    }


class HourlyStudyTests(unittest.TestCase):
    def test_student_t_survival_known_values(self):
        self.assertAlmostEqual(student_t_survival(0.0, 10), 0.5)
        self.assertAlmostEqual(student_t_survival(1.0, 1), 0.25)
        self.assertAlmostEqual(student_t_survival(1.812, 10), 0.05, delta=0.001)

    def test_entry_uses_next_minute_adverse_ask(self):
        close_ts = 3_600
        signal_ts = close_ts - 5 * 60
        market = {
            "ticker": "SERIES-EVENT-T1",
            "event_ticker": "SERIES-EVENT",
            "close_time": "1970-01-01T01:00:00Z",
            "result": "yes",
        }
        candles = {
            signal_ts - 60: candle(signal_ts - 60, 0.88, 0.90),
            signal_ts: candle(signal_ts, 0.89, 0.91),
            signal_ts + 60: candle(
                signal_ts + 60,
                0.91,
                0.93,
                open_bid=0.90,
                open_ask=0.92,
            ),
        }
        trade = candidate_snapshot(market, candles, remaining=5)
        self.assertIsNotNone(trade)
        self.assertEqual(trade["entry"], 0.92)
        self.assertAlmostEqual(trade["pnl"], (1.0 - 0.92) - fee(0.92))

    def test_signal_requires_causal_volume(self):
        close_ts = 3_600
        signal_ts = close_ts - 5 * 60
        market = {
            "ticker": "SERIES-EVENT-T1",
            "event_ticker": "SERIES-EVENT",
            "close_time": "1970-01-01T01:00:00Z",
            "result": "yes",
        }
        candles = {
            signal_ts: candle(signal_ts, 0.89, 0.91, volume=0.0),
            signal_ts + 60: candle(signal_ts + 60, 0.90, 0.92, volume=10.0),
        }
        self.assertIsNone(candidate_snapshot(market, candles, remaining=5))


if __name__ == "__main__":
    unittest.main()
