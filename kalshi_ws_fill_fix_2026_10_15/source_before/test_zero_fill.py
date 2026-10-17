"""Exchange-state and crash tests. No network or real orders."""
import copy
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from engine import Engine,Store
from execution_retry import retry_candidate,retry_allowed,ZERO_FILL_RETRY
from test_engine import FakeExchange,signal
import runner
import dashboard_panel

class Exchange(FakeExchange):
    def __init__(self):
        super().__init__();self.order_map={};self.fill_map={};self.outcomes=[];self.gets=[]
    def submit(self,r):
        self.posted.append(copy.deepcopy(r));oid='oid'+str(len(self.posted))
        count=self.outcomes.pop(0) if self.outcomes else '0'
        self.order_map[oid]=dict(order_id=oid,ticker=r['ticker'],client_order_id=r['client_id'],
            status='canceled' if D(count)<5 else 'executed',initial_count_fp='5.00',
            fill_count_fp=count,remaining_count_fp='0.00',maker_fill_cost_dollars='0',
            taker_fill_cost_dollars=str(D(count)*D(r['favorite_limit'])),maker_fees_dollars='0',
            taker_fees_dollars='0' if D(count)==0 else '.02')
        self.fill_map[oid]=[] if D(count)==0 else [dict(fill_id='fill'+oid,order_id=oid,ticker=r['ticker'],
            count_fp=count,yes_price_dollars=r['yes_limit'],fee_cost='.02')]
        if self.raise_post:raise TimeoutError('response lost after exchange acceptance')
        return dict(order_id=oid)
    def orders(self,**kwargs):
        self.gets.append(('orders',kwargs))
        if kwargs.get('status')=='resting':return self.rest
        return [r for r in self.order_map.values() if not kwargs.get('ticker') or r['ticker']==kwargs['ticker']]
    def order(self,oid):self.gets.append(('order',oid));return copy.deepcopy(self.order_map[oid])
    def fills(self,oid):self.gets.append(('fills',oid));return copy.deepcopy(self.fill_map[oid])

