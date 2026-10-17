"""Offline regression for the single authorized BTC-original threshold change."""
import dataclasses
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import engine
import lanes
import market_data
import operations_view
import runner
from test_engine import FakeExchange, signal
import test_signals


class BtcOriginalThreshold(unittest.TestCase):
    def test_existing_profiles_and_btc_override_preserved(self):
        path = Path(__file__).resolve().parents[1] / 'original/lanes.py'
        spec = importlib.util.spec_from_file_location('prior_lanes', path)
        prior = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = prior
        spec.loader.exec_module(prior)
        for asset in prior.ALL_ASSETS:
            for lane in lanes.LANES:
                with self.subTest(asset=asset, lane=lane):
                    record = dict(asset=asset, lane=lane)
                    old = dataclasses.asdict(prior.lane_for(record))
                    new = dataclasses.asdict(lanes.lane_for(record))
                    self.assertEqual(old, new)
                    for minute in range(1, 11):
                        expected = 3 if asset == 'BTC' and lane == lanes.Z2 else prior.lane_for(record).threshold(minute)
                        self.assertEqual(lanes.lane_for(record).threshold(minute), expected)

    def test_untagged_btc_and_ticker_inference_use_original_override(self):
        self.assertEqual(lanes.lane_for(dict(ticker='KXBTC15M-TEST')).z, 3)
        self.assertEqual(lanes.lane_for(dict(ticker='KXETH15M-TEST')).z, 2)

    def test_scanner_rejects_below_three_and_qualifies_three(self):
        T = 12000
        for asset, z, expected in [('BTC', 2.5, 'cushion_gate'), ('BTC', 2.999, 'cushion_gate'), ('BTC', 3.001, 'paper_fill'), ('ETH', 2.5, 'paper_fill')]:
            with self.subTest(asset=asset, z=z):
                responses = test_signals.PaperProbeTests().responses(T)
                responses[0]['markets'][0]['ticker'] = 'KX'+asset+'15M-TEST'
                sigma = (110/100-1)/(z*math.sqrt(7))
                with patch.object(market_data, 'get', side_effect=responses) as get, patch('time.time', return_value=T+1), patch.object(market_data.statistics, 'pstdev', return_value=sigma):
                    row = market_data.scan(asset, T, set())
                self.assertEqual(row['status'], expected)
                self.assertEqual(row['required_z'], 3 if asset=='BTC' else 2)
                self.assertEqual(get.call_count, 3 if expected=='cushion_gate' else 4)

    def test_final_order_check_independently_rejects_old_btc_signal(self):
        with tempfile.TemporaryDirectory() as root:
            store = engine.Store(Path(root)/'ledger.sqlite3')
            try:
                exchange = FakeExchange()
                e = engine.Engine(exchange, store, Path(root), live=True, clock=lambda:1010)
                for z in (2, 2.5, 2.999999):
                    s = signal(); s['z'] = z
                    with self.assertRaisesRegex(ValueError, 'signal gates failed'):
                        e.attempt(s)
                self.assertEqual(exchange.posted, [])
                self.assertEqual(store.all(), [])
                s = signal(); s['z'] = 3
                self.assertEqual(e.attempt(s), 'ack')
                self.assertEqual(exchange.posted[0]['requested_count'], '5')
            finally:
                store.db.close()

    def test_effective_status_displays_per_asset_threshold(self):
        class NoHalt:
            def halt_reason(self): return ''
        status = runner.asset_status(NoHalt(), [], 'LIVE')
        self.assertEqual(status['BTC']['rules'][lanes.Z2]['z_base_at_5m'], 3)
        self.assertEqual(status['BTC']['rules'][lanes.Z3]['z_base_at_5m'], 3)
        for asset in ('ETH','SOL','XRP','NATGAS'):
            self.assertEqual(status[asset]['rules'][lanes.Z2]['z_base_at_5m'], 2)
        self.assertEqual(runner.CONTRACTS, 5)
        self.assertEqual(int(runner.MAX_CONTRACTS), 25)
        self.assertEqual(float(runner.MAX_RISK), 25)
        self.assertEqual(float(runner.DAILY_LOSS_BUDGET), 20)

    def test_historical_qualification_keeps_recorded_threshold(self):
        s=signal();s.update(z=2.5,required_z=2)
        self.assertTrue(operations_view.qualified(s))
        s['required_z']=3
        self.assertFalse(operations_view.qualified(s))
        del s['required_z']
        self.assertFalse(operations_view.qualified(s))
        s['z']=3
        self.assertTrue(operations_view.qualified(s))
        for invalid in (None, 'bad', float('nan'), float('inf'), 0):
            s['required_z']=invalid
            self.assertFalse(operations_view.qualified(s))


if __name__ == '__main__':
    unittest.main()
