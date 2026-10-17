"""Offline accounting, as-of, execution and research parity checks."""
import ast,asyncio,copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from engine import *
from store import Store
from observer import Observer,Client
from reporting import summary
P=Path(__file__).resolve().parent
CFG=json.loads((P/'frozen_config.json').read_text())

def point(t=910,**kw):
 return dict(wall_clock=t,event_type='point',server='A',home='A',away='B',home_sets=0,away_sets=0,home_games=0,away_games=0,home_score=15,away_score=0,result='server_won',**kw)
def payload(items):return {'pbp':{'periods':[{'home_competitor_id':'A','away_competitor_id':'B','events':items}]}}
def candle(b=.48,a=.50,v=5):return dict(yes_bid={'close_dollars':str(b)},yes_ask={'close_dollars':str(a)},volume_fp=str(v))
def book(a=.50,q=1):return {'orderbook_fp':{'yes_dollars':[['0.48','10']],'no_dollars':[[str(1-a),str(q)]]}}
def pick(**kw):return dict(ticker='KXATPMATCH-TEST-A',side='yes',ask=.5,cap=.52,mult=1,**kw)
PRIOR=dict(home='A',away='B',pa=.65,pb=.65,validated=True,received=800,receipt=1)

class SignalTests(unittest.TestCase):
 def test_model_symmetry_and_fit(self):
  for base in (.6,.65):
   for target in (.2,.5,.8):
    m=fit(base,target);self.assertAlmostEqual((m.match(0,0,0)+m.match(0,0,1))/2,target,places=4);m.clear()
 def test_original_model_unchanged(self):
  src=P.parent/'kalshi_cross_market_edges_2026_10_11'/'tennis_markov.py'
  if not src.exists():self.skipTest('Research source parity checked on workstation')
  original=ast.parse(src.read_text());frozen=ast.parse((P/'model.py').read_text())
  for name in ('Model','fit'):
   a=next(n for n in original.body if getattr(n,'name',None)==name);b=next(n for n in frozen.body if getattr(n,'name',None)==name)
   self.assertEqual(ast.dump(a),ast.dump(b))
 def test_90_second_boundary(self):
  state,err=probability([point(),point(911)],PRIOR,1000,CFG);self.assertIsNone(err);self.assertEqual(state['source_t'],910);self.assertEqual(state['serve_counts'],[1,1,0,0])
 def test_no_future_score(self):self.assertEqual(probability([point(911)],PRIOR,1000,CFG)[1],'no_90s_score')
 def test_stale_score(self):self.assertEqual(probability([point(879)],PRIOR,1000,CFG)[1],'score_older_than_120s')
 def test_point_and_game_dedup(self):
  a=point(900);b={**a,'event_type':'game_won'};c=point(910)
  state,_=probability([a,b,c],PRIOR,1000,CFG);self.assertEqual(state['serve_counts'],[2,2,0,0])
 def test_game_and_tiebreak_skipped(self):
  for e in ({**point(),'event_type':'game_won'},{**point(),'home_games':6,'away_games':6}):self.assertIsNotNone(probability([e],PRIOR,1000,CFG)[1])
 def test_choose_cost_volume_and_complement(self):
  ms=[dict(ticker='TEST-A',custom_strike={'tennis_competitor':'A'})];cs={'TEST-A':{1000:candle()}}
  r,e=choose(ms,cs,{'probability_home':.8},PRIOR,1000,1,CFG);self.assertIsNone(e);self.assertEqual(r['side'],'yes');self.assertAlmostEqual(r['edge'],.8*.5+.49*.5-.5-.0175-.01)
  r,e=choose(ms,cs,{'probability_home':.2},PRIOR,1000,1,CFG);self.assertIsNone(e);self.assertEqual(r['side'],'no')
  cs['TEST-A'][1000]['volume_fp']='4';self.assertIsNone(choose(ms,cs,{'probability_home':.8},PRIOR,1000,1,CFG)[0])
 def test_bad_quote_and_fee(self):
  self.assertIsNone(quote(candle(.6,.5)));self.assertIsNone(quote(candle(float('nan'),.5)));self.assertEqual(fee(.5),.0175);self.assertEqual(fee(.9),.0063)

