import unittest
from core import simulate,fee,pay,qte
from recurring import select

def row(owner,entered=100,settled=200,payout=0,entry=.5):
    return {'owner':owner,'asset':'X','ticker':owner,'side':'yes','entered':entered,'settled':settled,'entry':entry,'payout':payout,'mult':1}
class WindowTests(unittest.TestCase):
    def test_complement_preserves_legal_price(self):
        c={'yes_bid':{'close_dollars':'.99'},'yes_ask':{'close_dollars':'1.00'}}
        self.assertEqual(qte(c,'no'),(0,.01))
    def test_no_edge_requirement(self):
        options=[dict(ticker='A',side='yes',prob=.1,ask=.6,bid=.58,mid=.59,mult=1),dict(ticker='A',side='no',prob=.9,ask=.95,bid=.93,mid=.94,mult=1)]
        self.assertIsNotNone(select(options,'model'))
    def test_future_quote_cannot_choose_side(self):
        a=dict(ticker='A',side='yes',prob=.6,ask=.5,bid=.48,mid=.49,mult=1,future_quote=.99)
        b=dict(ticker='B',side='yes',prob=.55,ask=.5,bid=.48,mid=.49,mult=1,future_quote=.01)
        self.assertEqual(select([a,b],'model')['ticker'],'A')
        a['future_quote']=.01;b['future_quote']=.99
        self.assertEqual(select([a,b],'model')['ticker'],'A')
    def test_scalar_no_settlement(self):
        self.assertAlmostEqual(pay({'result':'scalar','settlement_value_dollars':'.48'},'no'),.52)
    def test_one_owner_one_entry(self):
        out,_=simulate([row('same'),row('same',101)],1,.01)
        self.assertEqual(len(out),1)
    def test_future_settlement_cannot_fund_entry(self):
        # Five nearly-$5 positions would exceed the $20 open-loss budget.
        rr=[row(str(i),100+i,1000,payout=1,entry=.98) for i in range(6)]
        out,blocked=simulate(rr,5,.01)
        self.assertEqual(len(out),4);self.assertEqual(blocked['daily_risk'],2)
    def test_release_at_actual_settlement(self):
        rr=[row(str(i),100+i,110,payout=1,entry=.98) for i in range(4)]+[row('later',111,120,payout=1,entry=.98)]
        out,_=simulate(rr,5,.01);self.assertEqual(len(out),5)
    def test_losses_are_not_reset_daily(self):
        rr=[row(str(i),100+i*86400,200+i*86400,payout=0,entry=.98) for i in range(25)]
        out,blocked=simulate(rr,5,.01)
        self.assertLess(len(out),25);self.assertIn('cash',blocked)
    def test_larger_size_can_reduce_participation(self):
        rr=[row(str(i),100+i,1000,payout=1,entry=.7) for i in range(12)]
        small,_=simulate(rr,1,.01);big,_=simulate(rr,5,.01)
        self.assertEqual(len(small),12);self.assertEqual(len(big),5)
if __name__=='__main__':unittest.main()
