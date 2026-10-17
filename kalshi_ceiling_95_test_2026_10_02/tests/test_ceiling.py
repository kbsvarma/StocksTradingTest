"""Boundary, reservation, replay-parity and forward-observer checks."""
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

ROOT=Path(__file__).resolve().parents[1]
VARIANT=Path(engine.__file__).parent.name
CONFIG=json.loads((ROOT/'manifest.json').read_text())['variants']
CFG=CONFIG[VARIANT]


class CeilingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=engine.Store(Path(self.tmp.name)/'orders.sqlite3')
        self.ex=FakeExchange()
        self.bot=engine.Engine(self.ex,self.store,self.tmp.name,live=True,clock=lambda:1010)
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def test_last_signal_tick_passes_both_sides_and_keeps_hard_cap(self):
        for side in ('yes','no'):
            s=signal(side,ticker='KXBTC15M-'+side)
            ask=D(CFG['signal_ceiling'])-D('.001')
            s.update(signal_ask=float(ask),signal_bid=float(ask-D('.01')))
            cap=D(CFG['execution_cap'])
            self.ex.book=lambda _:dict(yes_dollars=[[str(1-cap),'5']],no_dollars=[[str(1-cap),'5']])
            self.assertEqual(engine.limit_price(s)[1],cap)
            self.assertEqual(self.bot.attempt(s),'ack')
            r=self.store.get(s['ticker']);r['status']='unfilled';self.store.save(r)
    def test_signal_upper_boundary_is_excluded(self):
        s=signal();s.update(signal_ask=float(CFG['signal_ceiling']),signal_bid=float(D(CFG['signal_ceiling'])-D('.01')))
        with self.assertRaisesRegex(ValueError,'signal gates'):self.bot.attempt(s)
        self.assertFalse(self.ex.posted)
    def test_execution_cap_plus_one_tick_rejects_both_sides(self):
        for side in ('yes','no'):
            s=signal(side);ask=D(CFG['signal_ceiling'])-D('.001')
            s.update(signal_ask=float(ask),signal_bid=float(ask-D('.01')))
            complement=str(1-D(CFG['execution_cap'])-D('.001'))
            self.ex.book=lambda _:dict(no_dollars=[[complement,'1000']],yes_dollars=[[complement,'1000']])
            self.assertEqual(self.bot.attempt(s),'Insufficient depth inside cap')
        self.assertFalse(self.ex.posted)
    def test_insufficient_quantity_does_not_become_assumed_fill(self):
        self.ex.book=lambda _:dict(no_dollars=[['.08','4']])
        self.assertEqual(self.bot.attempt(signal()),'Insufficient depth inside cap')
    def test_second_five_contract_position_remains_blocked(self):
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='5')]
        self.assertEqual(self.bot.attempt(signal()),'Eight-contract cap')
    def test_z_and_time_gates_remain(self):
        for updates in ({'z':1.9999},{'remaining_minutes':2},{'remaining_minutes':11}):
            s=signal();s.update(updates)
            with self.assertRaisesRegex(ValueError,'signal gates'):self.bot.attempt(s)
    def test_spread_gate_remains(self):
        s=signal();s['signal_bid']=.89
        with self.assertRaisesRegex(ValueError,'spread'):self.bot.attempt(s)
    def test_retry_is_bounded_and_never_repeats_order(self):
        self.assertTrue(retry_allowed('book_outside_limit','',1,10,False))
        self.assertFalse(retry_allowed('book_outside_limit','',8,10,False))
        self.assertFalse(retry_allowed('book_outside_limit','',1,26,False))
        self.assertFalse(retry_allowed('book_outside_limit','',1,10,True))
    def test_scanner_boundary_agrees_with_engine(self):
        top=D(CFG['signal_ceiling'])
        for ask,want in ((top-D('.001'),'paper_fill'),(top,'price_gate')):
            responses=test_signals.PaperProbeTests().responses(12000)
            responses[1]['candlesticks'][0]['yes_bid']['close_dollars']=str(ask-D('.01'))
            responses[1]['candlesticks'][0]['yes_ask']['close_dollars']=str(ask)
            with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=12003):
                self.assertEqual(market_data.scan('BTC',12000,set())['status'],want)
    def test_missing_reference_and_nonreference_history_reject(self):
        for missing in (12000-540,12000-1800):
            responses=test_signals.PaperProbeTests().responses(12000)
            responses[2]=[r for r in responses[2] if r[0]!=missing]
            with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=12003):
                self.assertEqual(market_data.scan('BTC',12000,set())['status'],'incomplete_spot')
    def test_risk_reserve_covers_maximum_price_and_fee(self):
        cap=D(CFG['execution_cap']);reserve=D(CFG['risk_per_contract'])
        for count in (1,2,5,8):
            cost=count*cap;fee=D('.07')*count*cap*(1-cap)
            self.assertLessEqual(cost+fee,count*reserve)
        if VARIANT=='ceiling95':self.assertGreater(D('.95')+D('.07')*D('.95')*D('.05'),D('.95'))
    def test_daily_budget_uses_fee_covered_reserve(self):
        self.store.save(dict(ticker='old',status='settled',pnl='-5.22',settled_day=engine.today()))
        want='Daily loss budget including open risk' if VARIANT=='ceiling95' else 'ack'
        self.assertEqual(self.bot.attempt(signal()),want)
    def test_exposure_budget_uses_fee_covered_reserve(self):
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='6')]
        with patch.object(engine,'CONTRACTS',2):
            self.assertEqual(self.bot.attempt(signal()),'Exposure cap' if VARIANT=='ceiling95' else 'ack')
    def test_observer_depth_respects_cap_on_both_sides(self):
        import shadow
        for side in ('yes','no'):
            s=signal(side);s.update(signal_ask=.949,signal_bid=.939,book_observed_ts=1010,
                                   book_levels=[['.05','5']])
            r=shadow.depth_for_variant(s,'ceiling95')
            self.assertTrue(r['enough']);self.assertEqual(D(r['cap']),D('.95'))
            self.assertFalse(shadow.depth_for_variant(s,'signal95')['enough'])
            s['book_levels']=[['.049','500']]
            self.assertFalse(shadow.depth_for_variant(s,'ceiling95')['enough'])
    def test_replay_reproduces_previous_93_and_935_runs(self):
        new=json.loads((ROOT/'results.json').read_text())
        old=json.loads((ROOT.parent/'kalshi_ceiling_935_test_2026_10_02/results.json').read_text())
        def key(r):
            return (r['dataset'],r['mode'],r['contracts'],r['extra_settlement_delay'],r['charge_at_cap'],r['fee_precision'])
        prior={key(p['baseline']):p for p in old['pairs']}
        for c in new['cases']:
            b=c['runs']['reference']
            if b['minimum_book_age']!=2:continue
            for v,oldkey in (('reference','baseline'),('ceiling935','candidate')):
                actual=c['runs'][v];expected=prior[key(b)][oldkey]
                for field in ('trades','wins','losses'):self.assertEqual(actual[field],expected[field])
                self.assertAlmostEqual(actual['pnl'],expected['pnl'],places=8)
                self.assertEqual({r['ticker'] for r in actual['rows']},{r['ticker'] for r in expected['rows']})


if __name__=='__main__':unittest.main()
