"""Offline deadline, late-result, pagination, write-safety and CAD retry tests."""
import copy,json,threading,time,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock,patch
import account_reads as ar
import kalshi_client as kc
import io_budget
from engine import Engine,Store
from test_engine import signal
from test_zero_fill import Exchange as ZeroExchange
from execution_retry import fresh_retry_candidate,fresh_retry_matches

class ReadTransport(unittest.TestCase):
 def setUp(self):
  self.client=kc.KalshiClientV2(creds=None,dry_run=True,timeout=5)
  self.clock=[0.0];self.calls=[]
 def tearDown(self):self.client.session.close()
 def test_timeout_budget_cuts_old_30second_path(self):
  def request(*a,**kw):self.calls.append(kw);self.clock[0]+=kw['timeout'].total;raise kc.requests.exceptions.Timeout('slow')
  with patch.object(self.client.session,'request',side_effect=request),patch.object(io_budget.time,'monotonic',side_effect=lambda:self.clock[0]),patch.object(io_budget.time,'sleep',side_effect=lambda s:self.clock.__setitem__(0,self.clock[0]+s)):
   with self.assertRaises(TimeoutError):self.client._request('GET','/portfolio/positions',signed=False)
  self.assertEqual(self.clock[0],3);self.assertEqual(len(self.calls),1)
 def test_fast_transient_failure_can_retry_but_only_twice(self):
  for fail in (kc.requests.exceptions.Timeout('fast'),kc.requests.exceptions.ConnectionError('fast')):
   response=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'balance':100})
   with patch.object(self.client.session,'request',side_effect=[fail,response]) as transport,patch.object(io_budget.time,'sleep') as sleep:
    self.assertEqual(self.client._request('GET','/portfolio/balance',signed=False),{'balance':100})
    self.assertEqual(transport.call_count,2);sleep.assert_called_once_with(.25)
   with patch.object(self.client.session,'request',side_effect=fail) as transport,patch.object(io_budget.time,'sleep'):
    with self.assertRaises(kc.KalshiAPIError):self.client._request('GET','/portfolio/balance',signed=False)
    self.assertEqual(transport.call_count,2)
 def test_rate_limit_backoff_and_permanent_error(self):
  good=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'ok':True})
  for code,tries in ((429,2),(500,2),(401,1)):
   bad=SimpleNamespace(status_code=code,text='error')
   with patch.object(self.client.session,'request',side_effect=[bad,good]) as transport,patch.object(io_budget.time,'sleep'):
    if tries==2:self.assertEqual(self.client._request('GET','/portfolio/orders',signed=False),{'ok':True})
    else:
     with self.assertRaises(kc.KalshiAPIError):self.client._request('GET','/portfolio/orders',signed=False)
    self.assertEqual(transport.call_count,tries)
 def test_nested_budget_is_respected_and_late_success_discarded(self):
  def request(*a,**kw):
   self.assertLessEqual(kw['timeout'].total,.2);self.clock[0]+=.3
   return SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'balance':100})
  with patch.object(io_budget.time,'monotonic',side_effect=lambda:self.clock[0]),patch.object(self.client.session,'request',side_effect=request):
   with io_budget.get_budget(.2):
    with self.assertRaises(TimeoutError):self.client._request('GET','/portfolio/balance',signed=False)
 def test_no_automatic_retry_or_changed_timeout_for_writes(self):
  self.client.dry_run=False
  for method in ('POST','DELETE'):
   with patch.object(self.client.session,'request',side_effect=kc.requests.exceptions.Timeout('ambiguous')) as transport:
    with io_budget.get_budget(0):
     with self.assertRaises(kc.KalshiAPIError):self.client._request(method,'/portfolio/events/orders',signed=False)
    self.assertEqual(transport.call_count,1);self.assertEqual(transport.call_args.kwargs['timeout'],5)
 def test_expired_budget_starts_no_request(self):
  with patch.object(self.client.session,'request') as transport,io_budget.get_budget(0):
   with self.assertRaises(TimeoutError):self.client._request('GET','/portfolio/orders',signed=False)
   transport.assert_not_called()

