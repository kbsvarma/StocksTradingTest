import copy,json,threading,time,unittest,tempfile,gzip
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from decimal import Decimal as D
from account_reads import FreshAccountReads,ReadClient
from kalshi_client import KalshiClientV2
from engine import Engine,Store,Exchange,crypto
from test_engine import FakeExchange,signal
from lanes import ACTIVE_ASSETS,ACTIVE_NEW_ASSETS,ALL_ASSETS,asset_permission,lane_for,Z2,Z3
import market_data,operations_view as view,dashboard_panel as panel

class ParallelReads(unittest.TestCase):
 def make(self,handler):
  made=[]
  class Client:
   def __init__(self,*a,**kw):self.session=Mock();self.kw=kw;made.append(self)
   def _request(self,method,path,**kw):
    assert method=='GET';return handler(path,kw['params'])
  read=FreshAccountReads(SimpleNamespace(creds=None,base_url='https://example.invalid',timeout=5),factory=Client)
  self.addCleanup(read.close);return read,made
 def test_reads_overlap_with_separate_read_only_sessions(self):
  barrier=threading.Barrier(3);seen=[]
  def handler(path,p):
   seen.append((path,p));barrier.wait(timeout=2)
   return {'/portfolio/positions':{'market_positions':[]},'/portfolio/orders':{'orders':[]},'/portfolio/balance':{'balance_dollars':'50'}}[path]
  reader,clients=self.make(handler);self.assertEqual(reader.snapshot(2),([],[],D(50)))
  self.assertEqual(len({id(c.session) for c in clients}),3);self.assertTrue(all(c.kw['dry_run'] for c in clients));self.assertEqual(len(seen),3)
  self.assertTrue(all(p['subaccount']==0 for _,p in seen));self.assertEqual(reader.last_timing['mode'],'parallel_fresh_get')
 def test_every_batch_is_fresh_and_preserves_route(self):
  calls=[];cash=[50]
  def handler(path,p):
   calls.append((path,p));return {'/portfolio/positions':{'market_positions':[]},'/portfolio/orders':{'orders':[]},'/portfolio/balance':{'balance_dollars':str(cash[0])}}[path]
  reader,_=self.make(handler);self.assertEqual(reader.snapshot(2)[2],50);cash[0]=0;self.assertEqual(reader.snapshot(0)[2],0)
  self.assertEqual(len(calls),6);self.assertEqual([p['exchange_index'] for path,p in calls if path.endswith('balance')],[2,0])
 def test_error_drains_other_workers_and_does_not_reuse_old_snapshot(self):
  fail=[False];completed=[]
  def handler(path,p):
   if fail[0] and path.endswith('positions'):raise TimeoutError('positions unavailable')
   if fail[0]:time.sleep(.015);completed.append(path)
   return {'/portfolio/positions':{'market_positions':[]},'/portfolio/orders':{'orders':[]},'/portfolio/balance':{'balance_dollars':'50'}}[path]
  reader,_=self.make(handler);reader.snapshot(2);fail[0]=True
  with self.assertRaises(TimeoutError):reader.snapshot(2)
  self.assertEqual(len(completed),2);self.assertIsNone(reader.last_timing)
 def test_pagination_preserved_and_cycles_rejected(self):
  calls=[]
  def handler(path,p):
   calls.append((path,p))
   if path.endswith('positions'):return {'market_positions':[{'ticker':p.get('cursor','first')}],'cursor':'next' if not p.get('cursor') else ''}
   return {'orders':[]} if path.endswith('orders') else {'balance':5000}
  reader,_=self.make(handler);self.assertEqual(len(reader.snapshot(0)[0]),2)
  with patch.object(reader.clients[0],'_request',return_value={'market_positions':[],'cursor':'repeat'}):
   with self.assertRaises(ValueError):reader.snapshot(0)
 def test_malformed_balance_and_positions_fail_closed(self):
  for reply in ({'balance_dollars':'NaN'},{'unexpected':'field'}):
   reader,_=self.make(lambda path,p: {'market_positions':[]} if path.endswith('positions') else {'orders':[]} if path.endswith('orders') else reply)
   with self.assertRaises((ValueError,KeyError)):reader.snapshot(2)
 def test_worker_rejects_order_writes_and_unrelated_gets(self):
  client=ReadClient(dry_run=True)
  with patch.object(KalshiClientV2,'_request') as transport:
   for method,path in [('POST','/portfolio/events/orders'),('DELETE','/portfolio/events/orders/id'),('GET','/markets')]:
    with self.assertRaises(RuntimeError):client._request(method,path)
   transport.assert_not_called()
  client.session.close()

