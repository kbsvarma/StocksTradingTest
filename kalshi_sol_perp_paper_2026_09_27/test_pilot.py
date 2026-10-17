import copy,json,unittest
from strategy import signal,size,vwap,pnl,funding_amount
from runner import advance,get
BOOK={'asks':[['10.001','100']], 'bids':[['9.999','100']]}
def state():return {'started_ts':1000,'position':None,'pending':None,'trades':[],'realized_pnl':0}
def indicator(T=3600):return {'status':'ready','decision_ts':T,'side':1,'mid':10,'spread_bp':2,'active_hour':True,'entry_high':9,'entry_low':8,'exit_high':11,'exit_low':9,'stop_fraction':.02}
class Tests(unittest.TestCase):
 def test_public_allowlist(self):
  with self.assertRaises(ValueError):get('/margin/orders')
 def test_no_retroactive_entry(self):
  s=state();s['started_ts']=3700;advance(s,3701,BOOK,indicator(),[],True);self.assertIsNone(s['pending'])
 def test_hour_delay(self):
  s=state();advance(s,3603,BOOK,indicator(),[],True);self.assertEqual(s['pending']['due'],7200);self.assertIsNone(s['position']);advance(s,7203,BOOK,indicator(7200),[],True);self.assertIsNotNone(s['position']);self.assertLessEqual(s['position']['count']*s['position']['entry'],50)
 def test_restart_idempotent(self):
  s=state();advance(s,3603,BOOK,indicator(),[],True);s=json.loads(json.dumps(s));advance(s,7203,BOOK,indicator(7200),[],True);p=copy.deepcopy(s['position']);advance(s,7233,BOOK,indicator(7200),[],True);self.assertEqual(p,s['position'])
 def test_late_entry_cancelled(self):
  s=state();advance(s,3603,BOOK,indicator(),[],True);advance(s,7350,BOOK,indicator(7200),[],True);self.assertIsNone(s['position'])
 def test_exit_without_indicator(self):
  s=state();s['position']={'side':1,'count':4,'entry':10,'entry_ts':3603,'entry_scheduled_ts':3600,'signal_ts':0,'stop_fraction':.02};book={'asks':[['9.6','100']],'bids':[['9.5','100']]};advance(s,7203,book,{'status':'history_gap'},[],True);self.assertEqual(s['pending']['kind'],'exit');advance(s,10803,book,{'status':'history_gap'},[],True);self.assertEqual(len(s['trades']),1);self.assertLess(s['trades'][0]['net'],0)
 def test_halted_exchange_does_not_fill_exit(self):
  s=state();s['position']={'side':1,'count':2,'entry':10,'entry_ts':1000,'stop_fraction':.02};s['pending']={'kind':'exit','due':2000,'reason':'stop'};advance(s,3603,BOOK,indicator(),[],False);self.assertIsNotNone(s['position']);self.assertEqual(len(s['trades']),0)
 def test_depth(self):self.assertIsNone(vwap({'asks':[['10','1']]},1,2))
 def test_funding_direction(self):
  p={'side':-1,'count':2,'entry_ts':0};rates=[{'funding_time':'1970-01-01T01:00:00Z','funding_rate':.001,'mark_price':'10'}];self.assertAlmostEqual(funding_amount(p,rates,4000),.02)
 def test_no_future_candle_effect(self):
  cs=[{'end_period_ts':i*3600,'bid':{'close':str(10+i*.001)},'ask':{'close':str(10.0001+i*.001)},'volume':'10'} for i in range(97)];a=signal(cs,96*3600);cs.append({'end_period_ts':97*3600,'bid':{'close':'500'},'ask':{'close':'501'},'volume':'10'});self.assertEqual(a,signal(cs,96*3600))
 def test_risk(self):
  for p in [1,10,50,100]:
   for s in [.01,.05,.5]:n=size(p,s);self.assertLessEqual(n*p,50);self.assertLessEqual(n*p*s,1+1e-9)
if __name__=='__main__':unittest.main()
