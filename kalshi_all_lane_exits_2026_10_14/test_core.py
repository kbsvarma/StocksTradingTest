import unittest,numpy as np
from core import *
def row():return dict(ticker='x',asset='ETH',lane='cushion_z2',won=0,quantity=5,hold_net=-4.53,settled=1000,entered=.1,entry_z=2.5,entry_gate=2,cost=4.5,entry_fee=.03,entry_friction=0)
def config(**changes):return dict(id='test',family='drop',value=1,grace=0,persist=0,**changes)
def pp():return dict(t=np.arange(10,100,10),z=np.full(9,.5),bc=np.full(9,.7),low=np.full(9,.6),high=np.full(9,.8),opening=np.full(9,.7),exit_t=np.arange(11,101,10))
class Tests(unittest.TestCase):
 def test_fee(self):self.assertAlmostEqual(fee_sale(5,.8)[0],3.944)
 def test_fee_fractional(self):self.assertGreater(fee_sale(3.57,.65)[1],0)
 def test_first(self):self.assertEqual(first_trigger(row(),config(),pp()),0)
 def test_confirmation_elapsed(self):
  c=config();c['persist']=30;self.assertEqual(first_trigger(row(),c,pp()),3)
 def test_recovery(self):
  p=pp();p['z'][2]=3;c=config();c['persist']=30;self.assertEqual(first_trigger(row(),c,p),6)
 def test_missing_resets(self):
  p=pp();p['z'][2]=np.nan;c=config();c['persist']=30;self.assertEqual(first_trigger(row(),c,p),6)
 def test_grace(self):
  c=config();c['grace']=30;self.assertEqual(first_trigger(row(),c,pp()),3)
 def test_no_breach(self):
  p=pp();p['z'][:]=4;self.assertIsNone(first_trigger(row(),config(),p))
 def test_no_reentry_or_added_entry_fee(self):
  x=exit_result(row(),config(),pp(),'low');self.assertAlmostEqual(x['net'],fee_sale(5,.6)[0]-.05-4.53)
 def test_envelope_order(self):
  self.assertLess(exit_result(row(),config(),pp(),'low')['net'],exit_result(row(),config(),pp(),'high')['net'])
 def test_zero_bid(self):
  p=pp();p['low'][:]=0;self.assertFalse(exit_result(row(),config(),p,'low')['exit'])
 def test_unknown_not_fill(self):
  p=pp();p['low'][:]=np.nan;x=exit_result(row(),config(),p,'low');self.assertTrue(x['quote_missing']);self.assertFalse(x['exit'])
 def test_no_complement(self):
  m=dict(candles={60:dict(bl=.2,bh=.7,al=.3,ah=.8)});self.assertAlmostEqual(quote(m,60,-1,'l'),.2);self.assertAlmostEqual(quote(m,60,-1,'h'),.7)
 def test_gate(self):
  c=config();c.update(family='gate',value=.5);self.assertEqual(threshold(row(),c),1.5)
 def test_fraction(self):
  c=config();c.update(family='fraction',value=.5);self.assertEqual(threshold(row(),c),1.25)
 def test_price(self):
  c=config();c.update(family='price',value=.1);self.assertAlmostEqual(threshold(row(),c),.8)
 def test_grid(self):self.assertEqual(len(rules()),320);self.assertEqual(len({c['id'] for c in rules()}),320)
if __name__=='__main__':unittest.main()