class ExecutionInvariant(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'s.db');self.ex=FakeExchange();self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:1003)
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def test_account_failure_never_reaches_book_or_order(self):
  self.ex.account_snapshot=Mock(side_effect=TimeoutError('snapshot failed'));self.ex.book=Mock()
  with self.assertRaises(TimeoutError):self.e.attempt(signal())
  self.ex.book.assert_not_called();self.assertFalse(self.ex.posted)
 def test_fresh_parallel_snapshot_reaches_same_cash_guard(self):
  self.ex.account_snapshot=Mock(return_value=([],[],D(0)))
  s=signal();self.assertIn('funding required',self.e.attempt(s));self.assertFalse(s['execution_executable']);self.assertFalse(self.ex.posted)
 def test_final_book_still_rejects_price_depth_floor_and_expiry(self):
  for levels in [[['.06','5']],[['.08','4.99']],[['.16','5']]]:
   self.ex.book=lambda _:dict(no_dollars=levels);s=signal();self.e.attempt(s)
   self.assertFalse(s['execution_executable']);self.assertFalse(self.ex.posted)
  self.ex.book=lambda _:dict(no_dollars=[['.08','5']]);self.e.clock=lambda:1036;s=signal();self.assertEqual(self.e.attempt(s),'Signal expired');self.assertFalse(s['execution_executable'])
 def test_passing_execution_and_timing_are_recorded(self):
  s=signal();s['book_observed_ts']=1002.8;self.assertEqual(self.e.attempt(s),'ack');self.assertTrue(s['execution_executable']);self.assertEqual(s['execution_verified_ts'],1003);self.assertAlmostEqual(s['execution_book_gap_ms'],200)
 def test_prior_retry_executable_receipts_are_cleared(self):
  s=signal();s.update(execution_executable=True,execution_verified_ts=1003,execution_book_levels=[['.08','5']],execution_cap='.93',execution_account_ms=50)
  with patch.object(market_data.time,'time',return_value=1004),patch.object(market_data,'get',return_value={'orderbook_fp':{'no_dollars':[['.04','5']]}}):r=market_data.refresh_book(s)
  self.assertFalse(any(k.startswith('execution_') for k in r));self.assertEqual(r['status'],'book_outside_limit')
 def test_copper_retired_even_if_old_halt_is_removed(self):
  self.assertNotIn('COPPER',ACTIVE_ASSETS);self.assertNotIn('COPPER',ACTIVE_NEW_ASSETS);self.assertIn('COPPER',ALL_ASSETS);self.assertTrue(crypto('KXCOPPER15M-OLD'))
  self.assertEqual(asset_permission(self.root,'COPPER'),'COPPER retired')
  s=signal(ticker='KXCOPPER15M-TEST');self.assertEqual(self.e.attempt(s),'COPPER retired');self.assertFalse(self.ex.posted)
 def test_copper_external_positions_still_count_toward_risk(self):
  self.ex.pos=[dict(ticker='KXCOPPER15M-OLD',position_fp='25',exchange_index=2)]
  self.assertEqual(self.e.attempt(signal()),'25-contract cap')
 def test_source_warmup_never_requests_copper(self):
  with patch.object(market_data.PYTH,'warm') as warm:market_data.warm_sources()
  self.assertEqual([c.args[0] for c in warm.call_args_list],['NATGAS','EURUSD','WTI'])

class Funnel(unittest.TestCase):
 def event(self,t='KXBTC15M-TEST',**kw):return dict(signal(ticker=t),lane=Z2,status='eligible',**kw)
 def test_old_eligible_or_good_preliminary_book_is_not_executable(self):
  e=self.event();e.update(book_levels=[['.08','5']],execution_status='Insufficient depth inside cap')
  c=view.summary([e,e],[])['counts'];self.assertEqual((c['qualified'],c['executable'],c['submitted'],c['filled']),(1,0,0,0));self.assertEqual(c['book_blocked'],1)
 def test_explicit_executable_still_not_a_fill(self):
  e=self.event(execution_executable=True,execution_verified_ts=1003)
  c=view.summary([e],[])['counts'];self.assertEqual((c['qualified'],c['executable'],c['submitted'],c['filled']),(1,1,0,0))
 def test_historical_submission_is_labeled_minimum(self):
  e=self.event();r=dict(ticker=e['ticker'],lane=Z2,status='unfilled',filled='0')
  c=view.summary([e],[r])['counts'];self.assertEqual((c['qualified'],c['executable'],c['submitted'],c['filled'],c['zero_fill'],c['historical_executable_lower_bound']),(1,1,1,0,1,1))
 def test_cross_lane_overlap_deduplicates_overview(self):
  e=self.event();z=dict(e,lane=Z3,z=3.2,signal_ask=.94,signal_bid=.93);r=dict(ticker=e['ticker'],lane=Z3,status='settled',filled='5')
  self.assertEqual(view.summary([e,z,e],[r])['counts']['qualified'],1)
  a,b=view.funnel([e,z],[r]);self.assertEqual((a['filled'],a['other_lane'],b['filled']),(0,1,1))
 def test_owned_skips_invalid_times_and_spreads_not_qualifiers(self):
  e=self.event()
  for overrides in [dict(status='already_entered'),dict(remaining_minutes=2),dict(signal_bid=.8),dict(z=float('nan'))]:
   self.assertEqual(view.summary([dict(e,**overrides)],[])['counts']['qualified'],0)
 def test_copper_not_in_active_filters_but_history_works(self):
  self.assertNotIn('Copper',panel.GROUPS);self.assertNotIn('COPPER',panel.GROUPS['All markets'])
  row=dict(ticker='KXCOPPER15M-OLD',status='settled',side='yes',result='no',pnl='-4.5')
  self.assertEqual(panel.ledger_summary([row])['losses'],1);self.assertEqual(panel.filtered([row],ACTIVE_ASSETS),[])

if __name__=='__main__':unittest.main()
