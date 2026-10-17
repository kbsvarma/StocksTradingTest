import unittest,math
from replay import simulate,sale,prepare
def fixture():
 r=dict(ticker='test',asset='BTC',lane='cushion_z2',won=0,entry_z=2,entered=.1,close=90,quantity=5,cost=4.5,entry_fee=.03,entry_friction=0,hold_net=-4.53,side=1)
 books=[[t*1000,0,0,[[8000,500]],[[1500,500]],0,0] for t in range(91)]
 r.update(b=dict(books=books),bt=[b[0] for b in books],z={t:{'5hz':.5} for t in range(1,90)})
 return r
class Tests(unittest.TestCase):
 def test_fee(self):
  r=sale([[8000,500]],5);self.assertEqual(r['proceeds'],3.944);self.assertAlmostEqual(r['fee'],.056)
 def test_levels(self):self.assertAlmostEqual(sale([[8000,100],[7000,400]],5)['gross'],3.6)
 def test_insufficient(self):self.assertIsNone(sale([[8000,499]],5))
 def test_held_no_side(self):
  r=fixture();r['side']=-1;self.assertEqual(simulate(r,1,1,0,0)['execution']['gross'],.75)
 def test_interval_and_later_book(self):
  for interval in [1,10,30,60]:
   r=simulate(fixture(),interval,1,0,0);self.assertEqual(r['trigger'],interval);self.assertEqual(r['book_after'],interval+1)
 def test_persistence_seconds(self):
  self.assertEqual(simulate(fixture(),1,1,0,30)['trigger'],31)
  self.assertEqual(simulate(fixture(),10,1,0,30)['trigger'],40)
 def test_persistence_recovery_reset(self):
  r=fixture();r['z'][10]['5hz']=3;self.assertEqual(simulate(r,1,1,0,30)['trigger'],41)
 def test_stale_reset(self):
  r=fixture();r['z'][10]={};self.assertEqual(simulate(r,1,1,0,30)['trigger'],41)
 def test_grace(self):self.assertEqual(simulate(fixture(),1,1,30,0)['trigger'],31)
 def test_expiry(self):
  r=fixture();r['close']=2;self.assertFalse(simulate(r,1,1,0,0)['exit'])
 def test_pre_book_worse(self):
  r=fixture();r['b']['books'][1][3]=[[5000,500]];self.assertEqual(simulate(r,1,1,0,0)['execution']['gross'],2.5)
 def test_post_book_worse(self):
  r=fixture();r['b']['books'][2][3]=[[5000,500]];self.assertEqual(simulate(r,1,1,0,0)['execution']['gross'],2.5)
 def test_frict_not_ignored(self):
  r=simulate(fixture(),1,1,0,0);self.assertAlmostEqual(r['net'],3.944-.05-4.53)
 def test_phase(self):self.assertEqual(simulate(fixture(),10,1,0,0,phase=3)['trigger'],3)
 def test_fresh_anchor_and_no_future(self):
  r=dict(ticker='anchor',entered=60.1,t=60,close=120,side=1,entry_sigma=.01,reference=100,entry_spot=102)
  cf={'5hz':[[59990,59900,100],[61500,61400,200]],'1hz':[[59990,59900,100],[61500,61400,200]]}
  q=prepare(r,dict(books=[],cf=cf),{})
  self.assertAlmostEqual(q['z'][61]['5hz'],.02/(.01*math.sqrt(59/60)))
  self.assertGreater(q['z'][62]['5hz'],100)
  self.assertNotIn('5hz',q['z'][64])
if __name__=='__main__':unittest.main()
