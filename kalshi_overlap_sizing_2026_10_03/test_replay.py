import unittest
import replay as r

def event(ticker='a',lane=0,t=1790000000,price=.93,z=3.,ask=.93,side=1,settled=None,asset='BTC',won=1):
    e=dict(ticker=ticker,asset=asset,lane=lane,profile=lane,t=t,entered=t+3,close=t+200,settled=settled or t+230,day=r.s.old.day(t+200),cohort='test',entry=price,cap=.939 if lane==0 else .975,fee=r.s.fee(price,5),quantity=5,won=won,z=z,side=side,rem=5,ask=ask,original_cap=.939)
    e['overlap']=r.intersection(e)
    e['fills']={n:(price,r.s.fee(price,n)) for n in [5,7,10]}
    e['strict']={n:.93<=price<=.939 for n in [7,10]};e['second_fill']=e['fills'][5]
    return e

class ReplayTests(unittest.TestCase):
    def test_gate_boundaries(self):
        for ask,z,expected in [(.93,3,True),(.949,3,True),(.95,3,False),(.92,3,False),(.94,2.999,False)]:
            self.assertEqual(event(ask=ask,z=z)['overlap'],expected)

    def test_later_confirmation_cannot_enlarge_first(self):
        ee=[event(z=2.1),event(lane=1,t=1790000060)]
        out,_,_=r.replay(ee,'overlap10')
        self.assertEqual([p['quantity'] for p in out],[5])
        out,_,_=r.replay(ee,'parallel5')
        self.assertEqual([p['quantity'] for p in out],[5,5])
        self.assertTrue(out[1]['second_later'])

    def test_simultaneous_larger_size_fallback(self):
        e=event();e['fills'][10]=None
        out,_,_=r.replay([e],'overlap10');self.assertEqual(out[0]['quantity'],5)

    def test_daily_budget_larger_size_fallback(self):
        out,_,_=r.replay([event('a',ask=.90,z=2.1),event('b',t=1790000001,asset='ETH')],'overlap10')
        self.assertEqual([p['quantity'] for p in out],[5,5])

    def test_strict_execution_intersection(self):
        e=event(price=.96,lane=1)
        self.assertEqual(r.replay([e],'overlap10')[0][0]['quantity'],10)
        self.assertEqual(r.replay([e],'strict10')[0][0]['quantity'],5)

    def test_two_legs_only_one_per_lane(self):
        ee=[event(),event(lane=1,t=1790000060),event(lane=0,t=1790000120),event(lane=1,t=1790000180)]
        self.assertEqual(len(r.replay(ee,'parallel5')[0]),2)
        self.assertEqual(len(r.replay(ee,'baseline')[0]),1)

    def test_opposite_direction_addon_rejected(self):
        self.assertEqual(len(r.replay([event(),event(lane=1,t=1790000060,side=-1)],'parallel5')[0]),1)

    def test_no_add_after_settlement(self):
        self.assertEqual(len(r.replay([event(settled=1790000050),event(lane=1,t=1790000060)],'parallel5')[0]),1)

    def test_book_depth_not_double_counted(self):
        book=(1,(),((.07,5),))
        self.assertIsNotNone(r.range_fill(book,1,.975,.93,5))
        self.assertIsNone(r.range_fill(book,1,.975,.93,10))
        self.assertIsNone(r.range_fill(book,1,.975,.93,5,skip=5))
        first,second=event(),event(lane=1)
        second['second_fill']=None
        self.assertEqual(len(r.replay([first,second],'parallel5')[0]),1)

    def test_multilevel_larger_order_reprices(self):
        book=(1,(),((.07,5),(.05,5)))
        self.assertAlmostEqual(r.range_fill(book,1,.975,.93,10)[0],.94)
        self.assertAlmostEqual(r.range_fill(book,1,.975,.93,5,skip=5)[0],.95)
        self.assertIsNone(r.range_fill(book,1,.939,.93,10))

    def test_two_leg_loss_is_same_outcome_and_daily_halt(self):
        ee=[event(won=0),event(lane=1,t=1790000060,won=0),event('b',asset='ETH',t=1790000300)]
        out,_,_=r.replay(ee,'parallel5')
        self.assertEqual(len(out),2);self.assertEqual(sum(p['quantity'] for p in out),10)
        self.assertLess(sum(p['net'] for p in out),-9)

    def test_different_market_same_asset_rejected_until_settlement(self):
        ee=[event(),event('b',t=1790000060),event('c',t=1790000300)]
        self.assertEqual([p['ticker'] for p in r.replay(ee,'parallel5')[0]],['a','c'])

if __name__=='__main__':unittest.main()
