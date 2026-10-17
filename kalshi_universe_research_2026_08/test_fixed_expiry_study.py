from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fixed_expiry_study import minute_candidate


CLOSE_TS = int(datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc).timestamp())


def candle(remaining: int, yes_bid: float, yes_ask: float) -> dict:
    return {
        "t": CLOSE_TS - remaining * 60,
        "bc": yes_bid,
        "ac": yes_ask,
        "bo": yes_bid,
        "ao": yes_ask,
    }


def market() -> dict:
    return {
        "ticker": "KXXRP15M-TEST",
        "event_ticker": "KXXRP15M-TEST",
        "close_time": "2026-01-01T00:15:00Z",
        "result": "yes",
    }


class MinuteCandidateTests(unittest.TestCase):
    def test_accepts_exact_two_cent_spread(self) -> None:
        candles = [candle(rem, 0.84, 0.86) for rem in range(14, 6, -1)]

        trade = minute_candidate(
            market(),
            candles,
            window=(8, 12),
            band=(0.80, 0.90),
            spread=0.02,
            observations=3,
        )

        self.assertIsNotNone(trade)

    def test_rejects_a_favorite_that_flipped_earlier(self) -> None:
        candles = [candle(rem, 0.84, 0.86) for rem in range(14, 6, -1)]
        candles[1] = candle(13, 0.14, 0.16)

        trade = minute_candidate(
            market(),
            candles,
            window=(8, 12),
            band=(0.80, 0.90),
            spread=0.02,
            observations=3,
        )

        self.assertIsNone(trade)


if __name__ == "__main__":
    unittest.main()
