import copy
from decimal import Decimal as D
import tempfile
from pathlib import Path
import unittest
from engine import Engine,Exchange,Store,limit_price,today

LADDER=[dict(start='0',end='.1',step='.001'),dict(start='.1',end='.9',step='.01'),dict(start='.9',end='1',step='.001')]
def signal(side='yes',ticker='KXBTC15M-TEST'):
    return dict(status='paper_fill',asset=ticker.split('-')[0][2:-3],ticker=ticker,side=side,signal_ask=.92,signal_bid=.91,z=2.5,
                remaining_minutes=5,decision_ts=1000,close_ts=1300,exchange_index=2,price_ranges=LADDER)

class FakeExchange:
    def __init__(self):self.pos=[];self.rest=[];self.remote=[];self.posted=[];self.cancelled=[];self.fs=[];self.cash=D('50');self.raise_post=False
    def positions(self):return self.pos
    def orders(self,**p):return self.rest if p.get('status')=='resting' else self.remote
    def balance(self):return self.cash
    def book(self,t):return dict(no_dollars=[['.08','5']],yes_dollars=[['.08','5']])
    def submit(self,r):
        self.posted.append(copy.deepcopy(r))
        if self.raise_post:raise TimeoutError('uncertain POST')
        return dict(order_id='oid')
    def order(self,oid):return self.remote[0]
    def fills(self,oid):return self.fs
    def cancel(self,oid):self.cancelled.append(oid)
    def market(self,t):return dict(status='finalized',result='yes',close_time='2026-09-12T18:00:00Z',settlement_ts='2026-09-12T18:00:10Z')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'state.db');self.ex=FakeExchange();self.now=1010
        self.engine=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def acknowledged(self,side='yes',fill='1',status='executed'):
        s=signal(side);self.engine.attempt(s);r=self.store.get(s['ticker'])
        self.ex.remote=[dict(order_id='oid',ticker=r['ticker'],client_order_id=r['client_id'],
                             status=status,initial_count_fp='5',fill_count_fp=fill,remaining_count_fp='0')]
        self.ex.fs=[dict(fill_id='f',order_id='oid',ticker=r['ticker'],count_fp=fill,yes_price_dollars='.92' if side=='yes' else '.08',fee_cost='.006')]
        return r
    def test_observer_never_submits(self):
        self.engine.live=False;self.assertEqual(self.engine.attempt(signal()),'Observation only');self.assertEqual(self.ex.posted,[])
    def test_intent_is_durable_before_post(self):
        old=self.ex.submit
        def submit(r):
            self.assertEqual(self.store.get(r['ticker'])['status'],'intent');return old(r)
        self.ex.submit=submit;self.engine.attempt(signal())
    def test_timeout_is_not_retried_even_after_restart(self):
        self.ex.raise_post=True;self.engine.attempt(signal())
        self.assertEqual(self.store.get(signal()['ticker'])['status'],'unknown')
        restarted=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        restarted.attempt(signal());self.assertEqual(len(self.ex.posted),1)
    def test_unknown_post_recovered_by_client_id(self):
        r=self.acknowledged();r.pop('order_id');r['status']='unknown';self.store.save(r)
        self.engine.reconcile();self.assertEqual(self.store.get(r['ticker'])['status'],'open')
    def test_partial_fill_kept_without_topup(self):
        r=self.acknowledged(fill='.4',status='canceled');self.engine.reconcile()
        self.assertEqual(self.store.get(r['ticker'])['filled'],'0.4')
        self.engine.attempt(signal());self.assertEqual(len(self.ex.posted),1)
    def test_fill_index_lag_blocks_new_orders(self):
        r=self.acknowledged();self.ex.fs=[];self.engine.reconcile()
        self.assertEqual(self.store.get(r['ticker'])['status'],'accounting_pending')
        self.assertEqual(self.engine.attempt(signal(ticker='KXETH15M-TEST')),'Order reconciliation pending')
    def test_no_price_and_actual_fees(self):
        r=self.acknowledged(side='no');self.now=1400;self.engine.reconcile()
        got=self.store.get(r['ticker']);self.assertEqual(D(got['entry']),D('.92'));self.assertEqual(D(got['pnl']),D('-.926'))
    def test_settlement_is_idempotent(self):
        r=self.acknowledged();self.now=1400;self.engine.reconcile();self.engine.reconcile()
        self.assertEqual(len(self.store.all()),1);self.assertEqual(D(self.store.get(r['ticker'])['pnl']),D('.074'))
    def test_duplicate_fill_ids_not_double_counted(self):
        r=self.acknowledged();self.ex.fs*=2;self.engine.reconcile()
        self.assertEqual(D(self.store.get(r['ticker'])['filled']),1)
    def test_overfill_blocks(self):
        self.acknowledged(fill='6');self.assertTrue(self.engine.reconcile());self.assertIn('invariant',self.engine.fault)
    def test_foreign_resting_orders_block_without_cancelling(self):
        self.ex.rest=[dict(ticker='KXETH15M-OTHER',order_id='foreign')]
        self.assertEqual(self.engine.attempt(signal()),'Crypto resting orders exist');self.assertFalse(self.ex.cancelled)
    def test_same_asset_position_blocks(self):
        self.ex.pos=[dict(ticker='KXBTC15M-OTHER',position_fp='-1')]
        self.assertEqual(self.engine.attempt(signal()),'Asset already has exposure')
    def test_twenty_contract_cap(self):
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='16')]
        self.assertEqual(self.engine.attempt(signal()),'20-contract cap')
    def test_daily_budget_counts_open_risk(self):
        self.store.save(dict(ticker='old',status='settled',pnl='-13.2',settled_day=today()))
        self.assertEqual(self.engine.attempt(signal()),'Daily loss budget including open risk')
    def test_manual_halt_preserves_reconciliation(self):
        r=self.acknowledged();(self.root/'HALT').touch();self.now=1400;self.engine.reconcile()
        self.assertEqual(self.store.get(r['ticker'])['status'],'settled')
        self.assertEqual(self.engine.attempt(signal(ticker='KXETH15M-TEST')),'Manual HALT')
    def test_cancel_only_own_unexpected_rest(self):
        r=self.acknowledged(status='resting');self.engine.reconcile();self.assertEqual(self.ex.cancelled,['oid'])
        self.assertEqual(self.store.get(r['ticker'])['status'],'ack')
    def test_observe_reconciliation_never_cancels(self):
        self.acknowledged(status='resting');self.engine.live=False;self.engine.reconcile();self.assertEqual(self.ex.cancelled,[])
    def test_stale_signal_cannot_order(self):
        self.now=1040;self.assertEqual(self.engine.attempt(signal()),'Signal expired');self.assertFalse(self.ex.posted)
    def test_arm_removed_during_preflight_blocks_post(self):
        state={'armed':True};self.engine.permission_check=lambda:'' if state['armed'] else 'Live arm removed'
        old=self.ex.book
        def book(t):state['armed']=False;return old(t)
        self.ex.book=book
        self.assertEqual(self.engine.attempt(signal()),'Live arm removed');self.assertFalse(self.ex.posted)
    def test_definitive_rejection_is_not_ambiguous(self):
        class Rejected(Exception):status=400
        def reject(r):raise Rejected('bad request')
        self.ex.submit=reject
        self.assertEqual(self.engine.attempt(signal()),'rejected')
    def test_book_weakened_does_not_count_as_bargain(self):
        self.ex.book=lambda t:dict(no_dollars=[['.30','5']])
        self.assertEqual(self.engine.attempt(signal()),'Book weakened below floor')
    def test_tick_rounding_never_increases_cost(self):
        for side in ('yes','no'):
            s=signal(side);s['signal_ask']=.855
            px,cost=limit_price(s);self.assertLessEqual(cost,D('.865'));self.assertEqual(cost,D('.86'))
    def test_order_payload_two_contract_ioc_no_fallback(self):
        class Client:
            def _request(self,method,path,body):self.body=body;return dict(order_id='x')
        c=Client();a=Exchange(c)
        a.submit(dict(ticker='test',client_id='id',side='no',yes_limit='.0750',exchange_index=2,requested_count='2'))
        self.assertEqual(c.body['count'],'2.00');self.assertEqual(c.body['side'],'ask')
        self.assertEqual(c.body['time_in_force'],'immediate_or_cancel');self.assertFalse(c.body['post_only'])
    def test_cancel_routes_to_crypto_instead_of_default_exchange(self):
        class Client:
            def _request(inner,method,path,params):
                self.assertEqual((method,path),('DELETE','/portfolio/events/orders/oid'))
                self.assertEqual(params,{'exchange_index':2,'subaccount':0})
                return {'order_id':'oid','reduced_by':'1.00'}
        self.assertEqual(Exchange(Client()).cancel('oid')['order_id'],'oid')
    def test_balance_is_scoped_to_crypto_exchange(self):
        class Client:
            def _request(inner,method,path,params):
                self.assertEqual((method,path),('GET','/portfolio/balance'))
                self.assertEqual(params,{'subaccount':0,'exchange_index':2})
                return {'balance_dollars':'0.0000'}
        self.assertEqual(Exchange(Client()).balance(),D('0'))
    def test_unfunded_crypto_blocks_before_reserving_order(self):
        self.ex.cash=D('0')
        self.assertIn('funding required',self.engine.attempt(signal()))
        self.assertFalse(self.ex.posted)
        self.assertEqual(self.store.all(),[])
    def test_changed_exchange_requires_review(self):
        s=signal();s['exchange_index']=3
        self.assertIn('exchange changed',self.engine.attempt(s))
        self.assertFalse(self.ex.posted)
    def test_pagination_consumed(self):
        seen=[]
        def page(**kw):
            seen.append(kw);return dict(rows=[kw.get('cursor','a')],cursor='b' if not kw.get('cursor') else '')
        self.assertEqual(Exchange(None).pages(page,'rows'),['a','b']);self.assertEqual(len(seen),2)

if __name__=='__main__':unittest.main()
