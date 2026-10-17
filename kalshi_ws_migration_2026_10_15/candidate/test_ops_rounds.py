"""Timing/ordering and failure tests; all transport is simulated."""
import copy,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch,Mock
import runner,engine,execution_retry,market_data,io_budget
from test_engine import Tests,signal,FakeExchange
from test_bounded_reads import FreshCAD
from test_zero_fill import Exchange as ZeroExchange
from lanes import ACTIVE_ASSETS,Z2,Z3,lane_for,route_for
from kalshi_client import KalshiAPIError

class Timing(unittest.TestCase):
 def test_status_write_crossing_first_scan_boundary_does_not_add_second(self):
  self.assertEqual(runner.sleep_delay(6000.27,set(),{},ACTIVE_ASSETS,{}),.01)
 def test_blocked_premium_does_not_cause_busy_loop_before_original_due(self):
  self.assertAlmostEqual(runner.sleep_delay(6000.6,set(),{(Z2,'BTC'):6000.3},('BTC',),{(Z2,'BTC'):1}),.7)
 def test_preliminary_book_uses_same_price_and_tick_cap_as_engine(self):
  for lane,ask,price in [(Z2,.94,.94),(Z3,.97,.975)]:
   for side in ('yes','no'):
    s=dict(signal(side),lane=lane,signal_ask=ask,price_ranges=[dict(start='0',end='1',step='.01')])
    key='no_dollars' if side=='yes' else 'yes_dollars'
    with patch.object(market_data.time,'time',return_value=1003),patch.object(market_data,'get',return_value=dict(orderbook_fp={key:[[str(1-price),'5']]})):
     r=market_data.refresh_book(s)
    self.assertEqual(r['status'],'book_outside_limit');self.assertEqual(r['book_execution_cap'],float(engine.limit_price(s)[1]))
 def test_book_recovery_within_original_cap_remains_eligible(self):
  s=dict(signal(),signal_ask=.94)
  books=[dict(orderbook_fp=dict(no_dollars=[['.06','5']])),dict(orderbook_fp=dict(no_dollars=[['.061','5']]))]
  with patch.object(market_data.time,'time',return_value=1003),patch.object(market_data,'get',side_effect=books):
   first=market_data.refresh_book(s);second=market_data.refresh_book(first)
  self.assertEqual(first['status'],'book_outside_limit');self.assertEqual(second['status'],'paper_fill');self.assertEqual(second['signal_ask'],.94)
 def test_first_scan_wakeup_does_not_wait_whole_second(self):
  self.assertAlmostEqual(runner.sleep_delay(6000.01,set(),{},ACTIVE_ASSETS),.24)
  self.assertAlmostEqual(runner.sleep_delay(5999.95,set(),{},ACTIVE_ASSETS),.30)
 def test_pending_checks_keep_one_second_spacing(self):
  done={(n,a) for n in (Z2,Z3) for a in ACTIVE_ASSETS}-{(Z2,'BTC')}
  self.assertAlmostEqual(runner.sleep_delay(6000.6,done,{(Z2,'BTC'):6000.3},ACTIVE_ASSETS),.7)
 def test_primary_headstart_unchanged(self):
  self.assertFalse(runner.scan_ready(Z3,'BTC',set(),{(Z2,'BTC'):7},8))
  self.assertTrue(runner.scan_ready(Z3,'BTC',set(),{(Z2,'BTC'):8},8))
 def test_public_late_response_discarded(self):
  clock=[0.];response=Mock();response.json.side_effect=lambda:clock.__setitem__(0,4) or {'markets':[]}
  with patch.object(io_budget.time,'monotonic',side_effect=lambda:clock[0]),patch.object(market_data._PUBLIC_SESSION,'get',return_value=response),io_budget.get_budget(3):
   with self.assertRaises(TimeoutError):market_data.get(market_data.BASE+'/markets')

