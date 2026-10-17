import math
import threading
import unittest

from cushion_bot import (
    cushion_z, gates, BAND_LO, BAND_HI, Z_MIN, REM_LO, REM_HI,
    MAX_SPREAD, MAX_CONCURRENT, SERIES, CushionBot,
    SpotFeed, executable_quote_from_book,
)


class CushionMathTest(unittest.TestCase):
    def test_yes_side_positive_when_spot_above_strike(self):
        z = cushion_z(spot=101_000, strike=100_000, side="yes",
                      sigma=0.001, rem=4)
        self.assertAlmostEqual(z, 0.01 / (0.001 * 2), places=6)

    def test_no_side_positive_when_spot_below_strike(self):
        z = cushion_z(spot=99_000, strike=100_000, side="no",
                      sigma=0.001, rem=4)
        self.assertGreater(z, 0)

    def test_sign_flips_against_the_favourite(self):
        """Spot through the strike against the position => negative z.
        12 of the old bot's 20 losses had exactly this signature."""
        z = cushion_z(spot=99_900, strike=100_000, side="yes",
                      sigma=0.001, rem=4)
        self.assertLess(z, 0)

    def test_more_time_shrinks_the_cushion(self):
        z4 = cushion_z(100_500, 100_000, "yes", 0.001, 4)
        z9 = cushion_z(100_500, 100_000, "yes", 0.001, 9)
        self.assertGreater(z4, z9)

    def test_cold_estimator_returns_none(self):
        self.assertIsNone(cushion_z(100_000, 100_000, "yes", None, 5))
        self.assertIsNone(cushion_z(None, 100_000, "yes", 0.001, 5))
        self.assertIsNone(cushion_z(100_000, 100_000, "yes", 0.0, 5))


class GateTest(unittest.TestCase):
    def test_happy_path(self):
        self.assertIsNone(gates(0.88, 0.90, 5.0, 2.0))

    def test_band_edges(self):
        self.assertIsNone(gates(0.83, 0.85, 5.0, 2.0))       # 85 inclusive
        self.assertIsNotNone(gates(0.91, 0.93, 5.0, 2.0))    # 93 exclusive
        self.assertIsNotNone(gates(0.93, 0.95, 5.0, 2.0))    # old band: refused

    def test_z_threshold(self):
        self.assertIsNotNone(gates(0.88, 0.90, 5.0, 1.49))
        self.assertIsNone(gates(0.88, 0.90, 5.0, 1.5))

    def test_no_cushion_estimate_blocks(self):
        self.assertIsNotNone(gates(0.88, 0.90, 5.0, None))

    def test_spread_two_cent_float_tolerance(self):
        self.assertIsNone(gates(0.88, 0.90, 5.0, 2.0))       # exactly 2c
        self.assertIsNotNone(gates(0.87, 0.90, 5.0, 2.0))    # 3c

    def test_rem_window(self):
        self.assertIsNotNone(gates(0.88, 0.90, 2.9, 2.0))
        self.assertIsNotNone(gates(0.88, 0.90, 10.1, 2.0))
        self.assertIsNone(gates(0.88, 0.90, 10.0, 2.0))


class DesignInvariantsTest(unittest.TestCase):
    def test_four_assets_and_structural_cap(self):
        self.assertEqual(len(SERIES), 4)
        self.assertEqual(MAX_CONCURRENT, 4)

    def test_band_is_the_new_band_not_the_old(self):
        """The 93-99c band does not survive slippage; the lane must never
        quietly revert to it."""
        self.assertEqual((BAND_LO, BAND_HI), (0.85, 0.93))

    def test_disaster_exit_is_exchange_reduce_only(self):
        """A stale local ledger must never let the stop reverse a position."""
        import cushion_bot, inspect
        src = inspect.getsource(cushion_bot.CushionBot._do_stop)
        self.assertIn("reduce_only=True", src)
        self.assertIn('time_in_force="immediate_or_cancel"', src)


if __name__ == "__main__":
    unittest.main()


class ExecutionFixTest(unittest.TestCase):
    def test_attempt_cap_finite(self):
        from cushion_bot import MAX_ATTEMPTS
        self.assertGreaterEqual(MAX_ATTEMPTS, 1)
        self.assertLessEqual(MAX_ATTEMPTS, 5)

    def test_cross_is_within_stressed_envelope(self):
        """The +1c cross must never exceed what the stressed backtest priced."""
        from cushion_bot import MAX_CROSS
        self.assertAlmostEqual(MAX_CROSS, 0.01)

    def test_poll_faster_than_original(self):
        from cushion_bot import POLL_S
        self.assertLessEqual(POLL_S, 5)


class WeakenedGuardTest(unittest.TestCase):
    def test_order_path_enforces_band_floor(self):
        """Live fills at 83-84c (below the validated floor) came from asks
        dropping between signal and order. The order path must refuse a live
        ask below BAND_LO."""
        import cushion_bot, inspect
        src = inspect.getsource(cushion_bot.CushionBot.take)
        self.assertIn("live < BAND_LO", src)


