import unittest,math,json
import replay as r
class Checks(unittest.TestCase):
 def test_depth_not_quote_touch(self):
  self.assertIsNone(r.fee_fill((0,[(.10,5)],[(.07,4.99)]),1,.939,.85))
  self.assertIsNone(r.fee_fill((0,[(.10,5)],[(.06,5)]),1,.939,.85))
  x=r.fee_fill((0,[(.10,5)],[(.07,3),(.065,3)]),1,.939,.85)
  self.assertAlmostEqual(x['cost'],3*.93+2*.935);self.assertGreater(x['fee'],0)
 def test_no_floor_breach(self):
  self.assertIsNone(r.fee_fill((0,[],[(.20,5)]),1,.939,.85))
 def test_source_is_asof(self):
  f=dict(t=120,spot=100)
  fn=r.source_fn({'hist':(118,r.np.array([99.,100.,101.,102.,103.]))},1)
  self.assertEqual(fn(120,f),100);self.assertEqual(fn(121,f),101)
  fn2=r.source_fn({'hist':(118,r.np.array([99.,100.,101.,9999.,103.]))},1)
  self.assertEqual(fn(121,f),fn2(121,f))
 def test_live_thresholds_and_caps(self):
  for a in r.ASSETS:
   self.assertEqual(r.lane_for(dict(asset=a,lane=r.Z2)).z,3 if a=='BTC' else 2)
   self.assertEqual(r.lane_for(dict(asset=a,lane=r.Z3)).z,3)
   self.assertEqual(r.lane_for(dict(asset=a,lane=r.Z2)).contracts,5)
 def test_future_book_and_deadline(self):
  T=600;f=dict(t=T,rem=10,side=1,bid=.92,ask=.93,z=3.1,spot=101,ref=100,sigma=.001)
  m=dict(asset='BTC',ticker='BTC-test',close=1200,start=300,settled=1203,result=1,features=[f],ranges=[dict(start='0',end='1',step='.001')])
  b=dict(market=m,books=[(600,[(.92,5)],[(.07,5)]),(626,[(.92,5)],[(.07,5)])],hist=(0,r.np.ones(1300)*100))
  rr,ct=r.opportunities(b,60,1);self.assertEqual(rr,[])
  b['books'][1]=(601,[(.92,5)],[(.07,5)])
  rr,ct=r.opportunities(b,60,1);self.assertEqual(len(rr),2);self.assertTrue(all(x['entered']==601 for x in rr))
  # A later z change is diagnostic only: preserve production frozen retries.
  b['hist'][1][600]=80
  rr,ct=r.opportunities(b,1,1)
  self.assertEqual(len(rr),2);self.assertTrue(all(x['observed_fill_z']<0 for x in rr))
 def test_portfolio_risk_and_unique_markets(self):
  start=1788523200;pool=[]
  for i in range(12):
   t=start+i*1000
   pool.append(dict(ticker=str(i),asset='BTC',lane=r.Z2,t=t,entered=t,settled=t+900,close=t+900,won=0,cost=4.65,fee=.023,entry=.93,net=-4.673,side=1,wait=1))
  rows,meta=r.simulate(pool+pool)
  self.assertEqual(len(rows),4);self.assertIn('daily_loss_including_open_risk',meta['blocks'])
if __name__=='__main__':unittest.main()