class ReadRecovery(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 def test_temporary_account_failure_then_fresh_read_can_submit(self):
  reads=[]
  def account(index):
   reads.append(index)
   if len(reads)==1:raise TimeoutError('temporary')
   return [],[],D(50)
  self.ex.account_snapshot=account;s=signal()
  self.assertEqual(runner.attempt_with_read_retry(self.engine,s),runner.READ_RETRY)
  self.assertFalse(self.store.all());self.assertFalse(self.ex.posted)
  self.now+=1
  self.assertEqual(runner.attempt_with_read_retry(self.engine,s),'ack');self.assertEqual(reads,[2,2])
  self.assertEqual(len(self.ex.posted),1)
 def test_temporary_final_book_failure_can_retry_with_new_account(self):
  self.ex.book=Mock(side_effect=[KalshiAPIError(503,'temporary','GET','/markets/T/orderbook'),dict(no_dollars=[['.08','5']])]);self.ex.account_snapshot=Mock(return_value=([],[],D(50)))
  self.assertEqual(runner.attempt_with_read_retry(self.engine,signal()),runner.READ_RETRY)
  self.assertEqual(runner.attempt_with_read_retry(self.engine,signal()),'ack');self.assertEqual(self.ex.account_snapshot.call_count,2)
 def test_zero_balance_on_recovery_still_blocks(self):
  self.ex.account_snapshot=Mock(side_effect=[TimeoutError('temporary'),([],[],D(0))])
  runner.attempt_with_read_retry(self.engine,signal())
  self.assertIn('funding required',runner.attempt_with_read_retry(self.engine,signal()));self.assertFalse(self.ex.posted)
 def test_only_transient_gets_are_recoverable(self):
  for method,code,allowed in [('GET',0,True),('GET',429,True),('GET',503,True),('GET',401,False),('GET',404,False),('POST',503,False),('DELETE',429,False)]:
   self.assertEqual(runner.transient_read_error(KalshiAPIError(code,'x',method,'/path')),allowed)
  self.assertFalse(runner.transient_read_error(ValueError('malformed')))
 def test_ambiguous_post_never_becomes_read_retry(self):
  self.ex.raise_post=True
  self.assertEqual(runner.attempt_with_read_retry(self.engine,signal()),'unknown')
  self.assertEqual(runner.attempt_with_read_retry(self.engine,signal()),'Already attempted this market');self.assertEqual(len(self.ex.posted),1)
 def test_retry_signal_checks_cannot_exceed_bounds(self):
  self.assertTrue(execution_retry.retry_allowed('paper_fill',runner.READ_RETRY,1,4,False))
  self.assertFalse(execution_retry.retry_allowed('paper_fill',runner.READ_RETRY,24,4,False))
  self.assertFalse(execution_retry.retry_allowed('paper_fill',runner.READ_RETRY,1,25.01,False))
 def test_timing_receipts_do_not_change_order_payload_or_intent(self):
  self.assertEqual(self.engine.attempt(signal()),'ack');row=self.store.get(signal()['ticker']);s=row['signal']
  self.assertLessEqual(s['execution_started_ts'],s['execution_book_ts']);self.assertLessEqual(s['execution_verified_ts'],s['execution_submit_ts']);self.assertLessEqual(s['execution_submit_ts'],s['execution_ack_ts'])
  self.assertGreaterEqual(s['execution_reserve_ms'],0);self.assertEqual(row['requested_count'],'5');self.assertEqual(row['favorite_limit'],'0.93')

class SettlementWork(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 acknowledged=Tests.acknowledged
 def test_known_terminal_open_fill_is_not_polled_before_expiry(self):
  r=self.acknowledged(fill='5');self.engine.reconcile();old=self.store.get(r['ticker'])
  self.ex.order=Mock(side_effect=AssertionError('redundant order GET'));self.ex.fills=Mock(side_effect=AssertionError('redundant fills GET'))
  self.assertEqual(self.engine.reconcile(),[]);self.assertEqual(self.store.get(r['ticker']),old)
 def test_full_order_and_fill_recheck_returns_at_settlement(self):
  r=self.acknowledged(fill='5');self.engine.reconcile();self.ex.order=Mock(wraps=self.ex.order);self.ex.fills=Mock(wraps=self.ex.fills);self.now=1400
  self.assertEqual(self.engine.reconcile(),[]);self.ex.order.assert_called_once();self.ex.fills.assert_called_once();self.assertEqual(self.store.get(r['ticker'])['status'],'settled')
 def test_legacy_open_without_proof_is_checked(self):
  r=self.acknowledged(fill='5');self.engine.reconcile();row=self.store.get(r['ticker']);row.pop('terminal_fill_verified');self.store.save(row)
  self.ex.order=Mock(wraps=self.ex.order);self.engine.reconcile();self.ex.order.assert_called_once()
 def test_early_open_skip_still_reserves_position_risk(self):
  self.acknowledged(fill='5');self.engine.reconcile()
  self.assertEqual(self.engine.attempt(signal(ticker='KXBTC15M-OTHER')),'Asset already has exposure')
 def test_pending_order_never_skipped_and_fill_index_lag_blocks(self):
  self.acknowledged(fill='5');self.ex.fs=[];self.engine.reconcile();self.assertTrue(self.store.pending())
  self.assertEqual(self.engine.attempt(signal(ticker='KXETH15M-OTHER')),'Order reconciliation pending')
 def test_query_projection_sees_external_write_and_does_not_cache_ownership(self):
  self.store.save(dict(ticker='history',status='settled'));self.assertEqual(self.store.entry_records(),[('history',None)])
  other=engine.Store(self.store.path)
  try:other.save(dict(ticker='new',status='unknown'))
  finally:other.db.close()
  self.assertTrue(self.store.pending());self.assertIn('new',self.engine.entered_markets(Z2,1000))

class EveryAssetRetry(unittest.TestCase):
 def test_both_lanes_each_asset_fresh_signal_and_total_limit(self):
  for asset in ACTIVE_ASSETS:
   for lane in (Z2,Z3):
    with self.subTest(asset=asset,lane=lane),tempfile.TemporaryDirectory() as tmp:
     root=Path(tmp);store=engine.Store(root/'state.db');self.addCleanup(store.db.close)
     for n in ('ARM_5CT','ARM_Z3_5CT','ARM_WTI_5CT','ARM_USDCAD_5CT'):(root/n).write_text('5')
     (root/'ARM_NONCRYPTO_5CT').write_text(json.dumps(dict(version=1,contracts=5,assets=['COPPER','NATGAS','EURUSD'])))
     clock=[1023.];ex=ZeroExchange();ex.outcomes=['0','0','0'];ex.balance=lambda exchange_index=2:D(100)
     eng=engine.Engine(ex,store,root,live=True,clock=lambda:clock[0]);s=dict(signal(ticker='KX'+asset+'15M-TEST'),lane=lane,decision_ts=1020,close_ts=1620,remaining_minutes=10,z=5,exchange_index=route_for(dict(asset=asset)))
     if lane==Z3:s.update(signal_ask=.96,signal_bid=.95);ex.book=lambda _:dict(no_dollars=[['.04','5']],yes_dollars=[['.04','5']])
     for minute in (1020,1080,1140):
      clock[0]=minute+3;s=dict(s,decision_ts=minute,remaining_minutes=(1620-minute)/60)
      self.assertNotIn(s['ticker'],eng.entered_markets(lane,minute))
      self.assertEqual(eng.attempt(s),'ack');self.assertEqual(eng.reconcile(),[])
     clock[0]=1203;s=dict(s,decision_ts=1200,remaining_minutes=7)
     self.assertIn(s['ticker'],eng.entered_markets(lane,1200));self.assertEqual(eng.attempt(s),'Already attempted this market');self.assertEqual(len(ex.posted),3)
 def test_retired_asset_and_inconsistent_identity_cannot_reopen(self):
  self.assertNotIn('COPPER',execution_retry.FRESH_RETRY_ASSETS)
  f=FreshCAD();f.setUp()
  try:
   f.zero();r=f.store.get(f.s['ticker']);bad=copy.deepcopy(r);bad['signal']['ticker']='other'
   self.assertFalse(execution_retry.fresh_retry_candidate(bad,1080,1083,Z2))
  finally:f.tearDown()

if __name__=='__main__':unittest.main()
