import unittest,datetime
from replay import short,fee,portfolio,ts
class ReplayTests(unittest.TestCase):
 def fixture(self):
  close=datetime.datetime(2026,9,20,12,tzinfo=datetime.timezone.utc).timestamp()
  m={'ticker':'TEST','event_ticker':'EVENT','open_time':datetime.datetime.fromtimestamp(close-900,datetime.timezone.utc).isoformat(),'close_time':datetime.datetime.fromtimestamp(close,datetime.timezone.utc).isoformat(),'settlement_ts':datetime.datetime.fromtimestamp(close+300,datetime.timezone.utc).isoformat(),'result':'yes'}
  cs=[{'t':int(close-i*60),'bc':.89,'ac':.90} for i in range(14,0,-1)]
  return m,cs
 def test_uses_later_quote(self):
  m,c=self.fixture();r=short(m,c,'quote85',1);self.assertEqual(r['entry_ts']-r['signal_ts'],60)
 def test_future_result_does_not_select_entry(self):
  m,c=self.fixture();a=short(m,c,'quote85',1);m['result']='no';b=short(m,c,'quote85',1)
  for k in ['entry_ts','signal_ts','entry','side']:self.assertEqual(a[k],b[k])
  self.assertAlmostEqual(a['net']-b['net'],2)
 def test_no_interpolation_missing_execution(self):
  m,c=self.fixture();c=[r for r in c if r['t']>datetime.datetime.fromisoformat(m['close_time']).timestamp()-600];self.assertIsNotNone(short(m,c,'quote85',1))
  self.assertIsNone(short(m,c[:1],'quote85',1))
 def test_zero_book_rejected(self):
  m,c=self.fixture()
  for r in c:r['bc']=0
  self.assertIsNone(short(m,c,'quote85',1))
 def test_fractional_timestamp(self):self.assertAlmostEqual(ts('2026-09-25T15:00:18.1493Z'),ts('2026-09-25T15:00:18.149300Z'))
 def test_fee_two_contracts(self):self.assertAlmostEqual(fee(.9,1),.0126);self.assertEqual(fee(.9,0),0)
 def test_pending_blocks_second_position(self):
  m,c=self.fixture();r=short(m,c,'quote85',1);other=dict(r,ticker='SECOND',event='SECOND',entry_ts=r['entry_ts']+60)
  p=portfolio([r,other]);self.assertEqual(p['accepted'],1);self.assertEqual(p['skips']['unsettled_position'],1)
 def test_losses_cannot_spend_through_day_budget(self):
  m,c=self.fixture();m['result']='no';r=short(m,c,'quote85',1)
  rows=[dict(r,ticker=str(i),event=str(i),entry_ts=r['entry_ts']+i*1800,settle_ts=r['settle_ts']+i*1800) for i in range(10)]
  p=portfolio(rows);self.assertEqual(p['accepted'],2);self.assertGreaterEqual(p['ending_cash'],96)
 def test_missing_settlement_blocks_portfolio(self):
  m,c=self.fixture();r=short(m,c,'quote85',1);r['settle_ts']=None;self.assertEqual(portfolio([r])['accepted'],0)
if __name__=='__main__':unittest.main()
