import unittest
from replay import Run,maker_fill,account,down,up
def book(t,bid=5000,ask=5100,old=5300,age=0):
    return [t,0,0,[[bid,10000]],[[10000-ask,10000]],age,50,.002,.005,old,0,800]
def tape(t,src,side,p,q=500,tid='a'):return [t,1,0,src,side,p,q,tid]
def start():
    r=Run();r.new_market({0:dict(ticker='TEST',close_ms=800000)});r.book(book(100000));r.book(book(100250));return r
class Cases(unittest.TestCase):
    def order(self):return dict(buy=True,aggressor=-1,price=5000,queue=200,remaining=500,accepted=100,until=200)
    def test_queue(self):
        o=self.order();self.assertEqual(maker_fill(o,150,-1,5000,350),150);self.assertEqual(o['queue'],0)
    def test_through_capped_quantity(self):
        o=self.order();self.assertEqual(maker_fill(o,150,-1,4900,100),100);self.assertEqual(maker_fill(o,150,-1,4900,1000),500)
    def test_time_side_and_quote_touch(self):
        o=self.order()
        for src,side,p in [(99,-1,4900),(200,-1,4900),(150,1,4900),(150,-1,5100)]:self.assertEqual(maker_fill(o,src,side,p,1000),0)
    def test_stable_screen(self):
        r=Run();r.new_market({0:dict(close_ms=800000,ticker='TEST')});b=book(100000);b[7]=.03
        self.assertIsNone(r.signal(b,1));r.stable=False;self.assertIsNotNone(r.signal(b,1))
    def test_post_only_reject(self):
        r=Run();r.new_market({0:dict(ticker='TEST',close_ms=800000)});r.book(book(100000));r.book(book(100250,4900,5000));self.assertEqual(r.ledger[0]['end_reason'],'post_only_cross');self.assertEqual(r.ledger[0]['net'],0)
    def test_partial_entry_cancel_race_and_exit_ack(self):
        r=start();r.trade(tape(100400,100380,-1,4900,100));r.trade(tape(100550,100530,-1,4900,200,'b'));r.trade(tape(100750,100700,-1,4900,500,'c'))
        self.assertEqual(r.states[0]['o']['inventory'],300);r.book(book(100900));self.assertEqual(r.states[0]['o']['exit']['remaining'],300)
        r.trade(tape(100920,100850,1,5300,500,'d'));self.assertEqual(r.states[0]['o']['inventory'],300)
        r.trade(tape(101000,100980,1,5300,500,'e'));self.assertIsNone(r.states[0]['o']);self.assertEqual(r.ledger[0]['quantity'],3);self.assertAlmostEqual(r.ledger[0]['net'],.06)
    def test_stop_slips_and_charges_taker(self):
        r=start();r.trade(tape(100400,100380,-1,4900));r.book(book(100900));r.book(book(101000,4400,4500));self.assertEqual(r.states[0]['o']['phase'],'liquidating')
        r.book(book(101500,4200,4300));o=r.ledger[0];self.assertLess(o['net'],-.40);self.assertEqual(o['end_reason'],'stop');self.assertGreater(o['fees'],0)
    def test_unfilled(self):
        r=start()
        for t in range(100500,110501,250):r.book(book(t))
        self.assertEqual(r.ledger[0]['quantity'],0);self.assertEqual(r.cash,100)
    def test_gap_not_dropped(self):
        r=start();r.trade(tape(100400,100380,-1,4900));r.book(book(103000,4800,4900));r.book(book(103500,4700,4800));self.assertTrue(r.ledger[0]['coverage_unknown']);self.assertEqual(r.ledger[0]['end_reason'],'feed_gap')
    def test_budget(self):
        r=Run();r.cash=3;r.new_market({0:dict(ticker='TEST',close_ms=800000)});r.book(book(100000));self.assertEqual(r.rejections['budget'],1);self.assertIsNone(r.states[0]['o'])
    def test_no_reuse_of_thin_exit_depth(self):
        r=start();r.trade(tape(100400,100380,-1,4900));r.book(book(100900));r.book(book(101000,4400,4500))
        b=book(101500,4200,4300);b[3][0][1]=100;r.book(b);self.assertEqual(r.states[0]['o']['inventory'],400)
        b[0]=101750;r.book(b);self.assertEqual(r.states[0]['o']['inventory'],400)
        r.book(book(102000,4100,4200));self.assertEqual(r.ledger[0]['remaining_qty'],0)
    def test_target_cancel_race_and_no_oversell(self):
        r=start();r.trade(tape(100400,100380,-1,4900));r.book(book(100900));r.book(book(101000,4400,4500))
        r.trade(tape(101240,101220,1,5300,200,'race'));self.assertEqual(r.states[0]['o']['inventory'],300)
        r.trade(tape(101350,101300,1,5300,500,'too_late'));self.assertEqual(r.states[0]['o']['inventory'],300)
        r.book(book(101500,4200,4300));self.assertEqual(r.ledger[0]['sold'],500);self.assertEqual(r.ledger[0]['end_reason'],'stop')
    def test_daily_budget_includes_reserved_principal(self):
        r=Run();r.new_market({0:dict(ticker='TEST',close_ms=800000)});r.daynet[r.day(100000)]=-19;r.book(book(100000));self.assertEqual(r.rejections['budget'],1)
    def test_fee_rounding_and_ticks(self):
        x=account([dict(order='entry',qty=500,price=9310,maker=True,buy=True),dict(order='target',qty=500,price=9510,maker=True,buy=False)]);self.assertEqual(x['net'],.1)
        self.assertEqual(down(6580),6500);self.assertEqual(up(6580),6600);self.assertEqual(down(9315),9310);self.assertEqual(up(9315),9320)
if __name__=='__main__':unittest.main()
