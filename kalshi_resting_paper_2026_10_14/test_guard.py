import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import observer
from test_paper import signal_fixture, payload, trade_fixture


class AssetPauseGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.live = self.root / 'live'
        (self.live / 'state').mkdir(parents=True)
        for name in ('ARM_5CT', 'HALT_COPPER'):
            (self.live / 'state' / name).write_text(name)
        self.obj = observer.Observer.__new__(observer.Observer)
        self.obj.root = self.root
        self.obj.cfg = dict(assets=['BTC', 'USDCAD'], live_flags={
            f.name: hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (self.live / 'state').iterdir()})
        self.mock = patch.object(observer, 'LIVE', self.live)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.tmp.cleanup()

    def review_pause(self):
        p = self.live / 'state' / 'HALT_USDCAD'
        p.write_text('user pause')
        (self.root / 'reviewed_halt_flags.json').write_text(json.dumps({
            'flags': {'HALT_USDCAD': hashlib.sha256(p.read_bytes()).hexdigest()}}))

    def test_baseline_unchanged(self):
        self.assertEqual(self.obj.flag_guard(), '')
        self.assertEqual(self.obj.flag_guard('BTC'), '')

    def test_reviewed_pause_is_asset_scoped(self):
        self.review_pause()
        self.assertEqual(self.obj.flag_guard(), '')
        self.assertEqual(self.obj.flag_guard('BTC'), '')
        self.assertEqual(self.obj.flag_guard('USDCAD'), 'live_asset_halted:USDCAD')

    def test_unreviewed_pause_still_blocks(self):
        (self.live / 'state' / 'HALT_USDCAD').touch()
        self.assertEqual(self.obj.flag_guard(), 'production_flags_changed')

    def test_removed_reviewed_pause_does_not_resume(self):
        self.review_pause()
        (self.live / 'state' / 'HALT_USDCAD').unlink()
        self.assertEqual(self.obj.flag_guard(), 'production_flags_changed')

    def test_changed_arm_or_pause_blocks(self):
        self.review_pause()
        for name in ('ARM_5CT', 'HALT_COPPER', 'HALT_USDCAD'):
            p = self.live / 'state' / name
            old = p.read_bytes()
            p.write_bytes(b'changed')
            self.assertEqual(self.obj.flag_guard(), 'production_flags_changed')
            p.write_bytes(old)

    def test_manifest_cannot_authorize_arming_or_global_halt(self):
        for flag in ('ARM_OTHER', 'HALT', 'HALT_UNKNOWN', 'HALT_COPPER'):
            (self.root / 'reviewed_halt_flags.json').write_text(json.dumps({'flags': {flag: 'x'}}))
            self.assertEqual(self.obj.flag_guard(), 'invalid_reviewed_halt_manifest')

    def make_collector(self, guard):
        o = self.obj
        o.cfg.update(acceptance_delay_seconds=.25, cancel_latency_seconds=.25,
                     rest_seconds=10, variants=[dict(name='rest_3c_10s', discount='.03'),
                     dict(name='rest_1c_10s', discount='.01')],
                     paper_cash_by_route={'0': '100', '2': '100'},
                     trade_publication_grace_seconds=15, max_book_gap_seconds=4,
                     max_trade_pages=5)
        o.store = observer.Store(self.root / 'paper.sqlite')
        o.db = o.store.db
        o.healthy = True
        o.block = ''
        o.running = True
        o.fees = {'BTC': dict(valid=True, multiplier=1, fee_type='quadratic', received=1000)}
        o.flag_guard = guard
        return o

    def test_paused_asset_cannot_admit_or_fetch_book(self):
        self.review_pause()
        o = self.make_collector(self.obj.flag_guard)
        async def forbidden(*args):
            self.fail('paused asset fetched a book')
        o.get = forbidden
        asyncio.run(o.opportunity(signal_fixture(asset='USDCAD')))
        self.assertEqual(len(o.store.orders()), 3)
        self.assertTrue(all(r['state'] == 'skipped' and r['filled'] == '0'
                            and r['end_reason'] == 'live_asset_halted:USDCAD'
                            for r in o.store.orders()))
        o.db.close()

    def test_pause_arriving_during_book_does_not_fill_control(self):
        clock = [1001.0]
        paused = [False]
        o = self.make_collector(lambda asset=None: 'live_asset_halted:BTC' if paused[0] else '')
        books = [0]
        async def get(path, params=None):
            started = clock[0]
            clock[0] += .1
            if path.endswith('/orderbook'):
                books[0] += 1
                if books[0] == 2:
                    # A blocking request returns executable depth after a pause.
                    paused[0] = True
                    clock[0] += .5
                body = payload(yes=[['.9', '10']], no=[['.07', '20']])
            else:
                body = {'cursor': '', 'trades': [trade_fixture(px='.89', qty='20', t=1002)]}
            r = dict(path=path, params=params, started=started, received=clock[0],
                     status=200, body=body, error=None, headers={})
            r['id'] = o.store.receipt(r)
            return r
        async def sleep(seconds):
            clock[0] += seconds
        o.get = get
        with patch.object(observer.time, 'time', side_effect=lambda: clock[0]), \
             patch.object(observer.asyncio, 'sleep', side_effect=sleep):
            asyncio.run(o.opportunity(signal_fixture()))
        self.assertEqual(books[0], 2)
        for r in o.store.orders():
            self.assertEqual(r['filled'], '0')
            self.assertFalse(r['coverage_complete'])
            self.assertEqual(r['end_reason'], 'health_cancel:live_asset_halted:BTC')
        o.db.close()


if __name__ == '__main__':
    unittest.main()
