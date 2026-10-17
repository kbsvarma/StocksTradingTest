import unittest,json
import numpy as np
import study as s
from book_hold import top,fill
from candidate_signal import signal

class Tests(unittest.TestCase):
    def market(self,result=1):return dict(ticker='TEST',asset='ETH',start=0,close=900,settled=930,result=result)
    def arr(self):
        x=np.full((1,14,10),np.nan);x[0,0,:4]=[.58,.60,.59,.60];x[0,0,6:9]=[.3,.1,.65];return x
    def cfg(self,**kw):
        c=dict(asset='ETH',direction=1,remlo=2,remhi=14,filter='none');c.update(kw);return c
    def test_breakeven_not_fifty(self):self.assertAlmostEqual(float(s.cash(.60))+.0025,.6193)
    def test_opposite_uses_ask(self):
        d=s.features(self.arr(),[self.market()]);r=s.rows(self.cfg(direction=-1),d)[0];self.assertAlmostEqual(r['entry'],.41)
    def test_two_directions_pay_spread_and_fees(self):
        d=s.features(self.arr(),[self.market()]);a=s.rows(self.cfg(),d)[0];b=s.rows(self.cfg(direction=-1),d)[0];self.assertEqual(a['won']+b['won'],1);self.assertAlmostEqual(a['pnl']+b['pnl'],1-float(s.cash(.6))-float(s.cash(.41)))
    def test_flip_then_recover(self):
        x=self.arr();x[0,1,:2]=[.4,.42];x[0,2,:2]=[.7,.72];r=s.rows(self.cfg(),s.features(x,[self.market()]))[0];self.assertTrue(r['crossed_below_half']);self.assertEqual(r['favorite_won'],1)
    def test_final_loss_without_observed_flip(self):
        x=self.arr();x[0,1,:2]=[.58,.60];r=s.rows(self.cfg(),s.features(x,[self.market(-1)]))[0];self.assertFalse(r['crossed_below_half']);self.assertEqual(r['favorite_won'],0)
    def test_missing_path_is_unknown(self):
        r=s.rows(self.cfg(),s.features(self.arr(),[self.market()]))[0];self.assertIsNone(r['crossed_below_half'])
    def test_first_entry_per_market(self):
        x=self.arr();x[0,1]=x[0,0];rr=s.rows(self.cfg(),s.features(x,[self.market()]));self.assertEqual(len(rr),1);self.assertEqual(rr[0]['rem'],14)
    def test_failed_quote_retries_later(self):
        x=self.arr();x[0,1]=x[0,0];x[0,0,2:4]=[.7,.71];rr=s.rows(self.cfg(),s.features(x,[self.market()]));self.assertEqual(rr[0]['rem'],13)
    def test_no_future_open_filter_for_book_signals(self):
        x=self.arr();x[0,0,2:4]=[.8,.81];d=s.features(x,[self.market()]);self.assertFalse(d['valid'][0,0]);self.assertTrue(d['signal_valid'][0,0])
    def test_quote_decline_is_completed_and_signed(self):
        x=self.arr();x[0,5,:4]=[.65,.67,.65,.67];x[0,6,:4]=[.59,.61,.59,.61];d=s.features(x,[self.market()]);r=s.rows(self.cfg(filter='quote_falling',remlo=6,remhi=8),d);self.assertEqual(len(r),1);self.assertEqual(r[0]['rem'],8)
    def test_no_future_price_in_entry_filter(self):
        x=self.arr();d=s.features(x,[self.market()]);a=s.rows(self.cfg(),d)[0];x[0,10,:2]=[.1,.12];b=s.rows(self.cfg(),s.features(x,[self.market(-1)]))[0];self.assertEqual((a['entry'],a['decision']),(b['entry'],b['decision']))
    def frame(self,t,y,no):return [t,*top(y),*top(no)]
    def test_depth_requires_full_contract(self):
        bb=[self.frame(0,[(.58,1)],[(.4,.5)]),self.frame(64,[(.58,1)],[(.4,.5)])];self.assertIsNone(fill(bb,60,1,1))
    def test_worst_ask_below_cap(self):
        bb=[self.frame(0,[(.63,1)],[(.36,.5),(.34,1)]),self.frame(64,[(.63,1)],[(.36,.5),(.34,1)])];self.assertIsNone(fill(bb,60,1,1))
    def test_no_pre_signal_fill(self):
        bb=[self.frame(0,[(.58,1)],[(.4,1)]),self.frame(62,[(.58,1)],[(.4,1)]),self.frame(64,[(.7,1)],[(.29,1)])];self.assertIsNone(fill(bb,60,1,1))
    def test_reference_entry_parity(self):
        x,mm=s.load();rr=s.rows(dict(asset='BTC',direction=1,remlo=2,remhi=14,filter='none'),s.features(x,mm));old=json.loads((s.ROOT/'kalshi_protected_profit_revisit_2026_10_03/trades_2.json').read_text());a={r['ticker']:r for r in rr};self.assertEqual(len(a),len(old))
        for r in old:self.assertEqual(a[r['ticker']]['decision'],r['decision']);self.assertAlmostEqual(a[r['ticker']]['pnl'],r['hold_pnl'],places=8)
        print('Prior identical-entry hold parity',len(old))
    def test_candidate_independent_signal_parity(self):
        x,mm=s.load();d=s.features(x,mm);expected=s.mask(s.configs()[252],dict(d,valid=d['signal_valid']));count=0;passing=0
        for i,m in enumerate(mm):
            for j in (6,7,8):
                pb,pa=x[i,j-1,:2];b,a=x[i,j,:2];r=signal(m['asset'],pb,pa,b,a,14-j)
                self.assertEqual(r is not None,bool(expected[i,j]));count+=1;passing+=int(r is not None)
        print('Independent candidate decision parity',count,'qualified',passing)

if __name__=='__main__':unittest.main()
