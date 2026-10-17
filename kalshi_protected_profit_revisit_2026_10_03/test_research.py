import unittest,json,math
import numpy as np
import study as s
from state_machine import Protection
from book_replay import top,trade,entry_signals

class Tests(unittest.TestCase):
    def cfg(self,**kw):
        c=dict(id=0,asset='BTC',lo=.55,hi=.65,remlo=2,remhi=14,filter=0,mode=1,stop=.45,safe=.06,ideal=.13,buffer=0);c.update(kw);return c
    def arr(self):
        x=np.full((14,10),np.nan);x[0,:6]=[.58,.60,.58,.60,.58,.60];return x
    def test_arm_no_sale(self):
        st=Protection.from_config(.60,10,self.cfg());self.assertIsNone(st.observe(.66));self.assertEqual(st.armed,1)
    def test_ideal_ratchets(self):
        st=Protection.from_config(.60,10,self.cfg());self.assertIsNone(st.observe(.75));self.assertEqual(st.armed,2);self.assertEqual(st.observe(.72),'ideal_reversal')
    def test_rising_keeps_position(self):
        st=Protection.from_config(.60,10,self.cfg())
        for p in [.65,.67,.70,.75,.85,.99]:self.assertIsNone(st.observe(p))
    def test_hard_stop(self):
        st=Protection.from_config(.60,10,self.cfg());self.assertEqual(st.observe(.30),'hard_stop')
    def test_pending_persistent(self):
        st=Protection.from_config(.60,10,self.cfg());st.observe(.30);self.assertEqual(st.observe(.80),'hard_stop')
    def test_buffer(self):
        st=Protection.from_config(.60,10,self.cfg(buffer=.03));st.observe(.67);self.assertIsNone(st.observe(.65));self.assertEqual(st.observe(.62),'safe_reversal')
    def test_time_scaling(self):
        st=Protection.from_config(.60,14,self.cfg(mode=3,stop=.15,buffer=.03));self.assertAlmostEqual(st.stop,.39);self.assertAlmostEqual(st.safe,.684)
    def test_trailing(self):
        st=Protection.from_config(.60,10,self.cfg(mode=4,stop=.15,buffer=.03));st.observe(.85);self.assertEqual(st.observe(.78),'ideal_reversal')
    def test_invalid_observations(self):
        st=Protection.from_config(.60,10,self.cfg())
        for p in [None,float('nan'),-1,2]:self.assertIsNone(st.observe(p))
    def test_fee_both_legs(self):self.assertAlmostEqual(s.cashchange(.6,1,True)+s.cashchange(.6,1,False),-.0336)
    def test_fee_cent_rounding(self):self.assertAlmostEqual(s.cashchange(.6,1,True,.01),-.62);self.assertAlmostEqual(s.cashchange(.6,1,False,.01),.58)
    def test_fee_quantity(self):self.assertAlmostEqual(s.cashchange(.6,5,True),-3.084)
    def test_fractional_depth(self):
        a=top([(.6,.5),(.5,5)]);self.assertAlmostEqual(a[1],.55);self.assertEqual(a[2],.5);self.assertAlmostEqual(a[3],.51)
    def test_short_depth(self):self.assertIsNone(top([(.6,.5)])[1]);self.assertIsNone(top([(.6,1)])[3])
    def test_gap_fill_actual_bid(self):
        x=self.arr();x[1,:4]=[.30,.32,.25,.27];t=s.simulate_market(x,1,s.carray(self.cfg()));self.assertEqual(t[3],.25);self.assertEqual(t[5],1)
    def test_no_exit_same_arm(self):
        x=self.arr();x[1,:4]=[.66,.68,.66,.68];t=s.simulate_market(x,1,s.carray(self.cfg()));self.assertEqual(t[5],0)
    def test_missing_exit_retry(self):
        x=self.arr();x[1,:2]=[.30,.32];x[2,2:4]=[.22,.24];t=s.simulate_market(x,1,s.carray(self.cfg()));self.assertEqual(t[3],.22);self.assertEqual(t[4],2)
    def test_failed_entry_can_retry(self):
        x=self.arr();x[0,2:4]=[.7,.72];x[2,:4]=[.60,.62,.61,.63];t=s.simulate_market(x,1,s.carray(self.cfg()));self.assertEqual(t[0],2)
    def test_complement_symmetry(self):
        x=self.arr();x[1,:4]=[.3,.32,.25,.27];y=x.copy()
        for j in range(3):y[:,2*j]=1-x[:,2*j+1];y[:,2*j+1]=1-x[:,2*j]
        y[:,6]=-x[:,6];y[:,7]=-x[:,7];y[:,8]=1-x[:,8]
        a=s.simulate_market(x,1,s.carray(self.cfg()));b=s.simulate_market(y,-1,s.carray(self.cfg()));self.assertAlmostEqual(a[2],b[2]);self.assertAlmostEqual(a[3],b[3]);self.assertEqual(a[1],-b[1])
    def test_later_outcome_no_entry_leak(self):
        x=self.arr();a=s.simulate_market(x,1,s.carray(self.cfg()));b=s.simulate_market(x,-1,s.carray(self.cfg()));self.assertEqual(a[:3],b[:3])
    def market(self):return dict(ticker='BTC_TEST',asset='BTC',start=0,close=900,settled=930,result=1)
    def frame(self,t,yes,no):return [t,*top(yes),*top(no)]
    def test_book_latency_and_actual_gap_price(self):
        books=[self.frame(0,[(.58,10)],[(.4,10)]),self.frame(63.25,[(.58,10)],[(.4,10)]),self.frame(70,[(.67,10)],[(.32,10)]),self.frame(90,[(.64,10)],[(.35,10)]),self.frame(90.3,[(.5,10)],[(.49,10)]),self.frame(91,[(.42,10)],[(.57,10)])]
        a=trade(self.cfg(),self.arr(),self.market(),books,.25);self.assertAlmostEqual(a['exit'],.5);self.assertEqual(a['reason'],'safe_reversal');self.assertEqual(a['exit_ts'],90.3)
    def test_no_own_side_ask_used_as_sale(self):
        books=[self.frame(0,[(.58,10)],[(.4,10)]),self.frame(63.25,[(.58,10)],[(.4,10)]),self.frame(70,[(.3,10)],[(.68,10)]),self.frame(71,[(.25,10)],[(.73,10)])]
        a=trade(self.cfg(),self.arr(),self.market(),books,.25);self.assertAlmostEqual(a['exit'],.25)
    def test_worst_fill_level_respects_limit(self):
        books=[self.frame(0,[(.63,10)],[(.36,.5),(.34,10)]),self.frame(64,[(.63,10)],[(.36,.5),(.34,10)])]
        self.assertIsNone(trade(self.cfg(),self.arr(),self.market(),books,.25))
    def test_crossed_book_not_used(self):
        books=[self.frame(0,[(.70,10)],[(.4,10)]),self.frame(64,[(.70,10)],[(.4,10)])]
        self.assertIsNone(trade(self.cfg(),self.arr(),self.market(),books,.25))
    def test_historical_state_parity(self):
        xx=np.load(s.P/'market_arrays.npy');mm=json.loads((s.P/'markets.json').read_text());cc=s.configs();checked=0
        for i in range(0,len(mm),19):
            for c in (cc[2],cc[21],cc[39],cc[40]):
                t=s.simulate_market(xx[i],mm[i]['result'],s.carray(c))
                if t[0]<0:continue
                state=Protection.from_config(t[2],14-t[0],c);reason=None;kfound=14
                for k in range(t[0]+1,14):
                    b,a=xx[i,k,:2]
                    if np.isfinite(b+a):reason=state.observe(b if t[1]==1 else 1-a)
                    if reason and np.isfinite(xx[i,k,2:4]).all():
                        b,a=xx[i,k,2:4];price=b if t[1]==1 else 1-a
                        if 0<price<1:kfound=k;break
                self.assertEqual(t[4],kfound);self.assertEqual(t[6],int(state.armed>=1));self.assertEqual(t[7],int(state.armed==2));checked+=1
        self.assertGreater(checked,1000);print('Independent state parity trades',checked)

if __name__=='__main__':unittest.main()
