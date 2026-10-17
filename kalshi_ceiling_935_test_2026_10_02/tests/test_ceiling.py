import copy
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
import market_data
from execution_retry import retry_allowed
from test_engine import FakeExchange, signal
import test_signals


class CeilingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=engine.Store(Path(self.tmp.name)/'orders.sqlite3')
        self.ex=FakeExchange()
        self.bot=engine.Engine(self.ex,self.store,self.tmp.name,live=True,clock=lambda:1010)
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def test_new_band_passes_both_sides_and_keeps_hard_cap(self):
        for side in ('yes','no'):
            s=signal(side,ticker='KXBTC15M-'+side)
            s.update(signal_ask=.934,signal_bid=.924)
            self.ex.book=lambda _:dict(yes_dollars=[['.061','5']],no_dollars=[['.061','5']])
            self.assertEqual(engine.limit_price(s)[1],D('.939'))
            self.assertEqual(self.bot.attempt(s),'ack')
            r=self.store.get(s['ticker']);r['status']='unfilled';self.store.save(r)
    def test_935_boundary_is_excluded(self):
        s=signal();s.update(signal_ask=.935,signal_bid=.925)
        with self.assertRaisesRegex(ValueError,'signal gates'):self.bot.attempt(s)
        self.assertFalse(self.ex.posted)
    def test_book_above_939_still_fails_new_band(self):
        s=signal();s.update(signal_ask=.934,signal_bid=.924)
        self.ex.book=lambda _:dict(no_dollars=[['.060','1000']])
        self.assertEqual(self.bot.attempt(s),'Insufficient depth inside cap')
        self.assertFalse(self.ex.posted)
    def test_insufficient_quantity_does_not_become_assumed_fill(self):
        self.ex.book=lambda _:dict(no_dollars=[['.08','4']])
        self.assertEqual(self.bot.attempt(signal()),'Insufficient depth inside cap')
    def test_second_five_contract_position_remains_blocked(self):
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='5')]
        self.assertEqual(self.bot.attempt(signal()),'Eight-contract cap')
    def test_unchanged_z_and_time_gates(self):
        for updates in ({'z':1.9999},{'remaining_minutes':2},{'remaining_minutes':11}):
            s=signal();s.update(updates)
            with self.assertRaisesRegex(ValueError,'signal gates'):self.bot.attempt(s)
    def test_retry_is_bounded_and_never_repeats_order(self):
        self.assertTrue(retry_allowed('book_outside_limit','',1,10,False))
        self.assertFalse(retry_allowed('book_outside_limit','',8,10,False))
        self.assertFalse(retry_allowed('book_outside_limit','',1,26,False))
        self.assertFalse(retry_allowed('book_outside_limit','',1,10,True))
    def test_scanner_new_band_and_upper_boundary(self):
        for ask,want in (('.930','paper_fill'),('.934','paper_fill'),('.935','price_gate')):
            responses=test_signals.PaperProbeTests().responses(12000)
            responses[1]['candlesticks'][0]['yes_bid']['close_dollars']=str(D(ask)-D('.01'))
            responses[1]['candlesticks'][0]['yes_ask']['close_dollars']=ask
            with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=12003):
                self.assertEqual(market_data.scan('BTC',12000,set())['status'],want)
    def test_missing_preopen_reference_rejects(self):
        responses=test_signals.PaperProbeTests().responses(12000)
        responses[2]=[r for r in responses[2] if r[0]!=12000-540]
        with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=12003):
            self.assertEqual(market_data.scan('BTC',12000,set())['status'],'incomplete_spot')
    def test_replay_matches_preexisting_august_trade_set(self):
        p=Path(__file__).resolve().parents[1]
        new=json.loads((p/'results.json').read_text())
        old=json.loads((p.parent/'kalshi_current_on_august_2026_09_28/results.json').read_text())
        pair=next(p for p in new['pairs'] if p['baseline']['dataset']=='august' and
                  p['baseline']['mode']=='book' and p['baseline']['contracts']==2 and not p['baseline']['charge_at_cap'])
        self.assertEqual({r['ticker'] for r in pair['baseline']['rows']},
                         {r['ticker'] for r in old['scenarios'][0]['rows']})
        pair=next(p for p in new['pairs'] if p['baseline']['dataset']=='august' and
                  p['baseline']['mode']=='next_open' and p['baseline']['contracts']==2 and not p['baseline']['charge_at_cap'])
        self.assertEqual(pair['baseline']['trades'],old['scenarios'][1]['trades'])
        self.assertAlmostEqual(pair['baseline']['pnl'],old['scenarios'][1]['pnl'],places=8)


if __name__=='__main__':unittest.main()