class BookTests(unittest.TestCase):
 def test_fractional_depth(self):
  b=book();b['orderbook_fp']['no_dollars']=[['.50','.4'],['.49','.6']]
  fill,err=book_fill(b,'yes',.51);self.assertIsNone(err);self.assertAlmostEqual(fill['vwap'],.506)
 def test_depth_and_cap(self):
  for b,c in [(book(q=.99),.52),(book(a=.53),.52)]:self.assertEqual(book_fill(b,'yes',c)[1],'insufficient_depth_inside_cap')
 def test_no_complement(self):
  fill,err=book_fill(book(),'no',.52);self.assertIsNone(err);self.assertAlmostEqual(fill['vwap'],.52)
 def test_bad_books(self):
  self.assertEqual(book_fill({},'yes',.52)[1],'missing_fixed_point_book')
  self.assertEqual(book_fill(book(a=.4),'yes',.52)[1],'crossed_book')
  self.assertEqual(book_fill(book(q=float('nan')),'yes',.52)[1],'invalid_book')

class StoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.cfg=copy.deepcopy(CFG);self.s=Store(Path(self.tmp.name)/'paper.sqlite',self.cfg)
  with self.s.db:self.s.db.execute('INSERT INTO events(event,series,start,info) VALUES(?,?,?,?)',('E','KXATPMATCH',900,'{"milestone":{"title":"Test"}}'))
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def ingest(self,items,received):
  r=dict(url='test',started=received-.1,received=received,duration=.1,status=200,headers={},body=payload(items));rid,h=self.s.receipt(r);self.s.ingest_score('E',rid,h,received,r['started']);return rid
 def test_snapshot_revisions_never_rewrite_past(self):
  self.ingest([point(910)],990);old=self.s.snapshot('E',1000)
  self.ingest([{**point(910),'home_score':30}],1001)
  self.assertEqual(self.s.snapshot('E',1000)['id'],old['id']);self.assertEqual(plays(self.s.body(old['hash']))[0]['home_score'],15)
  self.assertEqual(self.s.db.execute('SELECT sum(revision) FROM points').fetchone()[0],1)
 def test_startup_and_gap_censoring(self):
  self.ingest([],900);self.ingest([point(890),point(905)],910);self.ingest([point(890),point(905),point(930)],950)
  rows={r['source']:dict(r) for r in self.s.db.execute('SELECT * FROM points')}
  self.assertEqual(rows[890]['censored'],1);self.assertEqual(rows[905]['censored'],0);self.assertEqual(rows[930]['censored'],1);self.assertEqual(rows[905]['upper_delay'],5)
 def test_snapshot_requires_received_by_boundary(self):self.ingest([point()],1001);self.assertIsNone(self.s.snapshot('E',1000))
 def test_idempotent_fill_and_settle(self):
  aid,_=self.s.attempt('E',1000,'base',pick(),1000);self.assertTrue(self.s.fill(aid,.5,1004));self.assertFalse(self.s.fill(aid,.5,1005));self.assertEqual(self.s.attempt('E',1060,'base',pick(),1060)[1],'owned_or_pending')
  self.s.settle('KXATPMATCH-TEST-A',{'result':'yes'},1100,1);self.assertEqual(len(self.s.portfolio('base',1100)['settled']),0)
  self.s.settle('KXATPMATCH-TEST-A',{'result':'yes','settlement_ts':'2026-01-01T00:00:00Z'},1101,2);self.s.settle('KXATPMATCH-TEST-A',{'result':'no','settlement_ts':'2026-01-01T00:00:00Z'},1102,3)
  p=self.s.portfolio('base',1102);self.assertAlmostEqual(p['cash'],100.4725);self.assertEqual(p['reserved'],0);self.assertEqual(p['settled'][0]['settlement_receipt'],2)
 def test_pending_reserves_and_scenario_independence(self):
  aid,_=self.s.attempt('E',1000,'base',pick(),1000);self.assertEqual(self.s.attempt('E',1000,'stress',pick(),1000)[1],None)
  p=self.s.portfolio('base',1000);self.assertLess(p['free_cash'],100);self.assertGreater(p['reserved'],.52);self.s.fail(aid,'book_empty');self.assertEqual(self.s.portfolio('base',1000)['free_cash'],100)
 def test_daily_cap_includes_pending(self):
  self.cfg['daily_budget']=1
  self.s.attempt('E',1000,'base',pick(),1000);self.assertEqual(self.s.attempt('F',1000,'base',pick(),1000)[1],'daily_risk')
 def test_fill_over_cap_rejected(self):
  aid,_=self.s.attempt('E',1000,'base',pick(),1000)
  with self.assertRaises(AssertionError):self.s.fill(aid,.53,1004)
 def test_config_freeze(self):
  changed={**CFG,'edge':.05}
  with self.assertRaises(RuntimeError):Store(Path(self.tmp.name)/'paper.sqlite',changed)
 def test_readout_insufficient_evidence(self):
  s=summary(self.s);self.assertFalse(s['review']['sample_ready']);self.assertEqual(s['portfolios']['base']['net'],0)

class ObserverTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
  for f in ('frozen_config.json','model.py'):(root/f).write_bytes((P/f).read_bytes())
  self.o=Observer(root);self.o.clock_ok=True;self.s=self.o.store;self.clock=1000.;self.calls=[]
 async def asyncTearDown(self):self.s.db.close();self.tmp.cleanup()
 async def fake_sleep(self,d):self.clock+=d
 async def fake_get(self,path,params=None):
  self.calls.append((path,self.clock));self.clock+=.1
  if path.endswith('/orderbook'):body=book()
  else:body={'market':{'status':'active','close_time':'2030-01-01T00:00:00Z'}}
  r=dict(url=path,started=self.clock-.1,received=self.clock,duration=.1,status=200,headers={},body=body);rid,h=self.s.receipt(r);return {**r,'id':rid,'hash':h}
 async def test_execution_subsequent_books_and_stress(self):
  self.o.get=self.fake_get
  for scenario,delay,friction in [('base',3,.01),('stress',60,.02)]:
   start=self.clock;aid,_=self.s.attempt('E',int(start),'%s'%scenario,pick(),start)
   with patch('observer.time.time',lambda:self.clock),patch('observer.asyncio.sleep',self.fake_sleep):await self.o.execute(aid)
   pos=self.s.portfolio(scenario,self.clock)['active'][0];self.assertGreaterEqual(pos['entered'],start+delay+1);self.assertEqual(pos['friction'],friction);self.assertAlmostEqual(pos['price'],.5)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM book_checks').fetchone()[0],4)
 async def test_expired_restart_attempt_never_fills(self):
  aid,_=self.s.attempt('E',1000,'base',pick(),1000);self.clock=1020;self.o.get=self.fake_get
  with patch('observer.time.time',lambda:self.clock),patch('observer.asyncio.sleep',self.fake_sleep):await self.o.execute(aid)
  self.assertEqual(self.s.db.execute('SELECT reason FROM attempts WHERE id=?',(aid,)).fetchone()[0],'missed_execution_deadline');self.assertEqual(self.calls,[])
 async def test_second_book_vanishes(self):
  async def get(path,params=None):
   r=await self.fake_get(path,params)
   if len(self.calls)==3:r['body']=book(q=0)
   return r
  self.o.get=get;aid,_=self.s.attempt('E',1000,'base',pick(),1000)
  with patch('observer.time.time',lambda:self.clock),patch('observer.asyncio.sleep',self.fake_sleep):await self.o.execute(aid)
  self.assertEqual(len(self.s.portfolio('base',self.clock)['active']),0);self.assertEqual(self.s.db.execute('SELECT status FROM attempts WHERE id=?',(aid,)).fetchone()[0],'unfilled')
 async def test_public_only_client(self):
  c=Client()
  for p in ('/portfolio/orders','/portfolio/balance','/markets/X/../orders','https://example.com'):
   with self.assertRaises(ValueError):c.get(p)
 async def test_prospective_prior_and_reversed_score_mapping(self):
  info={'markets':[{'ticker':'KXATPMATCH-TEST-A','custom_strike':{'tennis_competitor':'A'}}],'milestone':{'details':{'first_competitor_id':'A','second_competitor_id':'B'}}}
  with self.s.db:self.s.db.execute('INSERT INTO events(event,series,start,milestone,info) VALUES(?,?,?,?,?)',('E','KXATPMATCH',2820,'abc',canonical(info)))
  async def candles(ms,b):return {ms[0]['ticker']:{b:candle(.68,.70)}},{'id':1,'received':1023}
  self.o.candles=candles
  await self.o.prior(self.s.db.execute('SELECT * FROM events').fetchone(),1023)
  row=self.s.db.execute('SELECT * FROM events').fetchone();before=json.loads(row['prior']);self.assertEqual(before['target_t'],1020);self.assertFalse(before['validated'])
  data={'pbp':{'periods':[{'home_competitor_id':'B','away_competitor_id':'A','events':[point(2850)]}]}}
  async def score(path,params=None):
   r=dict(url=path,started=2859.9,received=2860,duration=.1,status=200,headers={},body=data);rid,h=self.s.receipt(r);return {**r,'id':rid,'hash':h}
  self.o.get=score;await self.o.poll_score(row);after=json.loads(self.s.db.execute('SELECT prior FROM events').fetchone()[0]);self.assertTrue(after['validated']);self.assertEqual(after['home'],'B');self.assertEqual(after['pa'],before['pb']);self.assertAlmostEqual(after['probability'],1-before['probability'])
 async def test_late_prematch_input_rejected(self):
  with self.s.db:self.s.db.execute('INSERT INTO events(event,series,start,milestone,info,prior) VALUES(?,?,?,?,?,?)',('E','KXATPMATCH',900,'abc','{}',canonical(PRIOR)))
  async def score(path,params=None):
   r=dict(url=path,started=994.9,received=995,duration=.1,status=200,headers={},body=payload([point(790)]));rid,h=self.s.receipt(r);return {**r,'id':rid,'hash':h}
  self.o.get=score;await self.o.poll_score(self.s.db.execute('SELECT * FROM events').fetchone());p=json.loads(self.s.db.execute('SELECT prior FROM events').fetchone()[0]);self.assertFalse(p['validated']);self.assertEqual(p['invalid'],'prior_not_received_before_first_point')
 async def test_decision_end_to_end(self):
  info={'markets':[{'ticker':'KXATPMATCH-TEST-A','custom_strike':{'tennis_competitor':'A'}}]}
  with self.s.db:self.s.db.execute('INSERT INTO events(event,series,start,info,prior) VALUES(?,?,?,?,?)',('E','KXATPMATCH',800,canonical(info),canonical(PRIOR)))
  self.s.setmeta('clock',{'checked':1000});r=dict(url='score',started=994.9,received=995,duration=.1,status=200,headers={},body=payload([{**point(),'home_sets':1,'home_games':5,'home_score':40}]))
  rid,h=self.s.receipt(r);self.s.ingest_score('E',rid,h,995,994.9);self.o.series={'KXATPMATCH':1}
  async def candles(ms,b):return {ms[0]['ticker']:{b:candle()}},{'id':rid,'received':1003}
  self.o.candles=candles;self.clock=1003;row=self.s.db.execute('SELECT * FROM events WHERE event=?',('E',)).fetchone()
  async def defer(aid):pass
  self.o.execute=defer
  with patch('observer.time.time',lambda:self.clock):await self.o.decide(row,1000)
  await asyncio.sleep(0)
  self.assertEqual(self.s.db.execute('SELECT reason FROM decisions').fetchone()[0],'eligible');self.assertEqual(self.s.db.execute('SELECT count(*) FROM attempts').fetchone()[0],2)

if __name__=='__main__':unittest.main(verbosity=2)