class ZeroFill(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1003
        self.store=Store(self.root/'state.db');self.ex=Exchange()
        self.engine=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.s=signal()
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def zero(self):
        self.assertEqual(self.engine.attempt(copy.deepcopy(self.s)),'ack')
        self.assertEqual(self.engine.reconcile(),[])
        row=self.store.get(self.s['ticker']);self.assertEqual(row['status'],'unfilled')
        return row
    def retry(self):self.now+=2;return self.engine.attempt(copy.deepcopy(self.s))
    def test_zero_then_five_filled_preserves_both_attempts_and_one_position(self):
        self.zero();self.ex.outcomes=['5'];self.assertEqual(self.retry(),'ack');self.engine.reconcile()
        rows=self.store.attempts(self.s['ticker'])
        self.assertEqual([r['status'] for r in rows],['unfilled','open'])
        self.assertEqual([r['attempt_number'] for r in rows],[1,2])
        self.assertEqual(len({r['client_id'] for r in rows}),2)
        self.assertEqual(len(self.store.all()),1);self.assertEqual(self.store.all()[0]['filled'],'5')
        self.assertEqual(self.retry(),'Already attempted this market');self.assertEqual(len(self.ex.posted),2)
    def test_max_three_submissions_is_durable_after_restart(self):
        self.zero()
        for _ in range(2):self.assertEqual(self.retry(),'ack');self.engine.reconcile()
        self.store.db.close();self.store=Store(self.root/'state.db')
        self.engine=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.assertEqual(self.retry(),'Already attempted this market');self.assertEqual(len(self.ex.posted),3)
    def test_restart_recovers_frozen_retry_signal(self):
        self.zero();self.store.db.close();self.store=Store(self.root/'state.db')
        self.engine=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        s=self.engine.retry_signal('BTC','cushion_z2',1000)
        self.assertEqual(s['decision_ts'],1000);self.assertEqual(s['signal_ask'],.92)
        self.assertEqual(self.retry(),'ack')
        self.assertIsNone(self.engine.retry_signal('BTC','cushion_z2',1060))
    def test_partial_fill_never_retries(self):
        self.ex.outcomes=['.01'];self.engine.attempt(self.s);self.engine.reconcile()
        self.assertEqual(self.retry(),'Already attempted this market');self.assertEqual(len(self.ex.posted),1)
    def test_ambiguous_post_never_retries_even_if_later_found_canceled_zero(self):
        self.ex.raise_post=True;self.assertEqual(self.engine.attempt(self.s),'unknown')
        self.ex.raise_post=False;self.assertEqual(self.engine.reconcile(),[])
        self.assertEqual(self.store.get(self.s['ticker'])['status'],'unfilled')
        self.assertEqual(self.retry(),'Already attempted this market');self.assertEqual(len(self.ex.posted),1)
    def test_lost_ack_durable_intent_does_not_submit_on_restart(self):
        self.engine.attempt(self.s);r=self.store.get(self.s['ticker']);r['status']='intent';r.pop('order_id');self.store.save(r)
        restarted=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.assertEqual(restarted.attempt(self.s),'Already attempted this market')
        restarted.reconcile()
        self.assertEqual(restarted.attempt(self.s),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_executed_status_zero_is_not_retryable(self):
        self.engine.attempt(self.s);self.ex.order_map['oid1']['status']='executed';self.engine.reconcile()
        self.assertEqual(self.retry(),'Already attempted this market')
    def test_unexpected_resting_order_stays_nonretryable(self):
        self.engine.attempt(self.s);self.ex.order_map['oid1']['status']='resting';self.engine.reconcile()
        self.ex.order_map['oid1']['status']='canceled';self.engine.reconcile()
        self.assertEqual(self.retry(),'Already attempted this market');self.assertEqual(self.ex.cancelled,['oid1'])
    def test_cancel_timeout_cannot_reopen_unexpected_resting_order(self):
        self.engine.attempt(self.s);self.ex.order_map['oid1']['status']='resting'
        def cancel(oid):
            self.ex.order_map[oid]['status']='canceled';raise TimeoutError('lost cancel response')
        self.ex.cancel=cancel;self.assertTrue(self.engine.reconcile());self.engine.reconcile()
        self.assertEqual(self.retry(),'Already attempted this market')
    def test_observed_fill_is_durable_when_fills_get_fails(self):
        self.ex.outcomes=['1'];self.engine.attempt(self.s);original=self.ex.fills
        self.ex.fills=lambda oid:(_ for _ in ()).throw(TimeoutError())
        self.assertTrue(self.engine.reconcile())
        self.assertEqual(D(self.store.get(self.s['ticker'])['ever_filled']),1)
        self.ex.order_map['oid1']['fill_count_fp']='0';self.ex.fill_map['oid1']=[];self.ex.fills=original
        self.engine.reconcile();self.assertEqual(self.retry(),'Already attempted this market')
    def test_fill_index_lag_is_not_zero_confirmation(self):
        self.ex.outcomes=['1'];self.engine.attempt(self.s);self.ex.fill_map['oid1']=[];self.engine.reconcile()
        self.assertEqual(self.store.get(self.s['ticker'])['status'],'accounting_pending')
        self.assertEqual(self.retry(),'Already attempted this market')
    def test_remaining_quantity_on_terminal_order_blocks(self):
        self.engine.attempt(self.s);self.ex.order_map['oid1']['remaining_count_fp']='1'
        self.assertTrue(self.engine.reconcile());self.assertEqual(self.retry(),'Already attempted this market')
    def test_live_new_fill_after_local_zero_is_detected_before_retry(self):
        self.zero();self.ex.order_map['oid1']['fill_count_fp']='1'
        with self.assertRaisesRegex(RuntimeError,'not confirmed'):self.retry()
        self.assertEqual(len(self.ex.posted),1)
    def test_fill_api_row_even_with_zero_order_count_blocks(self):
        self.zero();self.ex.fill_map['oid1']=[{'fill_id':'unexpected'}]
        with self.assertRaisesRegex(RuntimeError,'has fills'):self.retry()
        self.assertEqual(len(self.ex.posted),1)
    def test_each_prior_attempt_is_rechecked_not_only_latest(self):
        self.zero();self.retry();self.engine.reconcile();self.ex.order_map['oid1']['fill_count_fp']='1'
        with self.assertRaisesRegex(RuntimeError,'not confirmed'):self.retry()
        self.assertEqual(len(self.ex.posted),2)
    def test_unknown_or_unindexed_market_order_blocks_retry(self):
        self.zero();self.ex.order_map['foreign']=dict(self.ex.order_map['oid1'],client_order_id='foreign')
        with self.assertRaisesRegex(RuntimeError,'Unexpected'):self.retry()
        del self.ex.order_map['foreign'];del self.ex.order_map['oid1']
        with self.assertRaisesRegex(RuntimeError,'missing'):self.retry()
    def test_nonzero_fees_and_missing_cost_fields_fail_closed(self):
        self.zero();self.ex.order_map['oid1']['taker_fees_dollars']='.01'
        with self.assertRaisesRegex(RuntimeError,'charged cash'):self.retry()
        self.ex.order_map['oid1']['taker_fees_dollars']='0';del self.ex.order_map['oid1']['maker_fill_cost_dollars']
        with self.assertRaisesRegex(ValueError,'Missing'):self.retry()
    def test_failed_confirmation_get_cannot_submit(self):
        self.zero();self.ex.order=lambda oid:(_ for _ in ()).throw(TimeoutError('GET unavailable'))
        with self.assertRaises(TimeoutError):self.retry()
        self.assertEqual(len(self.ex.posted),1)
    def test_changed_side_lane_minute_or_cap_cannot_retry(self):
        self.zero();self.now+=2
        for changes in ({'side':'no'},{'lane':'premium_z3','signal_ask':.94,'signal_bid':.93,'z':3.1},
                        {'decision_ts':1060},{'signal_ask':.925},{'close_ts':1360}):
            with self.subTest(changes=changes):
                s=dict(self.s,**changes)
                if s.get('lane')=='premium_z3':(self.root/'ARM_Z3_5CT').write_text('5')
                self.assertEqual(self.engine.attempt(s),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_cooldown_does_not_reserve_a_new_intent(self):
        self.zero();self.now+=.5
        self.assertEqual(self.engine.attempt(self.s),'Zero-fill retry cooldown');self.assertEqual(len(self.ex.posted),1)
    def test_deadline_at_dispatch_and_after_account_book_io(self):
        self.zero();self.now=1025.001;self.assertEqual(self.engine.attempt(self.s),'Already attempted this market')
        self.now=1024
        original=self.ex.book
        def slow_book(t):self.now=1025.1;return original(t)
        self.ex.book=slow_book
        self.assertEqual(self.engine.attempt(self.s),'Zero-fill retry deadline passed');self.assertEqual(len(self.ex.posted),1)
    def test_slow_intent_commit_cannot_send_a_late_retry(self):
        self.zero();original=self.store.reserve
        def slow(*args,**kwargs):original(*args,**kwargs);self.now=1026
        self.store.reserve=slow
        self.assertEqual(self.retry(),'Signal expired before submission')
        self.assertEqual(len(self.ex.posted),1);self.assertEqual(self.store.get(self.s['ticker'])['status'],'not_submitted')
    def test_cash_halt_position_risk_and_depth_still_apply(self):
        self.zero();self.now+=2
        self.ex.cash=D('1');self.assertIn('funding',self.engine.attempt(self.s))
        self.ex.cash=D('50');self.engine.account();(self.root/'HALT').touch()
        self.assertEqual(self.engine.attempt(self.s),'Manual HALT');(self.root/'HALT').unlink()
        self.ex.pos=[dict(ticker=self.s['ticker'],position_fp='1')]
        self.assertEqual(self.engine.attempt(self.s),'Asset already has exposure');self.ex.pos=[]
        self.ex.book=lambda _:dict(no_dollars=[['.04','100']])
        self.assertEqual(self.engine.attempt(self.s),'Insufficient depth inside cap')
        self.ex.book=lambda _:dict(no_dollars=[['.30','100']])
        self.assertEqual(self.engine.attempt(self.s),'Book weakened below floor');self.assertEqual(len(self.ex.posted),1)
    def test_other_lane_cannot_steal_retry_and_both_lanes_support_zero_retry(self):
        self.zero();self.assertIsNone(self.engine.retry_signal('BTC','premium_z3',1000))
        self.s=dict(signal(ticker='KXETH15M-TEST'),asset='ETH',lane='premium_z3',signal_ask=.95,signal_bid=.94,z=3.2)
        (self.root/'ARM_Z3_5CT').write_text('5');self.ex.book=lambda _:dict(no_dollars=[['.045','5']])
        self.zero();self.ex.outcomes=['5'];self.assertEqual(self.retry(),'ack');self.engine.reconcile()
        r=self.store.get(self.s['ticker']);self.assertEqual(r['lane'],'premium_z3');self.assertEqual(r['requested_count'],'5')
        self.assertLessEqual(D(r['favorite_limit']),D('.975'))
    def test_no_side_retry_preserves_complement_price(self):
        self.s=signal('no');self.zero();self.ex.outcomes=['5'];self.retry();self.engine.reconcile()
        r=self.store.get(self.s['ticker']);self.assertEqual(r['yes_limit'],'0.0700');self.assertEqual(D(r['entry']),D('.93'))
    def test_migration_preserves_old_rows_and_never_reopens_old_unfilled(self):
        row=self.zero();old=dict(row);old.pop('ioc_retry_version');old.pop('attempt_number');old.pop('zero_fill_confirmation')
        self.store.save(old);raw=self.store.db.execute('select record from orders').fetchone()[0]
        self.store.db.close();self.store=Store(self.root/'state.db')
        self.assertEqual(self.store.db.execute('select record from orders').fetchone()[0],raw)
        self.engine=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.assertEqual(self.retry(),'Already attempted this market')
    def test_reservation_is_atomic_against_stale_second_writer(self):
        previous=self.zero();self.now+=2
        r=dict(previous,client_id='retry2',attempt_number=2,status='intent',created_ts=self.now)
        other=Store(self.root/'state.db')
        try:
            self.store.reserve(r,previous=previous)
            with self.assertRaises(RuntimeError):other.reserve(dict(r,client_id='duplicate2'),previous=previous)
            with self.assertRaises(RuntimeError):other.save(previous)
            self.assertEqual(len(self.store.attempts(self.s['ticker'])),2)
        finally:other.db.close()
    def test_summary_counts_submissions_without_double_pnl(self):
        self.zero();self.ex.outcomes=['5'];self.retry();self.now=1400;self.engine.reconcile()
        rows=self.store.all();totals=dashboard_panel.ledger_summary(rows)
        self.assertEqual(totals['attempts'],2);self.assertEqual(totals['unfilled'],1)
        self.assertEqual(totals['settled'],1);self.assertEqual(totals['wins'],1)
        self.assertEqual(totals['net'],D(rows[0]['pnl']))
    def test_actual_eth_failure_and_later_book_can_reach_a_second_submission(self):
        p=Path(__file__).resolve().parents[1]/'evidence'
        actual=json.loads((p/'snapshot.json').read_text())['orders'][0]
        self.s=copy.deepcopy(actual['signal']);self.now=actual['created_ts']
        self.ex.book=lambda _:dict(no_dollars=actual['signal']['execution_book_levels'])
        self.zero()
        later=next(r for r in json.loads((p/'observer.json').read_text())['observer'] if r['depth']['5']['enough'])
        self.now=later['observed_ts'];self.ex.book=lambda _:dict(no_dollars=later['levels'])
        self.ex.outcomes=['5'];self.assertEqual(self.engine.attempt(self.s),'ack');self.engine.reconcile()
        row=self.store.get(self.s['ticker'])
        self.assertEqual(row['attempt_number'],2);self.assertEqual(row['favorite_limit'],'0.9390')
        self.assertEqual(row['filled'],'5');self.assertEqual(len(self.ex.posted),2)
    def run_zero_runner(self,delayed=False):
        root=self.root/'runner_state';root.mkdir();(root/'ARM_5CT').write_text('5');(root/'ARM_Z3_5CT').write_text('5')
        self.ex.outcomes=['0','5'];clock={'t':1003.0}
        if delayed:
            original=self.ex.order;lookups={'n':0}
            def order(oid):
                lookups['n']+=1
                if lookups['n']==1:raise RuntimeError('404 first acknowledgment not indexed')
                return original(oid)
            self.ex.order=order
        def scan(asset,T,entered,lane):
            if asset=='BTC' and lane=='cushion_z2':return signal()
            return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
        # Use a real minute boundary for the runner's scheduler.
        def fixed_signal(asset,T,entered,lane):
            r=scan(asset,T,entered,lane)
            if r.get('status')=='paper_fill':r.update(decision_ts=T,close_ts=T+300)
            return r
        clock['t']=1023.0
        def sleep(_):
            clock['t']+=1.2
            if clock['t']>1029:raise KeyboardInterrupt()
        with patch.object(runner,'STATE',root),patch.object(runner,'legacy_safe',return_value=''),\
             patch.object(runner.Credentials,'load',return_value=None),patch.object(runner,'make_stream',return_value=None),patch.object(runner,'KalshiClientV2'),\
             patch.object(runner,'Exchange',return_value=self.ex),\
             patch.object(runner,'Engine',side_effect=lambda *a,**k:Engine(*a,clock=lambda:clock['t'],**k)),\
             patch.object(runner.market_data,'scan',side_effect=fixed_signal),\
             patch.object(runner.market_data,'refresh_book',side_effect=lambda s:dict(s,status='paper_fill')),\
             patch.object(runner.market_data,'get'),patch.object(runner.time,'time',side_effect=lambda:clock['t']),\
             patch.object(runner.time,'sleep',side_effect=sleep),patch.object(runner.signal,'signal'),\
             patch('sys.argv',['runner.py','--live']):
            with self.assertRaises(KeyboardInterrupt):runner.main()
        self.assertEqual(len(self.ex.posted),2)
        d=json.loads((root/'status.json').read_text());self.assertEqual(d['attempts'],2)
        self.assertEqual(d['active'][0]['filled'],'5');self.assertEqual(d['max_ioc_submissions'],3)
    def test_runner_retries_same_signal_after_zero_without_waiting_next_minute(self):
        self.run_zero_runner()
    def test_runner_retries_after_initial_zero_cancellation_lookup_lags(self):
        self.run_zero_runner(delayed=True)
    def test_retry_scheduler_requires_positive_confirmation(self):
        self.assertFalse(retry_allowed('paper_fill',ZERO_FILL_RETRY,2,8,True))
        self.assertTrue(retry_allowed('paper_fill',ZERO_FILL_RETRY,2,8,True,True))
        self.assertFalse(retry_allowed('paper_fill',ZERO_FILL_RETRY,2,26,True,True))
    def test_transient_order_lookup_does_not_discard_other_frozen_signal(self):
        self.ex.outcomes=['5'];self.engine.attempt(self.s);original=self.ex.order
        self.ex.order=lambda _:(_ for _ in ()).throw(RuntimeError('404 on GET /portfolio/orders/oid1'))
        self.assertTrue(self.engine.reconcile());self.assertTrue(runner.has_pending_reconciliation(self.engine))
        other=dict(signal(ticker='KXETH15M-TEST'),asset='ETH',lane='cushion_z2')
        reason=self.engine.attempt(other);self.assertIn('404',reason)
        rr=runner.retry_reason_for(self.engine,other,reason)
        self.assertEqual(rr,'Order reconciliation pending')
        self.assertTrue(retry_allowed('paper_fill',rr,1,5,False))
        self.assertFalse(retry_allowed('paper_fill',rr,1,26,False))
        self.assertEqual(len(self.ex.posted),1)
        self.ex.order=original;self.assertEqual(self.engine.reconcile(),[])
        self.assertFalse(runner.has_pending_reconciliation(self.engine))
        self.assertEqual(self.engine.attempt(other),'ack');self.assertEqual(len(self.ex.posted),2)
    def test_unfilled_scanner_label_and_attempt_history_are_truthful(self):
        self.zero()
        rec=dict(status='already_entered',ticker=self.s['ticker'],decision_ts=1000)
        with patch.object(runner.time,'time',return_value=self.now):
            shown=runner.ownership_status(rec,self.store)
        self.assertEqual(shown['status'],'zero_fill_locked');self.assertIn('zero fills',shown['execution_status'])
    def test_real_runner_resumes_other_market_one_second_after_index_lag(self):
        root=self.root/'index_runner';root.mkdir();(root/'ARM_5CT').write_text('5');(root/'ARM_Z3_5CT').write_text('5')
        self.ex.outcomes=['5','5'];clock={'t':1023.0};original=self.ex.order;lookups={'n':0}
        def order(oid):
            lookups['n']+=1
            if lookups['n']==1:raise RuntimeError('404 order not indexed yet')
            return original(oid)
        self.ex.order=order
        def scan(asset,T,entered,lane):
            if asset in ('BTC','ETH') and lane=='cushion_z2':
                return dict(signal(ticker='KX'+asset+'15M-TEST'),asset=asset,lane=lane,decision_ts=T,close_ts=T+300)
            return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
        def sleep(_):
            clock['t']+=1.2
            if clock['t']>1029:raise KeyboardInterrupt()
        with patch.object(runner,'STATE',root),patch.object(runner,'legacy_safe',return_value=''),\
             patch.object(runner.Credentials,'load',return_value=None),patch.object(runner,'make_stream',return_value=None),patch.object(runner,'KalshiClientV2'),\
             patch.object(runner,'Exchange',return_value=self.ex),\
             patch.object(runner,'Engine',side_effect=lambda *a,**k:Engine(*a,clock=lambda:clock['t'],**k)),\
             patch.object(runner.market_data,'scan',side_effect=scan),\
             patch.object(runner.market_data,'refresh_book',side_effect=lambda s:dict(s,status='paper_fill')),\
             patch.object(runner.market_data,'get'),patch.object(runner.time,'time',side_effect=lambda:clock['t']),\
             patch.object(runner.time,'sleep',side_effect=sleep),patch.object(runner.signal,'signal'),\
             patch('sys.argv',['runner.py','--live']):
            with self.assertRaises(KeyboardInterrupt):runner.main()
        self.assertEqual([r['asset'] for r in self.ex.posted],['BTC','ETH'])
        self.assertLess(self.ex.posted[1]['created_ts']-self.ex.posted[0]['created_ts'],2)
        d=json.loads((root/'status.json').read_text());self.assertEqual(len(d['active']),2);self.assertEqual(d['error'],'')

if __name__=='__main__':unittest.main()
