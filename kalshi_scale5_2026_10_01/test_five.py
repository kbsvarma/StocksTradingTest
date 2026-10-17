import unittest
from decimal import Decimal as D
from engine import requested_count
from test_engine import Tests,signal
class Five(Tests):
 def test_five_requested(self):
  self.assertEqual(self.engine.attempt(signal()),'ack')
  self.assertEqual(self.ex.posted[0]['requested_count'],'5')
 def test_four_depth_rejected(self):
  self.ex.book=lambda t:dict(no_dollars=[['.08','4']])
  self.assertEqual(self.engine.attempt(signal()),'Insufficient depth inside cap')
 def test_existing_five_prevents_another(self):
  self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='5')]
  self.assertEqual(self.engine.attempt(signal()),'Eight-contract cap')
 def test_legacy_quantities(self):
  for n in ['1','2','5']:self.assertEqual(requested_count({'requested_count':n}),D(n))
  self.assertEqual(requested_count({}),D(1))
 def test_five_settled_cost(self):
  r=self.acknowledged(fill='5');self.now=1400;self.engine.reconcile()
  self.assertEqual(D(self.store.get(r['ticker'])['pnl']),D('.394'))
 def test_daily_budget_preserved(self):
  self.store.save(dict(ticker='old',status='settled',pnl='-5.3',settled_day=__import__('engine').today()))
  self.assertEqual(self.engine.attempt(signal()),'Daily loss budget including open risk')
