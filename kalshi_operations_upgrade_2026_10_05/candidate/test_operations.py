import copy,json,tempfile,threading,time,unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch,Mock
from contextlib import ExitStack
import book_feed,io_budget,runner,market_data,observe_execution,operations_view
from kalshi_client import KalshiClientV2,KalshiAPIError
import requests
from book_feed import Books,CheckSchedule
from engine import Store,Engine
from test_engine import signal,LADDER
from test_zero_fill import Exchange

TICKER='KXETH15M-TEST'
def snapshot(seq=1,qty='5',source=10000):
    return dict(type='orderbook_snapshot',sid=1,seq=seq,sending_ts_ms=source,
        msg=dict(market_ticker=TICKER,yes_dollars_fp=[['.96','5']],no_dollars_fp=[['.026',qty]]))
def delta(seq=2,qty='-1',source=10100):
    return dict(type='orderbook_delta',sid=1,seq=seq,sending_ts_ms=source,
        msg=dict(market_ticker=TICKER,side='no',price_dollars='.026',delta_fp=qty))

class StreamTests(unittest.TestCase):
    def setUp(self):self.b=Books();self.b.apply(snapshot(),10)
    def ready(self,now=10.1):return self.b.ready(TICKER,'yes',D('.93'),D('.975'),5,now)
    def test_exact_depth_and_cap(self):
        self.assertTrue(self.ready());self.b.apply(delta(),10.1);self.assertFalse(self.ready())
        self.b.apply(delta(3,'1',10200),10.2);self.assertTrue(self.ready(10.2))
    def test_stale_future_and_missing_source_cannot_trigger(self):
        self.assertFalse(self.ready(10.6));self.assertFalse(self.ready(9.9))
        self.b.reset();self.b.apply(snapshot(2,source=8000),10)
        self.assertFalse(self.ready())
    def test_sequence_gap_invalidates_all_books(self):
        with self.assertRaises(ValueError):self.b.apply(delta(3),10.1)
        self.assertEqual(self.b.books,{})
    def test_negative_depth_invalidates(self):
        with self.assertRaises(ValueError):self.b.apply(delta(qty='-6'),10.1)
        self.assertFalse(self.ready())
    def test_delta_without_snapshot_rejected(self):
        self.b.reset()
        with self.assertRaises(ValueError):self.b.apply(delta(),10.1)
    def test_source_regression_rejected(self):
        with self.assertRaises(ValueError):self.b.apply(delta(source=9999),10.1)
    def test_invalid_price_and_missing_timestamp(self):
        for px in ('NaN','1.01','-0.01'):
            r=snapshot();r['msg']['no_dollars_fp']=[[px,'5']]
            with self.assertRaises(Exception):self.b.apply(r,10)
        r=snapshot();r.pop('sending_ts_ms')
        with self.assertRaises(ValueError):self.b.apply(r,10)
    def test_floor_is_not_relaxed_by_good_depth(self):
        r=snapshot(2);r['msg']['no_dollars_fp']=[['.08','5']];self.b.apply(r,10.1)
        self.assertFalse(self.ready())
    def test_yes_and_no_are_complementary(self):
        r=snapshot(2);r['msg']['yes_dollars_fp']=[['.026','5']];self.b.apply(r,10.1)
        self.assertTrue(self.b.ready(TICKER,'no',D('.93'),D('.975'),5,10.1))
    def test_hint_invalidates_on_disconnect(self):
        with patch.object(book_feed.threading.Thread,'start'):
            f=book_feed.BookFeed(lambda:{})
        s=dict(signal(),ticker=TICKER,lane='premium_z3',z=3.5,signal_ask=.97,decision_ts=0)
        f.books=self.b;f.connected=True
        with patch.object(book_feed.time,'time',return_value=10.1):
            f.arm(s);self.assertIsNotNone(f.hint(s));f.connected=False;self.assertIsNone(f.hint(s))
    def test_stream_cannot_consume_tail_checks(self):
        q=CheckSchedule();checks=0;fast=0;times=[]
        for n in range(200,2501,5):
            age=n/100;hint=(n,);reason=q.ready('x',checks,age,age,hint)
            if reason:
                q.checked('x',age,hint,reason);checks+=1;times.append(age);fast+=reason=='book_update'
        self.assertLessEqual(checks,24);self.assertLessEqual(fast,4);self.assertGreaterEqual(times[-1],24)
        self.assertTrue(any(b-a<1 for a,b in zip(times,times[1:])))
    def test_fallback_checks_without_stream(self):
        q=CheckSchedule();self.assertEqual(q.ready('x',0,3,3,None),'initial');q.checked('x',3,None,'initial')
        self.assertIsNone(q.ready('x',1,3.5,3.5,None));self.assertEqual(q.ready('x',1,4,4,None),'rest_fallback')
    def test_connection_invalidates_book_before_reconnect_backoff(self):
        ws=Mock();ws.recv.side_effect=[json.dumps(snapshot()),json.dumps(delta(3))]
        context=Mock();context.__enter__=Mock(return_value=ws);context.__exit__=Mock(return_value=False)
        with patch.object(book_feed.threading.Thread,'start'),patch.object(book_feed.time,'time',return_value=10):
            f=book_feed.BookFeed(lambda:{},connector=Mock(return_value=context));f.watch(TICKER,1000)
            def backoff(_):
                self.assertFalse(f.connected);self.assertEqual(f.books.books,{});f.stop.set()
            with patch.object(f.stop,'wait',side_effect=backoff):f._run()
        self.assertEqual(f.reconnects,1);self.assertIn('sequence gap',f.error)

