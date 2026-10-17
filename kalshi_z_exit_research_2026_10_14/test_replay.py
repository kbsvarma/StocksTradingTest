import unittest
from decimal import Decimal as D
from replay import simulate,sale,tick_down,threshold

class ExitTests(unittest.TestCase):
 def row(self):
  return dict(ticker='TEST',asset='BTC',lane='cushion_z2',entered=1,close=600,settled=615,won=1,quantity=5,cost=4.5,entry_fee=.0315,entry_friction=0,hold_net=.4685,entry_z=2,entry_gate=2,side=1,multiplier=1,ranges=[dict(start='0',end='1',step='.01')],path=[dict(t=t,z=z,z_frozen_sigma=z,bid_close=.7,bid_next_open=.69,bid_delayed_open=.6) for t,z in [(60,1.4),(120,1.3),(180,2.2),(240,1.2),(300,1.1)]])
 def config(self,**kw):return dict(family='gate_drop',level=.5,delay=0,persist=1,**kw)
 def test_next_open_bid_haircut(self):
  x=simulate(self.row(),self.config());self.assertEqual(x['exit_price'],.68);self.assertEqual(x['exit_ts'],61)
 def test_requires_two_consecutive(self):
  c=self.config();c['persist']=2;x=simulate(self.row(),c);self.assertEqual(x['exit_ts'],121)
 def test_delay_from_actual_entry(self):
  c=self.config();c['delay']=120;x=simulate(self.row(),c);self.assertEqual(x['exit_ts'],241)
 def test_missing_resets_persistence(self):
  r=self.row();r['path'][1]['z']=None;c=self.config();c['persist']=2;x=simulate(r,c);self.assertEqual(x['exit_ts'],301)
 def test_no_postexpiry_sell(self):
  r=self.row();r['close']=61;x=simulate(r,self.config());self.assertFalse(x['exit']);self.assertEqual(x['net'],r['hold_net'])
 def test_no_zero_bid_fill(self):
  r=self.row()
  for x in r['path']:x['bid_next_open']=0
  x=simulate(r,self.config());self.assertFalse(x['exit']);self.assertGreater(x['failed_quotes'],0)
 def test_delayed_quote_not_trigger_quote(self):
  x=simulate(self.row(),self.config(),latency=60);self.assertEqual(x['exit_price'],.59);self.assertEqual(x['exit_ts'],121)
 def test_sale_fee_and_rounding(self):
  proceeds,fee=sale(D(5),D('.87'),1);self.assertEqual(proceeds,D('4.3104'));self.assertEqual(fee,D('.0396'))
 def test_no_threshold_lookahead(self):
  r=self.row();c=self.config();self.assertEqual(threshold(r,c),1.5);r['entry_z']=6;self.assertEqual(threshold(r,c),1.5)
 def test_entry_drop_is_fixed_anchor(self):
  r=self.row();c=self.config();c['family']='entry_drop';self.assertEqual(threshold(r,c),1.5)
 def test_strict_below(self):
  r=self.row()
  for x in r['path']:x['z']=1.5
  self.assertFalse(simulate(r,self.config())['exit'])
 def test_no_tick_rounds_to_lower_proceeds(self):
  r=self.row();r['side']=-1;self.assertEqual(tick_down(D('.695'),r),D('.69'))
 def test_same_entry_cost_kept(self):
  r=self.row();x=simulate(r,self.config());pr,_=sale(D(5),D('.68'),1);self.assertAlmostEqual(x['net'],float(pr)-r['cost']-r['entry_fee'])
if __name__=='__main__':unittest.main()