class SnapshotDeadline(unittest.TestCase):
 def make(self,handler):
  made=[]
  class Client:
   def __init__(self,*a,**kw):self.session=Mock();made.append(self)
   def _request(self,method,path,**kw):return handler(path,kw['params'])
  f=ar.FreshAccountReads(SimpleNamespace(creds=None,base_url='https://example.invalid',timeout=5),factory=Client)
  self.addCleanup(f.close);return f
 @staticmethod
 def response(path,cash='50'):
  return {'/portfolio/positions':{'market_positions':[]},'/portfolio/orders':{'orders':[]},'/portfolio/balance':{'balance_dollars':cash}}[path]
 def test_stuck_worker_deadline_no_overlap_no_late_cache(self):
  release=threading.Event();calls=[];cash=['50']
  def handler(path,p):
   calls.append(path)
   if path.endswith('positions') and not release.is_set():release.wait(2)
   return self.response(path,cash[0])
  f=self.make(handler)
  try:
   with patch.object(ar,'ACCOUNT_READ_BUDGET_SECONDS',.08):
    began=time.monotonic()
    with self.assertRaisesRegex(TimeoutError,'deadline'):f.snapshot(0)
    self.assertLess(time.monotonic()-began,.3);self.assertEqual(len(calls),3);self.assertIsNone(f.last_timing)
    with self.assertRaisesRegex(TimeoutError,'draining'):f.snapshot(0)
    self.assertEqual(len(calls),3)
    release.set()
    ar.wait(f.pending,timeout=1)
    cash[0]='0'
    self.assertEqual(f.snapshot(0),([],[],D('0')));self.assertEqual(len(calls),6)
  finally:release.set()
 def test_parent_deadline_reaches_all_workers(self):
  seen=[]
  def handler(path,p):seen.append(io_budget.remaining());return self.response(path)
  f=self.make(handler)
  with io_budget.get_budget(.25):f.snapshot(0)
  self.assertEqual(len(seen),3);self.assertTrue(all(0<v<=.25 for v in seen))
 def test_slow_pagination_cannot_reset_budget_each_page(self):
  calls=[]
  def handler(path,p):
   calls.append((path,p))
   if path.endswith('positions'):
    time.sleep(.035)
    io_budget.timeout(1)
    return {'market_positions':[], 'cursor':str(len(calls))}
   return self.response(path)
  f=self.make(handler)
  with patch.object(ar,'ACCOUNT_READ_BUDGET_SECONDS',.08):
   started=time.monotonic()
   with self.assertRaises(TimeoutError):f.snapshot(2)
   self.assertLess(time.monotonic()-started,.3);self.assertIsNone(f.last_timing)
 def test_expired_parent_dispatches_no_workers(self):
  calls=[];f=self.make(lambda p,k:calls.append(p))
  with io_budget.get_budget(0):
   with self.assertRaises(TimeoutError):f.snapshot(2)
  self.assertEqual(calls,[])

class CadExchange(ZeroExchange):
 def __init__(self):super().__init__();self.routes=[]
 def balance(self,exchange_index=2):self.routes.append(exchange_index);return self.cash