class TransportAndDiagnostics(unittest.TestCase):
    def test_get_budget_expires_and_restores(self):
        with patch.object(io_budget.time,'monotonic',return_value=10):
            with io_budget.get_budget(1):
                self.assertEqual(io_budget.timeout(5),1)
                with self.assertRaises(TimeoutError):io_budget.retry_sleep(1)
                with patch.object(io_budget.time,'monotonic',return_value=11):
                    with self.assertRaises(TimeoutError):io_budget.timeout(5)
        self.assertEqual(io_budget.timeout(5),5)
    def test_nested_budget_cannot_extend_outer(self):
        with io_budget.get_budget(.5):
            with io_budget.get_budget(10):self.assertLessEqual(io_budget.timeout(10),.5)
    def test_get_retry_stops_before_backoff_exceeds_budget(self):
        c=KalshiClientV2(timeout=5)
        with patch.object(c.session,'request',side_effect=requests.Timeout('slow')) as send:
            with io_budget.get_budget(.1):
                with self.assertRaises(TimeoutError):c._request('GET','/markets',signed=False)
            self.assertEqual(send.call_count,1);self.assertLessEqual(send.call_args.kwargs['timeout'],.1)
    def test_post_timeout_is_unchanged_and_not_retried(self):
        c=KalshiClientV2(dry_run=False,timeout=5)
        with patch.object(c,'_headers',return_value={}),patch.object(c.session,'request',side_effect=requests.Timeout('ambiguous')) as send:
            with io_budget.get_budget(0):
                with self.assertRaises(KalshiAPIError):c._request('POST','/portfolio/events/orders',body={})
            self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.kwargs['timeout'],5)
    def test_lane_caps_and_floors_match_executor(self):
        s=dict(signal(),signal_ask=.97,lane='premium_z3',z=3.5)
        self.assertEqual(observe_execution.exact_cap(s),.975)
        self.assertTrue(observe_execution.book_stats([['.026','5']],.975,5,.93)['enough'])
        self.assertFalse(observe_execution.book_stats([['.026','5']],.939,5,.85)['enough'])
        self.assertFalse(observe_execution.book_stats([['.08','5']],.975,5,.93)['enough'])
    def test_completed_spot_cache_retries_missing_bars(self):
        url='https://api.exchange.coinbase.com/products/BTC-USD/candles?end=6000'
        raw=[[k,0,0,0,100,0] for k in range(2340,6000,60)]
        self.assertTrue(runner.MinuteQuotes.cacheable(url,raw));self.assertFalse(runner.MinuteQuotes.cacheable(url,raw[:-1]))
        raw[0][4]=0;self.assertFalse(runner.MinuteQuotes.cacheable(url,raw))
    def test_unique_market_funnel_and_ownership_not_false_overlap(self):
        s=dict(signal(),lane='cushion_z2',status='eligible')
        owned=dict(s,lane='premium_z3',status='position_filled',z=4,signal_ask=.94)
        rows=[dict(ticker=s['ticker'],lane='cushion_z2',filled='5',status='open')]
        r=operations_view.funnel([s,s,owned],rows)
        self.assertEqual((r[0]['qualified'],r[0]['filled']),(1,1))
        self.assertEqual((r[1]['qualified'],r[1]['owned_market_skips']),(0,1))
    def test_public_429_cooldown_does_not_sleep(self):
        response=Mock(status_code=429,headers={'Retry-After':'1'});response.raise_for_status.side_effect=ValueError('429')
        with patch.object(market_data,'_RATE_UNTIL',{}),patch.object(market_data._PUBLIC_SESSION,'get',return_value=response) as get:
            with self.assertRaises(ValueError):market_data.get(market_data.BASE+'/markets')
            with self.assertRaises(TimeoutError):market_data.get(market_data.BASE+'/markets')
            self.assertEqual(get.call_count,1)

class ExecutionIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'test.db');self.ex=Exchange();self.ex.outcomes=['5']
        self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:1003)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def test_stream_hint_does_not_override_final_rest_book(self):
        s=dict(signal(),execution_trigger='book_update');self.ex.book=lambda _:dict(no_dollars=[['.04','5']])
        self.assertEqual(self.e.attempt(s),'Insufficient depth inside cap');self.assertEqual(self.ex.posted,[])
    def test_final_public_book_failure_is_retryable_without_global_halt(self):
        original=self.ex.book
        self.ex.book=lambda _:(_ for _ in ()).throw(TimeoutError('book timeout'))
        s=signal();self.assertEqual(self.e.attempt(s),'Execution book unavailable')
        self.assertEqual(self.ex.posted,[]);self.assertFalse(self.e.fault)
        self.assertTrue(runner.retry_allowed('paper_fill','Execution book unavailable',1,3,False))
        self.ex.book=original
        self.assertEqual(self.e.attempt(dict(signal(ticker='KXETH15M-TEST'),asset='ETH')),'ack')
    def test_fill_display_and_lifecycle_use_reconciled_row(self):
        s=signal();self.e.attempt(s);self.e.reconcile()
        self.assertEqual(runner.execution_result(self.e,s['ticker'],'Already attempted this market'),'Filled 5 contracts')
        events=[json.loads(x[0]) for x in self.store.db.execute('select event from events')]
        self.assertEqual([r['status'] for r in events],['intent','ack','open'])
        self.e.reconcile();self.assertEqual(self.store.db.execute('select count(*) from events').fetchone()[0],3)
        self.assertIn('execution_risk',self.store.get(s['ticker'])['signal'])
    def run_fault(self,account=False,fast=False):
        state=self.root/'state';state.mkdir();(state/'ARM_5CT').write_text('5');(state/'ARM_Z3_5CT').write_text('5')
        clock={'t':1023.};seen=[]
        def scan(asset,T,entered,lane):
            seen.append((asset,lane))
            if fast:
                if asset=='BTC' and lane=='cushion_z2':return dict(signal(),lane=lane,decision_ts=T,close_ts=T+300,status='insufficient_depth')
                return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
            if asset=='BTC':raise TimeoutError('public unavailable')
            if asset=='ETH' and lane=='cushion_z2':return dict(signal(ticker='KXETH15M-TEST'),asset=asset,lane=lane,decision_ts=T,close_ts=T+300)
            return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
        def sleep(_):
            clock['t']+=.25 if fast else 1.2
            if clock['t']>1027:raise KeyboardInterrupt()
        if account:self.ex.balance=lambda:(_ for _ in ()).throw(TimeoutError('account unavailable'))
        with ExitStack() as stack:
            for target,kwargs in [
                ('STATE',{'new':state}),('legacy_safe',{'return_value':''}),('KalshiClientV2',{}),
                ('Exchange',{'return_value':self.ex}),('Engine',{'side_effect':lambda *a,**k:Engine(*a,clock=lambda:clock['t'],**k)})]:
                stack.enter_context(patch.object(runner,target,**kwargs))
            stack.enter_context(patch.object(runner.Credentials,'load',return_value=None))
            if fast:
                class Feed(book_feed.NullFeed):
                    def hint(self,s):return (1,clock['t'])
                stack.enter_context(patch.object(runner,'make_feed',return_value=Feed()))
                stack.enter_context(patch.object(runner.market_data,'refresh_book',side_effect=lambda s:dict(s,status='paper_fill')))
            stack.enter_context(patch.object(runner.market_data,'scan',side_effect=scan))
            stack.enter_context(patch.object(runner.market_data,'get'))
            stack.enter_context(patch.object(runner.time,'time',side_effect=lambda:clock['t']))
            stack.enter_context(patch.object(runner.time,'sleep',side_effect=sleep))
            stack.enter_context(patch.object(runner.signal,'signal'))
            stack.enter_context(patch('sys.argv',['runner.py','--live']))
            with self.assertRaises(KeyboardInterrupt):runner.main()
        d=json.loads((state/'status.json').read_text())
        return seen,d
    def test_btc_public_failure_does_not_block_eth_and_retries(self):
        seen,d=self.run_fault();self.assertEqual([r['asset'] for r in self.ex.posted],['ETH'])
        self.assertGreater(seen.count(('BTC','cushion_z2')),1);self.assertIn(('SOL','premium_z3'),seen)
        self.assertFalse(d['error']);self.assertFalse(d['halted'])
    def test_account_failure_still_blocks_all_orders(self):
        seen,d=self.run_fault(account=True);self.assertEqual(self.ex.posted,[]);self.assertIn('account unavailable',d['error'])
    def test_runner_stream_hint_rechecks_within_one_second_through_normal_engine(self):
        seen,d=self.run_fault(fast=True)
        self.assertEqual(len(self.ex.posted),1)
        self.assertLess(self.ex.posted[0]['created_ts']-1023,1)
        self.assertEqual(self.ex.posted[0]['signal']['execution_trigger'],'book_update')

if __name__=='__main__':unittest.main()
