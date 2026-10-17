"""Offline routing, source finality, ownership and portfolio invariants."""
import copy
import datetime as dt
import json
import math
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import market_data
from pyth_data import PythMinutes
from lanes import *
from engine import Engine, Store, Exchange, today
from test_engine import FakeExchange, signal

class RoutingExchange(FakeExchange):
    def __init__(self):
        super().__init__();self.cash_by_route={0:D('50'),2:D('50')};self.read_routes=[]
    def balance(self,exchange_index=2):
        self.read_routes.append(exchange_index);return self.cash_by_route[exchange_index]
    def cancel(self,oid,exchange_index=2):self.cancelled.append((oid,exchange_index))

class Profiles(unittest.TestCase):
    def test_frozen_research_profiles(self):
        expected={'COPPER':(1.5,2.5,.8,1,0),'NATGAS':(2,3,.85,3,0),'EURUSD':(1.75,2.75,.85,1,.5),'WTI':(1.5,2.5,.85,3,0)}
        for a,(lo,hi,floor,minute,slope) in expected.items():
            x,y=(lane_for(dict(asset=a,lane=l)) for l in (Z2,Z3))
            self.assertEqual((x.z,y.z,float(x.floor),x.min_minutes,x.z_slope),(lo,hi,floor,minute,slope))
            self.assertEqual((x.cap,y.cap,x.contracts,y.contracts),(D('.939'),D('.975'),5,5))
    def test_crypto_profiles_unchanged(self):
        for a in CRYPTO_ASSETS:
            for l in (Z2,Z3):self.assertEqual(lane_for(dict(asset=a,lane=l)),replace(LANES[l],z=3) if a=='BTC' and l==Z2 else LANES[l])
    def test_fx_threshold_adapts_before_decision(self):
        r=lane_for(dict(asset='EURUSD'))
        self.assertAlmostEqual(r.threshold(1),1.75+.5*math.log2(.2))
        self.assertEqual(r.threshold(5),1.75);self.assertEqual(r.threshold(10),2.25)
        for t in [0,-1,float('nan')]:
            with self.assertRaises(ValueError):r.threshold(t)
    def test_reject_mismatched_ticker_or_unknown_asset(self):
        with self.assertRaises(ValueError):lane_for(dict(asset='BTC',ticker='KXEURUSD15M-TEST'))
        with self.assertRaises(ValueError):route_for(dict(ticker='KXGOLD15M-TEST'))