class FreshCAD(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  for name in ('ARM_USDCAD_5CT','ARM_Z3_5CT'):(self.root/name).write_text('5')
  self.store=Store(self.root/'ledger.db');self.ex=CadExchange();self.now=1023
  self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
  self.s=dict(signal(ticker='KXUSDCAD15M-TEST'),asset='USDCAD',lane='cushion_z2',exchange_index=0,decision_ts=1020,close_ts=1620,remaining_minutes=10,z=2.6,price_ranges=[dict(start='0',end='1',step='.01')])
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def zero(self):
  self.assertEqual(self.e.attempt(copy.deepcopy(self.s)),'ack');self.assertEqual(self.e.reconcile(),[])
  r=self.store.get(self.s['ticker']);self.assertEqual(r['status'],'unfilled');return r
 def next(self,minute=1080,**kw):
  self.now=minute+3;return dict(self.s,decision_ts=minute,remaining_minutes=(1620-minute)/60,**kw)
 def test_later_fresh_signal_full_fill_keeps_history_route_size_and_cap(self):
  old=self.zero();s=self.next(signal_ask=.93,signal_bid=.92,z=2.8)
  self.assertNotIn(s['ticker'],self.e.entered_markets('cushion_z2',1080))
  self.assertIn(s['ticker'],self.e.entered_markets('premium_z3',1080))
  self.assertIsNone(self.e.retry_signal('USDCAD','cushion_z2',1080))
  self.ex.outcomes=['5'];self.assertEqual(self.e.attempt(s),'ack');self.e.reconcile()
  rows=self.store.attempts(s['ticker']);self.assertEqual(rows[0],old)
  self.assertEqual([r['status'] for r in rows],['unfilled','open'])
  self.assertEqual(rows[1]['requested_count'],'5');self.assertEqual(rows[1]['exchange_index'],0)
  self.assertEqual(D(rows[1]['favorite_limit']),D('.93'));self.assertTrue(all(r==0 for r in self.ex.routes))
  self.assertEqual(self.e.attempt(self.next(1140)),'Already attempted this market')
 def test_premium_no_side_retains_97cent_tick_cap(self):
  self.s.update(lane='premium_z3',side='no',signal_ask=.96,signal_bid=.95,z=3.5)
  self.ex.book=lambda _:dict(yes_dollars=[['.03','5']])
  self.zero();self.ex.outcomes=['5'];self.assertEqual(self.e.attempt(self.next()),'ack')
  r=self.ex.posted[-1];self.assertEqual(D(r['favorite_limit']),D('.97'));self.assertEqual(r['requested_count'],'5')
 def test_three_total_attempt_limit_across_minutes_and_restart(self):
  self.zero()
  for m in (1080,1140):self.e.attempt(self.next(m));self.e.reconcile()
  self.store.db.close();self.store=Store(self.root/'ledger.db');self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
  self.assertEqual(self.e.attempt(self.next(1200)),'Already attempted this market');self.assertEqual(len(self.ex.posted),3)
 def test_partial_ambiguous_unproven_zero_do_not_reopen(self):
  row=self.zero();s=self.next()
  for kw in (dict(filled='1'),dict(ever_filled='.01'),dict(status='unknown'),dict(retry_forbidden='ambiguous'),dict(zero_fill_confirmation={})):
   self.store.save(dict(row,**kw));self.assertEqual(self.e.attempt(copy.deepcopy(s)),'Already attempted this market')
  self.assertEqual(len(self.ex.posted),1)
 def test_fresh_z_price_time_and_depth_must_pass(self):
  self.zero()
  for kw in (dict(z=2.1),dict(signal_ask=.95),dict(signal_ask=.84,signal_bid=.83),dict(signal_bid=.85)):
   with self.assertRaises(ValueError):self.e.attempt(self.next(**kw))
  for levels in ([['.06','50']],[['.07','4.99']],[['.16','5']]):
   self.ex.book=lambda _,v=levels:dict(no_dollars=v)
   self.assertIn(self.e.attempt(self.next()),('Insufficient depth inside cap','Book weakened below floor'))
  self.assertEqual(len(self.ex.posted),1)
 def test_cannot_change_side_owner_route_expiry_or_tick_ladder(self):
  row=self.zero();s=self.next()
  for kw in (dict(side='no'),dict(lane='premium_z3'),dict(exchange_index=2),dict(close_ts=1680),dict(price_ranges=[]),dict(decision_ts=1081)):
   self.assertFalse(fresh_retry_matches(row,dict(s,**kw),self.now))
 def test_exchange_zero_proof_rechecked_not_just_local_record(self):
  self.zero();s=self.next();self.ex.fill_map['oid1']=[{'fill_id':'late'}]
  with self.assertRaisesRegex(RuntimeError,'has fills'):self.e.attempt(s)
  self.ex.fill_map['oid1']=[];self.ex.order_map['oid1']['taker_fees_dollars']='.01'
  with self.assertRaisesRegex(RuntimeError,'charged cash'):self.e.attempt(s)
  self.assertEqual(len(self.ex.posted),1)
 def test_account_failures_cash_risk_halt_and_expiry_block(self):
  self.zero();s=self.next();self.ex.cash=D(0)
  self.assertIn('funding',self.e.attempt(copy.deepcopy(s)));self.ex.cash=D(50)
  self.ex.pos=[dict(ticker='KXBTC15M-X',position_fp='25')]
  self.assertEqual(self.e.attempt(copy.deepcopy(s)),'25-contract cap');self.ex.pos=[]
  (self.root/'HALT_USDCAD').touch();self.assertIn('halted',self.e.attempt(copy.deepcopy(s)));(self.root/'HALT_USDCAD').unlink()
  self.now=1105.1;self.assertEqual(self.e.attempt(copy.deepcopy(s)),'Already attempted this market')
  self.now=1083;self.ex.account_snapshot=Mock(side_effect=TimeoutError('deadline'))
  with self.assertRaises(TimeoutError):self.e.attempt(copy.deepcopy(s))
  self.assertEqual(len(self.ex.posted),1)
 def test_only_wti_and_cad_new_minutes_are_enabled(self):
  row=self.zero()
  for a in ('BTC','ETH','SOL','XRP','NATGAS','EURUSD','COPPER','AUDUSD'):
   self.assertFalse(fresh_retry_candidate(dict(row,asset=a,ticker='KX'+a+'15M-TEST'),1080,1083,'cushion_z2'))

if __name__=='__main__':unittest.main()
