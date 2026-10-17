import unittest
from unittest.mock import patch
import market_data
from execution_retry import retry_allowed
class Retry(unittest.TestCase):
 def test_never_resubmit_reserved_order(self):
  for status in ['ack','unknown','rejected','unfilled','open','settled']:
   self.assertFalse(retry_allowed('book_outside_limit',status,1,5,True))
 def test_bounded_prepost_only(self):
  self.assertTrue(retry_allowed('book_outside_limit','',1,4,False))
  self.assertTrue(retry_allowed('paper_fill','Insufficient depth inside cap',1,4,False))
  self.assertFalse(retry_allowed('paper_fill','Eight-contract cap',1,4,False))
  self.assertFalse(retry_allowed('book_outside_limit','Book weakened below floor',1,4,False))
  self.assertFalse(retry_allowed('book_outside_limit','',8,4,False))
  self.assertFalse(retry_allowed('book_outside_limit','',1,26,False))
 def test_refresh_same_signal_later_book(self):
  s=dict(decision_ts=1000,ticker='TEST',side='yes',signal_ask=.92,z=2.1,close_ts=1300)
  books=[dict(orderbook_fp=dict(no_dollars=[['.05','5']])),dict(orderbook_fp=dict(no_dollars=[['.075','5']]))]
  with patch.object(market_data,'get',side_effect=books),patch.object(market_data.time,'time',return_value=1005):
   first=market_data.refresh_book(s);second=market_data.refresh_book(first)
  self.assertEqual(first['status'],'book_outside_limit');self.assertEqual(second['status'],'paper_fill')
  self.assertEqual(second['z'],2.1);self.assertEqual(second['signal_ask'],.92);self.assertEqual(second['decision_ts'],1000)
  self.assertEqual(second['count'],5);self.assertEqual(second['book_available_count'],5)
 def test_expired_does_not_read_book(self):
  s=dict(decision_ts=1000,ticker='TEST',side='yes',signal_ask=.92)
  with patch.object(market_data,'get') as get,patch.object(market_data.time,'time',return_value=1036):
   self.assertEqual(market_data.refresh_book(s)['status'],'signal_too_old');get.assert_not_called()

class ObserverChecks(unittest.TestCase):
 def test_observer_cap_matches_engine(self):
  from observe_execution import exact_cap,book_stats
  from engine import limit_price
  from test_engine import signal
  for side in ('yes','no'):
   for ask in (.855,.89,.919,.929):
    s=signal(side);s['signal_ask']=ask
    self.assertAlmostEqual(exact_cap(s),float(limit_price(s)[1]))
  self.assertTrue(book_stats([['.08','5']],.93,5)['enough'])
  self.assertFalse(book_stats([['.08','4']],.93,5)['enough'])
 def test_recovery_keeps_price_cap(self):
  from observe_execution import book_stats
  self.assertFalse(book_stats([['.05','500']],.929,5)['enough'])
