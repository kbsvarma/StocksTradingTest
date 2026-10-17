"""Offline meaningful failure/recovery tests; network and credentials are mocked."""
import json,tempfile,unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from decimal import Decimal as D
import runner,engine,io_budget
from lanes import NONCRYPTO_ARM,BASE_NONCRYPTO_ASSETS
from test_engine import signal,FakeExchange
class Cache(unittest.TestCase):
 def test_missing_minute_refetched_then_complete_inputs_frozen(self):
  T=6000;url=f'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60&start=300&end={T}'
  full=[[t,1,1,1,100+t*.001,1] for t in range(T-3660,T,60)];pending=full[1:]
  answers=[pending,full,[[t,1,1,1,999,1] for t in range(T-3660,T,60)]];calls=[]
  def get(u):calls.append(u);return answers.pop(0)
  cache=runner.MinuteQuotes(get);cache.begin(T)
  self.assertEqual(cache(url),pending);self.assertEqual(cache(url),full);self.assertEqual(cache(url),full);self.assertEqual(len(calls),2)
 def test_invalid_price_never_cached(self):
  T=6000;url=f'https://api.exchange.coinbase.com/products/ETH-USD/candles?end={T}'
  for bad in (0,float('nan'),float('inf'),-1):
   raw=[[t,1,1,1,100,1] for t in range(T-3660,T,60)];raw[0][4]=bad
   self.assertFalse(runner.MinuteQuotes.cacheable(url,raw))
 def test_valid_candle_cache_and_books_stay_fresh(self):
  calls=[]
  def get(url):calls.append(url);return {'orderbook_fp':{}}
  cache=runner.MinuteQuotes(get);cache.begin(6000)
  for _ in range(2):cache('https://api.elections.kalshi.com/trade-api/v2/markets/T/orderbook?depth=10')
  self.assertEqual(len(calls),2)
class Integration(unittest.TestCase):
 def run_case(self,account_failure=False,incomplete=False):
  with tempfile.TemporaryDirectory() as tmp:
   state=Path(tmp);(state/'ARM_5CT').write_text('5');(state/'ARM_Z3_5CT').write_text('5');(state/'ARM_WTI_5CT').write_text('5');(state/'HALT_COPPER').write_text('user pause')
   (state/NONCRYPTO_ARM).write_text(json.dumps(dict(version=1,contracts=5,assets=list(BASE_NONCRYPTO_ASSETS))))
   clock={'t':1023.};seen=[];budgets=[];ex=FakeExchange();actual_submit=ex.submit
   def submit(r):
    result=actual_submit(r)
    ex.remote=[dict(order_id='oid',ticker=r['ticker'],client_order_id=r['client_id'],status='executed',initial_count_fp='5',fill_count_fp='5',remaining_count_fp='0')]
    ex.fs=[dict(fill_id='f',order_id='oid',ticker=r['ticker'],count_fp='5',yes_price_dollars='.92',fee_cost='.0258')]
    return result
   ex.submit=submit
   def balance(exchange_index=2):
    if account_failure:raise TimeoutError('account unavailable')
    return D(50)
   ex.balance=balance
   def scan(asset,T,entered,lane):
    seen.append((asset,lane));budgets.append(io_budget.remaining())
    if asset=='BTC' and not incomplete:raise TimeoutError('BTC public unavailable')
    target='BTC' if incomplete else 'ETH'
    if asset==target and lane=='cushion_z2':
     if incomplete and seen.count((asset,lane))==1:return dict(asset=asset,lane=lane,decision_ts=T,status='incomplete_spot')
     return dict(signal(ticker='KX'+asset+'15M-TEST'),lane=lane,decision_ts=T,close_ts=T+300)
    return dict(asset=asset,lane=lane,decision_ts=T,status='no_current_market')
   def sleep(_):
    clock['t']+=1.2
    if clock['t']>1028:raise KeyboardInterrupt()
   real_engine=engine.Engine
   with ExitStack() as stack:
    for target,kwargs in [('STATE',dict(new=state)),('legacy_safe',dict(return_value='')),('KalshiClientV2',{}),('Exchange',dict(return_value=ex)),('Engine',dict(side_effect=lambda *a,**k:real_engine(*a,clock=lambda:clock['t'],**k)))]:stack.enter_context(patch.object(runner,target,**kwargs))
    stack.enter_context(patch.object(runner.Credentials,'load',return_value=None));stack.enter_context(patch.object(runner.market_data,'warm_sources',return_value={}))
    stack.enter_context(patch.object(runner.market_data,'scan',side_effect=scan));stack.enter_context(patch.object(runner.market_data,'get'));stack.enter_context(patch.object(runner.time,'time',side_effect=lambda:clock['t']));stack.enter_context(patch.object(runner.time,'sleep',side_effect=sleep));stack.enter_context(patch.object(runner.signal,'signal'));stack.enter_context(patch('sys.argv',['runner.py','--live']))
    with self.assertRaises(KeyboardInterrupt):runner.main()
   result=json.loads((state/'status.json').read_text());return ex.posted,seen,result,budgets
 def test_btc_data_failure_isolated_other_asset_can_fill(self):
  posted,seen,s,budgets=self.run_case();self.assertEqual([r['asset'] for r in posted],['ETH']);self.assertGreater(seen.count(('BTC','cushion_z2')),1);self.assertIn(('SOL','premium_z3'),seen);self.assertFalse(s['error']);self.assertFalse(s['halted']);self.assertTrue(all(0<x<=3 for x in budgets))
 def test_account_failure_still_blocks_every_order(self):
  posted,seen,s,budgets=self.run_case(account_failure=True);self.assertEqual(posted,[]);self.assertEqual(seen,[]);self.assertIn('account unavailable',s['error'])
 def test_incomplete_crypto_data_retried_in_same_minute(self):
  posted,seen,s,budgets=self.run_case(incomplete=True);self.assertEqual([r['asset'] for r in posted],['BTC']);self.assertGreater(seen.count(('BTC','cushion_z2')),1)
 def test_user_copper_pause_preserved_both_lanes(self):
  posted,seen,s,budgets=self.run_case();self.assertFalse(any(a=='COPPER' for a,l in seen));self.assertNotIn('COPPER',s['assets'])
 def test_authoritative_fill_display_overrides_old_attempt_message(self):
  class Store:
   def get(self,t):return dict(status='open',filled='5.00')
  class Engine:store=Store()
  self.assertEqual(runner.execution_result(Engine(),'T','Already attempted this market'),'Filled 5.00 contracts')
if __name__=='__main__':unittest.main()
