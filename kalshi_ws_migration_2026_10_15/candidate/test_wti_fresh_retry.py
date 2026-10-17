"""Offline WTI minute-rollover, exchange-proof and duplicate-writer regression tests."""
import copy
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import engine
from engine import Engine,Store
from execution_retry import fresh_retry_candidate
from test_engine import signal
from test_zero_fill import Exchange
import runner

class FreshWTI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'ARM_WTI_5CT').write_text('5');(self.root/'ARM_Z3_5CT').write_text('5')
        self.store=Store(self.root/'state.db');self.ex=Exchange();self.now=1023
        self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.s=dict(signal(ticker='KXWTI15M-TEST'),asset='WTI',lane='cushion_z2',
                    decision_ts=1020,close_ts=1620,remaining_minutes=10,z=1.6)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def zero(self):
        self.assertEqual(self.e.attempt(copy.deepcopy(self.s)),'ack')
        self.assertEqual(self.e.reconcile(),[])
        return self.store.get(self.s['ticker'])
    def advance(self,minute=1080,**changes):
        self.now=minute+3
        return dict(self.s,decision_ts=minute,remaining_minutes=(self.s['close_ts']-minute)/60,**changes)
    def test_later_minute_requalifies_and_retains_old_attempt(self):
        original=self.zero();s=self.advance(signal_ask=.925,signal_bid=.915,z=1.9)
        self.assertNotIn(s['ticker'],self.e.entered_markets('cushion_z2',1080))
        self.assertIn(s['ticker'],self.e.entered_markets('premium_z3',1080))
        self.assertIsNone(self.e.retry_signal('WTI','cushion_z2',1080))
        self.ex.outcomes=['5'];self.assertEqual(self.e.attempt(s),'ack');self.e.reconcile()
        rows=self.store.attempts(s['ticker']);self.assertEqual(rows[0],original)
        self.assertEqual([r['attempt_number'] for r in rows],[1,2])
        self.assertEqual([r['status'] for r in rows],['unfilled','open'])
        self.assertEqual(rows[1]['signal']['decision_ts'],1080)
        self.assertEqual(D(rows[1]['favorite_limit']),D('.935'));self.assertEqual(rows[1]['filled'],'5')
        self.assertEqual(self.e.attempt(self.advance(1140)),'Already attempted this market')
    def test_three_submission_limit_survives_minutes_and_restart(self):
        self.zero()
        for minute in (1080,1140):
            self.assertEqual(self.e.attempt(self.advance(minute)),'ack');self.e.reconcile()
        self.store.db.close();self.store=Store(self.root/'state.db')
        self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.assertEqual(self.e.attempt(self.advance(1200)),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),3)
    def test_fresh_inputs_must_pass_all_signal_gates(self):
        self.zero()
        for changes in ({'z':1.49},{'signal_ask':.95},{'signal_ask':.84,'signal_bid':.83},
                        {'signal_bid':.89}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.e.attempt(self.advance(**changes))
        s=self.advance(1500)
        with self.assertRaises(ValueError):self.e.attempt(s)
        self.assertEqual(len(self.ex.posted),1)
    def test_side_lane_route_expiry_ladder_and_minute_identity_cannot_change(self):
        self.zero();s=self.advance()
        for changes in ({'side':'no'},{'lane':'premium_z3','signal_ask':.96,'signal_bid':.95,'z':3},
                        {'exchange_index':0},{'close_ts':1680},{'price_ranges':[]},
                        {'decision_ts':1081},{'remaining_minutes':8}):
            with self.subTest(changes=changes):
                self.assertEqual(self.e.attempt(dict(s,**changes)),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_no_side_and_premium_keep_their_price_caps(self):
        self.s.update(side='no',lane='premium_z3',signal_ask=.974,signal_bid=.964,z=2.7)
        self.ex.book=lambda _:dict(yes_dollars=[['.025','5']])
        self.zero();self.ex.outcomes=['5'];self.assertEqual(self.e.attempt(self.advance(z=2.8)),'ack')
        row=self.ex.posted[-1]
        self.assertEqual(D(row['favorite_limit']),D('.975'));self.assertEqual(row['yes_limit'],'0.0250')
        self.assertEqual(row['requested_count'],'5')
    def test_standard_cap_and_full_depth_are_unchanged(self):
        self.zero();s=self.advance(signal_ask=.938,signal_bid=.928)
        for book in ([['.06','50']],[['.061','4.99']]):
            self.ex.book=lambda _,b=book:dict(no_dollars=b)
            self.assertEqual(self.e.attempt(copy.deepcopy(s)),'Insufficient depth inside cap')
        self.ex.book=lambda _:dict(no_dollars=[['.061','5']])
        self.assertEqual(self.e.attempt(s),'ack');self.assertEqual(D(self.ex.posted[-1]['favorite_limit']),D('.939'))
    def test_partial_or_ambiguous_or_unproven_never_reopens(self):
        row=self.zero();s=self.advance()
        variants=[dict(row,filled='1'),dict(row,ever_filled='.01'),dict(row,status='ack'),
                  dict(row,retry_forbidden='Ambiguous'),dict(row,zero_fill_confirmation={}),
                  dict(row,ioc_retry_version=None),dict(row,requested_count='1')]
        for changed in variants:
            self.store.save(changed)
            self.assertEqual(self.e.attempt(copy.deepcopy(s)),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_every_prior_exchange_attempt_is_reconfirmed(self):
        self.zero();self.e.attempt(self.advance());self.e.reconcile();s=self.advance(1140)
        self.ex.order_map['oid1']['fill_count_fp']='1'
        with self.assertRaisesRegex(RuntimeError,'not confirmed'):self.e.attempt(s)
        self.ex.order_map['oid1']['fill_count_fp']='0';self.ex.fill_map['oid1']=[{'fill_id':'late'}]
        with self.assertRaisesRegex(RuntimeError,'has fills'):self.e.attempt(s)
        self.ex.fill_map['oid1']=[];self.ex.order_map['oid1']['taker_fees_dollars']='.01'
        with self.assertRaisesRegex(RuntimeError,'charged cash'):self.e.attempt(s)
        self.assertEqual(len(self.ex.posted),2)
    def test_missing_history_foreign_orders_and_failed_get_block(self):
        self.zero();s=self.advance();old=self.ex.orders
        self.ex.orders=lambda **kw:[]
        with self.assertRaisesRegex(RuntimeError,'missing'):self.e.attempt(s)
        self.ex.orders=lambda **kw:old(**kw)+[{'client_order_id':'foreign'}]
        with self.assertRaisesRegex(RuntimeError,'Unexpected'):self.e.attempt(s)
        self.ex.orders=old;self.ex.order=lambda _:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):self.e.attempt(s)
        self.store.db.execute('delete from order_attempts');self.store.db.commit()
        with self.assertRaisesRegex(RuntimeError,'Incomplete'):self.e.attempt(s)
        self.assertEqual(len(self.ex.posted),1)
    def test_risk_cash_positions_and_halts_are_rechecked(self):
        self.zero();s=self.advance();self.ex.cash=D(1)
        self.assertIn('funding',self.e.attempt(s));self.ex.cash=D(50)
        self.ex.pos=[dict(ticker=s['ticker'],position_fp='1')]
        self.assertEqual(self.e.attempt(s),'Asset already has exposure');self.ex.pos=[]
        self.ex.rest=[{'ticker':s['ticker']}]
        self.assertEqual(self.e.attempt(s),'Crypto resting orders exist');self.ex.rest=[]
        for name in ('HALT','HALT_WTI'):
            (self.root/name).touch();self.assertIn('halt',self.e.attempt(s).lower());(self.root/name).unlink()
        (self.root/'ARM_WTI_5CT').unlink();self.assertIn('not activated',self.e.attempt(s))
        (self.root/'ARM_WTI_5CT').write_text('5')
        self.store.save(dict(ticker='loss',status='settled',pnl='-16',settled_day=engine.today()))
        self.assertEqual(self.e.attempt(s),'Daily loss budget including open risk')
        self.assertEqual(len(self.ex.posted),1)
    def test_shared_contract_and_exposure_caps_still_block(self):
        self.zero();s=self.advance()
        self.ex.pos=[dict(ticker='KXBTC15M-X',position_fp='25')]
        self.assertEqual(self.e.attempt(s),'25-contract cap')
        self.ex.pos=[dict(ticker='KXBTC15M-X',position_fp='21')]
        self.assertEqual(self.e.attempt(s),'25-contract cap')
        self.ex.pos=[dict(ticker='KXBTC15M-X',position_fp='20')]
        with patch.object(engine,'MAX_RISK',D(24)):
            self.assertEqual(self.e.attempt(s),'Exposure cap')
        self.assertEqual(len(self.ex.posted),1)
    def test_new_signal_deadline_applies_during_book_and_commit(self):
        self.zero();s=self.advance();self.now=1105.01
        self.assertEqual(self.e.attempt(s),'Already attempted this market')
        self.now=1083;old=self.ex.book
        def slow_book(t):self.now=1105.01;return old(t)
        self.ex.book=slow_book
        self.assertEqual(self.e.attempt(s),'Zero-fill retry deadline passed')
        self.ex.book=old;self.now=1083;reserve=self.store.reserve
        def slow_commit(*a,**kw):reserve(*a,**kw);self.now=1106
        self.store.reserve=slow_commit
        self.assertEqual(self.e.attempt(s),'Signal expired before submission')
        self.assertEqual(len(self.ex.posted),1)
    def test_fresh_reservation_is_atomic_and_prior_record_cannot_overwrite(self):
        row=self.zero();s=self.advance()
        new=dict(row,signal=s,client_id='fresh2',created_ts=self.now,attempt_number=2,status='intent')
        other=Store(self.root/'state.db')
        try:
            self.store.reserve(new,previous=row)
            with self.assertRaises(RuntimeError):other.reserve(dict(new,client_id='duplicate'),previous=row)
            with self.assertRaises(RuntimeError):other.save(row)
            self.assertEqual(len(self.store.attempts(s['ticker'])),2)
        finally:other.db.close()
    def test_other_assets_and_closed_wti_do_not_reopen(self):
        row=self.zero()
        for asset in ('BTC','ETH','SOL','XRP','NATGAS','EURUSD','COPPER'):
            other=dict(row,asset=asset,ticker='KX'+asset+'15M-TEST')
            self.assertFalse(fresh_retry_candidate(other,1080,1083,'cushion_z2'))
        self.assertFalse(fresh_retry_candidate(row,1620,1623,'cushion_z2'))
        self.assertFalse(fresh_retry_candidate(row,1080,1079,'cushion_z2'))
    def run_rollover(self,qualifies=True,depth_reappears=False):
        root=self.root/'runner';root.mkdir()
        for name in ('ARM_5CT','ARM_Z3_5CT','ARM_WTI_5CT'):(root/name).write_text('5')
        clock={'t':1023.0};scans=[];refresh=[];self.ex.outcomes=['0','5']
        def scan(asset,T,entered,lane):
            if lane!='cushion_z2':return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
            scans.append((T,set(entered)))
            if self.s['ticker'] in entered:return dict(self.s,decision_ts=T,status='already_entered')
            r=dict(self.s,decision_ts=T,remaining_minutes=(1620-T)/60)
            if T>1020:r.update(signal_ask=.925,signal_bid=.915,z=1.9)
            if T>1020 and not qualifies:r.update(status='cushion_gate',z=1)
            if T>1020 and depth_reappears:r['status']='book_outside_limit'
            return r
        def refresh_book(s):
            refresh.append(s['decision_ts'])
            return dict(s,status='book_outside_limit' if s['decision_ts']==1020 else 'paper_fill')
        def sleep(_):
            clock['t']+=1.2
            if clock['t']>1088:raise KeyboardInterrupt()
        with patch.object(runner,'STATE',root),patch.object(runner,'legacy_safe',return_value=''),\
             patch.object(runner.Credentials,'load',return_value=None),patch.object(runner,'make_stream',return_value=None),patch.object(runner,'KalshiClientV2'),\
             patch.object(runner,'Exchange',return_value=self.ex),\
             patch.object(runner,'Engine',side_effect=lambda *a,**k:Engine(*a,clock=lambda:clock['t'],**k)),\
             patch.object(runner.market_data,'ASSETS',('WTI',)),patch.object(runner.market_data,'warm_sources',return_value={}),\
             patch.object(runner.market_data,'scan',side_effect=scan),\
             patch.object(runner.market_data,'refresh_book',side_effect=refresh_book),\
             patch.object(runner.market_data,'get'),patch.object(runner.time,'time',side_effect=lambda:clock['t']),\
             patch.object(runner.time,'sleep',side_effect=sleep),patch.object(runner.signal,'signal'),\
             patch('sys.argv',['runner.py','--live']):
            with self.assertRaises(KeyboardInterrupt):runner.main()
        self.assertTrue(any(t==1080 and self.s['ticker'] not in entered for t,entered in scans))
        self.assertEqual(len(self.ex.posted),2 if qualifies else 1)
        if qualifies:
            self.assertEqual(self.ex.posted[-1]['signal']['decision_ts'],1080)
            self.assertEqual(self.ex.posted[-1]['signal']['signal_ask'],.925)
            self.assertEqual(self.ex.posted[-1]['requested_count'],'5')
        if depth_reappears:self.assertIn(1080,refresh)
        d=json.loads((root/'status.json').read_text());self.assertEqual(d['wti_fresh_retry_version'],1)
    def test_runner_requalifies_after_minute_rollover(self):self.run_rollover()
    def test_runner_does_not_reuse_old_signal_when_new_z_fails(self):self.run_rollover(qualifies=False)
    def test_runner_keeps_checking_new_minute_book_without_reusing_old_signal(self):self.run_rollover(depth_reappears=True)

if __name__=='__main__':unittest.main()
