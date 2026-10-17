import unittest
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import numpy as np
from engine import cash,entry,exit_trade,direction

def path():
    x=np.zeros((14,14));x[:,0]=.59;x[:,1]=.60;x[:,2]=.59;x[:,3]=.60;x[:,4]=.59;x[:,5]=.60
    return x
def ex(mode=1,amount=.1,activate=14,confirm=1,arm=9,col=8,margin=0):return np.array([mode,amount,activate,confirm,arm,col,margin],float)
class StopTests(unittest.TestCase):
    def test_buy_and_sell_cash_rounding(self):
        for price in (.03,.1,.15,.333,.6,.999):
            p=Decimal(str(price));fee=(Decimal('.07')*p*(1-p)).quantize(Decimal('.000001'),rounding=ROUND_CEILING)
            for buy in (True,False):
                expected=(((-p if buy else p)-fee)/Decimal('.0001')).to_integral_value(rounding=ROUND_FLOOR)*Decimal('.0001')
                self.assertAlmostEqual(cash(price,buy),float(expected),places=10)
    def test_gap_sells_at_bid_not_stop(self):
        x=path();x[1,:4]=.49,.5,.2,.21
        r=exit_trade(x,1,entry(x,0,14,0),ex())
        self.assertTrue(r[2]);self.assertAlmostEqual(r[0],.2);self.assertEqual(r[1],1)
    def test_sale_fees_reduce_salvage(self):
        self.assertLess(cash(.15,False)-.0025,.15)
    def test_absolute_salvage_not_profit_exit_for_cheap_entry(self):
        x=path();x[:,:6]=[.09,.1,.09,.1,.09,.1];ent=entry(x,2,14,0)
        r=exit_trade(x,1,ent,ex(2,.15));self.assertFalse(r[2]);self.assertEqual(r[0],1)
    def test_confirmation_resets_after_recovery(self):
        x=path();x[1,:4]=.48,.49,.48,.49;x[3:5,:4]=[.48,.49,.48,.49]
        r=exit_trade(x,1,entry(x,0,14,0),ex(confirm=2));self.assertEqual(r[1],4)
    def test_missing_signal_resets_confirmation(self):
        x=path();x[1,:4]=.48,.49,.48,.49;x[2,0:2]=np.nan;x[3,:4]=.48,.49,.48,.49
        r=exit_trade(x,1,entry(x,0,14,0),ex(confirm=2));self.assertFalse(r[2])
    def test_pending_exit_waits_for_bid(self):
        x=path();x[1,:4]=.48,.49,np.nan,np.nan;x[2,:4]=.7,.71,.69,.7
        r=exit_trade(x,1,entry(x,0,14,0),ex());self.assertTrue(r[2]);self.assertEqual(r[1],2);self.assertAlmostEqual(r[0],.69)
    def test_delayed_sale_cannot_occur_at_expiration(self):
        x=path();x[13,:6]=.2,.21,.2,.21,.1,.11
        r=exit_trade(x,-1,entry(x,0,14,0,1),ex(),1);self.assertFalse(r[2]);self.assertEqual(r[0],0)
    def test_band_has_unconditional_deadline(self):
        x=path();x[:,:6]=[.8,.81,.8,.81,.8,.81]
        r=entry(x,0,8,1);self.assertEqual(r[0],6);self.assertAlmostEqual(r[2],.81)
    def test_missing_model_falls_back_without_skip(self):
        x=path();x[:,11]=np.nan;r=entry(x,11,14,0);self.assertEqual(r[0],0);self.assertEqual(r[1],1);self.assertEqual(r[3],1)
    def test_no_complement_ask_error(self):
        x=path();r=entry(x,1,14,0);self.assertEqual(r[1],-1);self.assertAlmostEqual(r[2],.41)
    def test_late_activation(self):
        x=path();x[1:,:6]=[.1,.11,.1,.11,.1,.11]
        r=exit_trade(x,-1,entry(x,0,14,0),ex(2,.15,activate=2));self.assertEqual(r[1],12)
    def test_trailing_requires_profit_arm(self):
        x=path();x[1,:4]=.52,.53,.52,.53;x[2,:4]=.82,.83,.82,.83;x[3,:4]=.71,.72,.69,.7
        r=exit_trade(x,1,entry(x,0,14,0),ex(4,.1,arm=.2));self.assertEqual(r[1],3);self.assertTrue(r[3])
    def test_hold_matches_actual_result(self):
        x=path();ent=entry(x,0,14,0)
        self.assertEqual(exit_trade(x,1,ent,ex(0))[0],1);self.assertEqual(exit_trade(x,-1,ent,ex(0))[0],0)
if __name__=='__main__':unittest.main()
