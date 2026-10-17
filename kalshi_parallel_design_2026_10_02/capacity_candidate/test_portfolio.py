import unittest
from decimal import Decimal as D
from unittest.mock import patch
import engine
import fixtures
from fixtures import signal
class Portfolio(unittest.TestCase):
 setUp=fixtures.Tests.setUp
 tearDown=fixtures.Tests.tearDown
 def open_four(self):
  for a in ['BTC','ETH','SOL','XRP']:
   s=signal(ticker='KX'+a+'15M-TEST');s['asset']=a
   result=self.engine.attempt(s)
   if result!='ack':return result
   r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
  return 'four open'
 def test_caps(self):
  self.assertEqual(engine.MAX_CONTRACTS,D(20));self.assertEqual(engine.MAX_RISK,D(19))
 def test_four_with_scaled_daily(self):
  with patch.object(engine,'DAILY_LOSS_BUDGET',D(25)):
   self.assertEqual(self.open_four(),'four open');self.assertEqual(len(self.ex.posted),4)
 def test_preserved_daily_blocks_third(self):
  with patch.object(engine,'DAILY_LOSS_BUDGET',D(10)):
   self.assertEqual(self.open_four(),'Daily loss budget including open risk');self.assertEqual(len(self.ex.posted),2)
 def test_21_exceeds_contract_limit(self):
  self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='16')]
  self.assertEqual(self.engine.attempt(signal()),'20-contract cap')
 def test_loss_budget_includes_reserved_risk(self):
  with patch.object(engine,'DAILY_LOSS_BUDGET',D(25)):
   self.store.save(dict(ticker='old',status='settled',pnl='-21',settled_day=engine.today()))
   self.assertEqual(self.engine.attempt(signal()),'Daily loss budget including open risk')
 def test_size_and_signal_unchanged(self):
  self.assertEqual(engine.CONTRACTS,5)
  s=signal();s['z']=1.99
  with self.assertRaises(ValueError):self.engine.attempt(s)
  self.assertEqual(self.ex.posted,[])
