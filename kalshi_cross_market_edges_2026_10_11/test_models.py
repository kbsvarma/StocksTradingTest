import unittest
from tennis_markov import Model,fit
from weather_bounds import side_for_bound
from core import fee,qte

class ProbabilityChecks(unittest.TestCase):
 def tearDown(self):Model(.6,.6).clear()
 def test_symmetry_and_total_probability(self):
  for p in (.5,.6,.65,.7):
   m=Model(p,p)
   self.assertAlmostEqual((m.match(0,0,0)+m.match(0,0,1))/2,.5)
   for s in (0,1):
    self.assertAlmostEqual(sum(m.setdist(0,0,s)),1)
    self.assertAlmostEqual(m.tb(0,0,s),.5)
    self.assertAlmostEqual(m.current(0,0,0,0,0,0,s),m.match(0,0,s))
   m.clear()
 def test_deuce_advantage_and_terminal(self):
  m=Model(.6,.65);d=.6**2/(.6**2+.4**2)
  self.assertAlmostEqual(m.game(3,3,0),d)
  self.assertAlmostEqual(m.game(4,3,0),.6+.4*d)
  self.assertAlmostEqual(m.game(3,4,0),.6*d)
  self.assertEqual(m.game(4,0,0),1)
  self.assertEqual(m.game(0,4,0),0)
  self.assertEqual(m.current(2,0,0,0,0,0,0),1)
 def test_fitting_and_monotonicity(self):
  for target in (.1,.3,.5,.7,.9):
   m=fit(.6,target)
   self.assertAlmostEqual((m.match(0,0,0)+m.match(0,0,1))/2,target,places=4)
   self.assertGreater(m.current(0,0,2,2,3,0,0),m.current(0,0,2,2,0,3,0))
   m.clear()
 def test_set_server_parity(self):
  m=Model(.6,.6)
  self.assertEqual(m.setdist(6,4,0),(1.,0.,0.,0.))
  self.assertEqual(m.setdist(4,6,1),(0.,0.,0.,1.))
  d=m.setdist(6,6,0);self.assertEqual(d[0]+d[2],0);self.assertAlmostEqual(d[1]+d[3],1)

class AccountingChecks(unittest.TestCase):
 def test_fees_and_complement(self):
  self.assertAlmostEqual(fee(.5,1,1),.0175)
  self.assertAlmostEqual(fee(.939,1,1),.0041)
  c={'yes_bid':{'close_dollars':'.35'},'yes_ask':{'close_dollars':'.37'}}
  self.assertEqual(qte(c,'no'),(.63,.65))
 def test_bound_strictness(self):
  m={'strike_type':'greater','floor_strike':70}
  self.assertIsNone(side_for_bound(m,70));self.assertEqual(side_for_bound(m,71),'yes')
  m={'strike_type':'between','floor_strike':70,'cap_strike':71}
  self.assertIsNone(side_for_bound(m,71));self.assertEqual(side_for_bound(m,72),'no')
 def test_nested_threshold_payoffs(self):
  for x in range(-2,5):
   self.assertGreaterEqual(int(x>0)+int(not x>2),1)
 def test_at_most_one_sports_winner(self):
  for n in (2,3):
   for winner in range(-1,n):
    self.assertGreaterEqual(sum(i!=winner for i in range(n)),n-1)

if __name__=='__main__':unittest.main()
