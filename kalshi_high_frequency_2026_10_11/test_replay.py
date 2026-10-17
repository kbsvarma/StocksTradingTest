import unittest,copy
from pairs import make_trade,portfolio,quote,fee,signal_quote
from public import ts
def market(asset,result):
 c={t:dict(bc='.49',ac='.50',bo='.49',ao='.50',v='100') for t in range(60,961,60)}
 f={r:dict(prich=(.7 if asset=='BTC' else .3),z=(.8 if asset=='BTC' else -.8)) for r in range(14,0,-1)}
 return dict(ticker=asset,asset=asset,close=900,start=0,settled=910,result=result,cohort='selection',candles=c,new_features=f)
class Tests(unittest.TestCase):
 def test_fee_and_fractional_timestamp(self):
  self.assertAlmostEqual(fee(.5),.0175)
  self.assertEqual(ts('2026-10-04T00:15:07.21711Z'),1791072907)
 def test_no_side_is_complemented_bid_and_ask(self):
  self.assertEqual(quote(market('BTC',1),120,-1),(.5,.51))
 def test_opposite_crypto_bets_can_both_lose(self):
  r,_=make_trade(market('BTC',-1),market('ETH',1),('rich',0,'12','settlement'))
  self.assertEqual(r['exit'],0);self.assertLess(r['pnl'],-1)
 def test_opposite_crypto_bets_can_both_win(self):
  r,_=make_trade(market('BTC',1),market('ETH',-1),('rich',0,'12','settlement'))
  self.assertEqual(r['exit'],2);self.assertGreater(r['pnl'],.9)
 def test_no_atomic_fill_assumption_when_second_quote_missing(self):
  e=market('ETH',1);del e['candles'][240]
  r,why=make_trade(market('BTC',1),e,('rich',0,'12','settlement'))
  self.assertIsNone(r);self.assertEqual(why['paired_execution_unavailable'],1)
 def test_price_cap_checked_before_counting_pair(self):
  b=market('BTC',1);b['candles'][240]['ao']='.55'
  r,_=make_trade(b,market('ETH',1),('rich',0,'12','settlement'));self.assertIsNone(r)
 def test_extra_cost_does_not_reselect_entry(self):
  b,e=market('BTC',1),market('ETH',1);cfg=('rich',0,'12','2min')
  a,_=make_trade(b,e,cfg,False,.01);c,_=make_trade(b,e,cfg,False,.02)
  self.assertEqual(a['decision'],c['decision']);self.assertEqual(a['direction'],c['direction'])
  self.assertAlmostEqual(a['pnl']-c['pnl'],.04)
 def test_exit_precedes_expiry_and_follows_trigger(self):
  r,_=make_trade(market('BTC',1),market('ETH',1),('rich',0,'12','2min'))
  self.assertEqual(r['exit_decision'],300);self.assertEqual(r['exit_ts'],303);self.assertEqual(r['legs'],4)
 def test_delayed_exit_uses_later_completed_bid(self):
  b=market('BTC',1);b['candles'][360]['bc']='.40'
  r,_=make_trade(b,market('ETH',1),('rich',0,'12','2min'),True)
  self.assertEqual(r['exit_ts'],360);self.assertEqual(r['exit_prices'][0],.4)
 def test_forward_signal_not_revised_historical_candle(self):
  b=market('BTC',1);b['cohort']='forward';b['new_features'][12].update(b=.2,a=.21,received=183)
  self.assertEqual(signal_quote(b,180,1),(.2,.21))
  b['new_features'][12]['received']=220;self.assertIsNone(signal_quote(b,180,1))
 def test_unsettled_capital_is_not_reused(self):
  r,_=make_trade(market('BTC',1),market('ETH',1),('rich',0,'12','settlement'))
  rows=[]
  for i in range(20):
   q=copy.deepcopy(r);q['entered']+=i;q['exit_ts']=10000;rows.append(q)
  done,sk,acct=portfolio(rows)
  self.assertEqual(len(done),12);self.assertEqual(sk['risk_or_cash'],8);self.assertLess(acct['peak_committed'],25)
if __name__=='__main__':unittest.main()