class RouteRisk(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'ledger.db');self.ex=RoutingExchange();self.now=1010
        self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        (self.root/NONCRYPTO_ARM).write_text(json.dumps(dict(version=1,contracts=5,assets=list(BASE_NONCRYPTO_ASSETS))))
        (self.root/Z3_ARM).write_text('5')
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def sig(self,a='EURUSD',lane=Z2):
        s=signal(ticker='KX'+a+'15M-TEST');s.update(exchange_index=route_for(s),lane=lane)
        return s
    def test_default_route_cash_used_for_fx(self):
        self.ex.cash_by_route[2]=D('0');self.assertEqual(self.e.attempt(self.sig()),'ack')
        self.assertEqual(self.ex.read_routes,[0]);self.assertEqual(self.ex.posted[0]['exchange_index'],0)
    def test_commodity_cash_uses_route_two(self):
        self.assertEqual(self.e.attempt(self.sig('NATGAS')),'ack');self.assertEqual(self.ex.read_routes,[2])
    def test_aggregate_cash_cannot_substitute_for_empty_fx_route(self):
        self.ex.cash_by_route[0]=D('0');self.assertIn('funding required',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
        self.assertEqual(self.e.attempt(self.sig('BTC')),'ack')
    def test_disabled_by_default(self):
        (self.root/NONCRYPTO_ARM).unlink();self.assertIn('not activated',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
    def test_per_asset_halt(self):
        (self.root/'HALT_EURUSD').touch();self.assertIn('halted',self.e.attempt(self.sig()))
        self.assertEqual(self.e.attempt(self.sig('NATGAS')),'ack')
    def test_arm_removed_during_final_book_blocks(self):
        book=self.ex.book
        def remove(t):
            (self.root/NONCRYPTO_ARM).unlink();return book(t)
        self.ex.book=remove;self.assertIn('not activated',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
    def test_arm_removed_after_durable_intent_blocks(self):
        reserve=self.store.reserve
        def remove(*args,**kw):
            reserve(*args,**kw);(self.root/NONCRYPTO_ARM).unlink()
        self.store.reserve=remove;self.e.attempt(self.sig())
        self.assertFalse(self.ex.posted);self.assertEqual(self.store.all()[0]['status'],'not_submitted')
    def test_cross_route_positions_share_daily_risk(self):
        self.ex.pos=[dict(ticker='KXBTC15M-OTHER',position_fp='16',exchange_index=2)]
        self.assertEqual(self.e.attempt(self.sig()),'Daily loss budget including open risk');self.assertFalse(self.ex.posted)
    def test_cross_route_positions_share_contract_cap(self):
        self.ex.pos=[dict(ticker='KXBTC15M-OTHER',position_fp='21',exchange_index=2)]
        self.assertEqual(self.e.attempt(self.sig()),'25-contract cap')
    def test_wrong_route_position_rejected(self):
        self.ex.pos=[dict(ticker='KXEURUSD15M-OTHER',position_fp='1',exchange_index=2)]
        with self.assertRaises(ValueError):self.e.attempt(self.sig('NATGAS'))
    def test_one_market_owned_across_lanes(self):
        s=self.sig('NATGAS');self.e.attempt(s)
        y=dict(s,lane=Z3,z=4,signal_ask=.95,signal_bid=.94)
        self.assertEqual(self.e.attempt(y),'Already attempted this market');self.assertEqual(len(self.ex.posted),1)
    def test_dynamic_time_threshold_enforced(self):
        s=self.sig();s.update(remaining_minutes=10,z=2.1)
        with self.assertRaises(ValueError):self.e.attempt(s)
        s.update(remaining_minutes=1,z=1);self.assertEqual(self.e.attempt(s),'ack')
    def test_fx_reconciliation_cancels_only_own_route(self):
        s=self.sig();self.e.attempt(s);r=self.store.get(s['ticker'])
        self.ex.remote=[dict(order_id='oid',ticker=r['ticker'],client_order_id=r['client_id'],status='resting',initial_count_fp='5',fill_count_fp='0',remaining_count_fp='5')]
        self.e.reconcile();self.assertEqual(self.ex.cancelled,[('oid',0)])
    def test_fx_fills_and_settlement_use_actual_cost(self):
        s=self.sig();self.e.attempt(s);r=self.store.get(s['ticker'])
        self.ex.remote=[dict(order_id='oid',ticker=r['ticker'],client_order_id=r['client_id'],status='executed',initial_count_fp='5',fill_count_fp='5',remaining_count_fp='0')]
        self.ex.fs=[dict(fill_id='f',order_id='oid',ticker=r['ticker'],count_fp='5',yes_price_dollars='.92',fee_cost='.03')]
        self.now=1400;self.assertEqual(self.e.reconcile(),[])
        r=self.store.get(s['ticker']);self.assertEqual(r['status'],'settled');self.assertEqual(D(r['pnl']),D('.37'))
    def test_fx_payload_and_balance_scope(self):
        calls=[]
        class Client:
            def _request(self,*args,**kw):calls.append((args,kw));return dict(balance_dollars='12',order_id='x')
        x=Exchange(Client());self.assertEqual(x.balance(0),D('12'))
        self.assertEqual(calls[-1][1]['params']['exchange_index'],0)
        x.submit(dict(ticker='KXEURUSD15M-T',client_id='c',side='yes',yes_limit='.93',exchange_index=0,requested_count='5'))
        self.assertEqual(calls[-1][1]['body']['exchange_index'],0)
        self.assertEqual(calls[-1][1]['body']['time_in_force'],'immediate_or_cancel')

class PythFinality(unittest.TestCase):
    T=12000
    def setUp(self):
        self.p=PythMinutes();self.asset='COPPER'
        iso=lambda x:dt.datetime.fromtimestamp(x,dt.timezone.utc).isoformat()
        self.market=dict(ticker='KXCOPPER15M-TEST',event_ticker='EVENT',open_time=iso(self.T-600),close_time=iso(self.T+300),notional_value_dollars='1',exchange_index=2)
        self.points=[dict(t=(t+59)*1000,v=100+.002*(i%3)+i*.01) for i,t in enumerate(range(self.T-3660,self.T,60))]
    def raw(self,points=None,asset=None):return dict(live_data=dict(details=dict(asset=asset or self.asset,timeseries=self.points if points is None else points)))
    def sample(self,raw):
        with patch('pyth_data.time.time',return_value=self.T+2):return self.p.sample(self.asset,self.market,self.T,lambda u:raw,'https://unused')
    def test_exact_completed_minute_coverage(self):
        vals,ref,evidence=self.sample(self.raw());self.assertEqual(len(vals),61)
        self.assertEqual(evidence['bars_end'],self.T-60);self.assertEqual(evidence['reference_ts'],self.T-660)
    def test_missing_terminal_second_waits_and_can_recover(self):
        self.assertIsNone(self.sample(self.raw(self.points[:-1])))
        self.assertIsNotNone(self.sample(self.raw()))
    def test_intermediate_or_future_seconds_cannot_fill_gap(self):
        bad=self.points[:-1]+[dict(t=(self.T-2)*1000,v=999),dict(t=(self.T+59)*1000,v=999)]
        self.assertIsNone(self.sample(self.raw(bad)))
    def test_wrong_asset_and_nonfinite_rejected(self):
        with self.assertRaises(ValueError):self.sample(self.raw(asset='EURUSD'))
        for value in [float('nan'),float('inf'),0,-1]:
            with self.assertRaises(ValueError):self.sample(self.raw([dict(t=self.points[0]['t'],v=value)]))
    def test_complete_decision_is_frozen_across_lanes(self):
        first=self.sample(self.raw());changed=copy.deepcopy(self.points);changed[-1]['v']+=1
        self.assertEqual(first,self.sample(self.raw(changed)))
        with patch('pyth_data.time.time',return_value=self.T+62):
            second=self.p.sample(self.asset,self.market,self.T+60,lambda u:self.raw(changed+[dict(t=(self.T+59)*1000,v=102)]),'https://unused')
        self.assertEqual(second[0][-2],changed[-1]['v'])
    def test_current_chart_must_succeed_even_with_cached_bars(self):
        self.p.absorb(self.asset,self.raw(),self.T+1)
        with self.assertRaises(ValueError):self.sample(self.raw(asset='NATGAS'))
    def test_scanner_computes_profile_from_source(self):
        import statistics
        vals=[p['v'] for p in self.points];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
        expected=(vals[-1]/vals[-11]-1)/(sigma*math.sqrt(5))
        quote=dict(end_period_ts=self.T,yes_bid=dict(close_dollars='.81'),yes_ask=dict(close_dollars='.82'))
        responses=[dict(markets=[self.market]),dict(candlesticks=[quote]),self.raw(),dict(orderbook_fp=dict(no_dollars=[['.18','5']]))]
        with patch.object(market_data,'PYTH',self.p),patch.object(market_data,'get',side_effect=responses),patch('time.time',return_value=self.T+2):
            row=market_data.scan(self.asset,self.T,set())
        self.assertEqual(row['status'],'paper_fill');self.assertAlmostEqual(row['z'],expected);self.assertEqual(row['required_z'],1.5)
    def test_new_public_chain_has_total_budget(self):
        import io_budget
        def read(url):
            self.assertLessEqual(io_budget.remaining(),3);raise TimeoutError('test')
        with patch.object(market_data,'get',side_effect=read),patch('time.time',return_value=self.T+2):
            with self.assertRaises(TimeoutError):market_data.scan(self.asset,self.T,set())

if __name__=='__main__':unittest.main()