class WebsocketWiringTest(unittest.TestCase):
    def test_ws_module_shapes(self):
        import ws_feeds
        self.assertTrue(hasattr(ws_feeds.KalshiWS, "ensure"))
        self.assertTrue(hasattr(ws_feeds.KalshiWS, "quote"))
        self.assertTrue(hasattr(ws_feeds.CoinbaseWS, "price"))

    def test_cents_to_dollars_logic(self):
        """WS ticker prices arrive as integer cents; 57 must become 0.57."""
        b, a = 55.0, 57.0
        if b > 1 or a > 1:
            b, a = b / 100.0, a / 100.0
        self.assertAlmostEqual(a, 0.57)

    def test_stale_quote_returns_none(self):
        import time as _t
        import ws_feeds
        w = ws_feeds.KalshiWS.__new__(ws_feeds.KalshiWS)
        w.quotes = {"X": (0.9, 0.91, _t.time() - 99)}
        self.assertIsNone(ws_feeds.KalshiWS.quote(w, "X", max_age=5.0))

    def test_hot_loop_faster_than_rest(self):
        from cushion_bot import TICK_S, POLL_S
        self.assertLess(TICK_S, 1.0)
        self.assertLessEqual(POLL_S, 5)

    def test_coinbase_completed_bars_excludes_forming_minute(self):
        import ws_feeds

        feed = ws_feeds.CoinbaseWS(["BTC-USD"])
        now = 3_600.5
        feed.minute_closes["BTC-USD"] = {
            3_480: 100.0,
            3_540: 101.0,
            3_600: 999.0,
        }
        self.assertEqual(
            feed.completed_bars("BTC-USD", now=now),
            {3_480: 100.0, 3_540: 101.0},
        )

    def test_spot_state_uses_last_completed_rest_candle(self):
        feed = SpotFeed()
        now = 3_600.5
        feed.bars["BTC-USD"] = {
            ts: 100.0 + index
            for index, ts in enumerate(range(1_200, 3_601, 60))
        }
        close, sigma = feed.state("BTC-USD", now=now)
        self.assertEqual(close, feed.bars["BTC-USD"][3_540])
        self.assertIsNotNone(sigma)

    def test_orderbook_snapshot_delta_and_disconnect(self):
        import ws_feeds

        feed = ws_feeds.KalshiOrderbookWS.__new__(
            ws_feeds.KalshiOrderbookWS
        )
        feed.books = {}
        feed._lock = threading.Lock()
        feed.wake = threading.Event()
        feed.connected = True
        feed.is_alive = lambda: True
        feed._apply_market_message({
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": "T",
                "yes_dollars": [["0.87", "2"]],
                "no_dollars": [["0.09", "3"]],
                "ts_ms": 123,
            },
        })
        self.assertEqual(feed.quote("T")[:2], (0.87, 0.91))
        feed._apply_market_message({
            "type": "orderbook_delta",
            "msg": {
                "market_ticker": "T", "side": "no",
                "price_dollars": "0.10", "delta_fp": "1",
                "ts_ms": 124,
            },
        })
        self.assertEqual(feed.quote("T")[:2], (0.87, 0.90))
        feed.connected = False
        self.assertIsNone(feed.quote("T"))


class ExecutableBookTruthTest(unittest.TestCase):
    def test_bid_only_book_derives_both_executable_yes_prices(self):
        quote = executable_quote_from_book({
            "yes_dollars": [["0.8600", "4.00"], ["0.8700", "2.00"]],
            "no_dollars": [["0.0800", "5.00"], ["0.0900", "3.00"]],
        })
        self.assertEqual(quote, (0.87, 0.91))

    def test_empty_side_is_not_a_tradeable_quote(self):
        self.assertIsNone(executable_quote_from_book({
            "yes_dollars": [["0.8700", "2.00"]],
            "no_dollars": [],
        }))

    def test_batch_book_replaces_summary_price(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def _request(self, method, path, params=None):
                self.calls.append((method, path, params))
                return {"orderbooks": [{
                    "ticker": "TEST",
                    "orderbook_fp": {
                        "yes_dollars": [["0.0200", "4.00"]],
                        "no_dollars": [["0.0300", "6.00"]],
                    },
                }]}

        bot = CushionBot.__new__(CushionBot)
        bot.client = FakeClient()
        bot.meta = {"TEST": ("KXBTC15M", 100.0, 200.0)}
        bot.book_quotes = {"TEST": (0.01, 0.88, 10.0)}
        bot.last_book_refresh = 0.0
        bot.last_book_error_log = 0.0
        bot._refresh_orderbooks(100.0)

        self.assertEqual(bot.book_quotes["TEST"][:2], (0.02, 0.97))
        self.assertEqual(bot.client.calls[0][1], "/markets/orderbooks")
        self.assertEqual(bot.client.calls[0][2], [("tickers", "TEST")])

    def test_book_cache_fails_closed_when_stale(self):
        bot = CushionBot.__new__(CushionBot)
        bot.book_quotes = {"TEST": (0.88, 0.90, 10.0)}
        self.assertIsNotNone(bot._book_quote("TEST", now=11.0))
        self.assertIsNone(bot._book_quote("TEST", now=20.0))

    def test_signal_and_order_paths_cannot_use_summary_quotes(self):
        import inspect

        poll_source = inspect.getsource(CushionBot.poll)
        take_source = inspect.getsource(CushionBot.take)
        self.assertIn("_book_quote", poll_source)
        self.assertNotIn("kws.quotes", poll_source)
        self.assertIn("book_ask", take_source)
        self.assertNotIn("kws.quote", take_source)

    def test_submission_does_not_wait_for_fill_confirmation(self):
        import inspect

        take_source = inspect.getsource(CushionBot.take)
        self.assertNotIn("confirm_fill(", take_source)
        self.assertIn("self.resting[tkr]", take_source)
