import asyncio,json,tempfile,unittest,time,hashlib
from pathlib import Path
from unittest.mock import patch
from engine import reconstruct,book,fee,choose,cap_price,payout,ts
from store import Store,status
from observer import Client,Observer

CFG={'paper_only':True,'contracts':5,'bankroll':100,'max_contracts':25,'max_exposure':25,'daily_budget':20,'min_cash':2,'friction':{'early':.01,'delay60':.02,'canonical':.01},'delays':{'early':3,'delay60':60},'max_source_poll_gap':45,'max_source_age':300,'min_remaining':60,'max_remaining':300,'min_training':60,'min_volume10':5,'market_weight':.5,'max_spread':.02,'min_ask':.5,'max_ask':.95,'edge':.03,'cap_increment':.01,'max_cap':.95,'cities':['KXTEMPMIAH'],'stop_days':14,'execution_deadline':8,'second_book_seconds':1,'max_http':5,'frozen_files':{}}
def ob(ask=.6,bid=.59,qty=5):return {'orderbook_fp':{'yes_dollars':[[str(bid),'50']],'no_dollars':[[str(round(1-ask,4)),str(qty)]]}}
def market(close=1260):return {'ticker':'KXTEMPMIAH-E-T80','event_ticker':'KXTEMPMIAH-E','status':'active','strike_type':'greater','floor_strike':80,'close_time':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(close)),'price_level_structure':'linear_cent'}
def pick(close=9999999999):return {'event':'KXTEMPMIAH-E','series':'KXTEMPMIAH','ticker':'KXTEMPMIAH-E-T80','side':'yes','ask':.6,'cap':.61,'close':close,'source':{'source_t':600}}
class Paper(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'p.sqlite',CFG)
    def tearDown(self):self.store.db.close();self.temp.cleanup()
    def test_fee_rounding(self):self.assertEqual(fee(.61,5),.0833)
    def test_fractional_depth(self):
        b=ob();b['orderbook_fp']['no_dollars']=[['.40','2.5'],['.39','2.5']]
        x,w=book(b,'yes',.61,5);self.assertIsNone(w);self.assertAlmostEqual(x['vwap'],.605)
        self.assertEqual(book(b,'yes',.60,5)[1],'insufficient_depth_inside_cap')
    def test_crossed_and_empty(self):
        self.assertEqual(book(ob(.6,.7))[1],'crossed_book');self.assertEqual(book({'orderbook_fp':{}})[1],'empty_book')
    def test_no_side_depth(self):
        x,w=book(ob(),'no',.41,5);self.assertIsNone(w);self.assertAlmostEqual(x['ask'],.41)
    def test_invalid_depth(self):self.assertEqual(book(ob(qty='nan'))[1],'empty_book')
    def test_tick_cap(self):
        m=market();self.assertEqual(cap_price(m,.639,CFG),.64);m['price_level_structure']='unknown';self.assertIsNone(cap_price(m,.639,CFG))
    def test_source_configuration_and_roster(self):
        cfg=[{'published_at_ms':500000,'effective_at_ms':0,'config_version':'v1','city_reference_c':0,'stations':[{'station_id':'A','weight':1,'offset_c':1}]}]
        point={'t':600000,'stations':[{'station_id':'A','source':'hf_asos','temp_f':68,'received_at_ms':610000,'code':'pending'}]}
        r,w=reconstruct(point,cfg,620);self.assertIsNone(w);self.assertAlmostEqual(r['forecast'],66.2);self.assertTrue(r['pending'])
        self.assertEqual(reconstruct(point,cfg,400)[1],'no_known_configuration')
        point['stations'][0]['code']='rejected';self.assertEqual(reconstruct(point,cfg,620)[1],'explicit_qc_rejection')
        self.assertIsNone(reconstruct(point,cfg,620,False)[1]);point['stations']=[];self.assertEqual(reconstruct(point,cfg,620)[1],'incomplete_roster')
    def test_source_startup_and_arrival(self):
        cfg=[{'published_at_ms':0,'effective_at_ms':0,'config_version':'v1','city_reference_c':0,'stations':[{'station_id':'A','weight':1,'offset_c':0}]}]
        p={'t':600000,'stations':[{'station_id':'A','source':'hf_asos','temp_f':68,'received_at_ms':610000}]}
        def r(received):return {'id':1,'received':received,'started':received-.1,'body':{'timeseries':[p]}}
        self.assertIsNone(self.store.ingest('KXTEMPMIAH',r(620),cfg,True))
        self.assertIsNone(self.store.ingest('KXTEMPMIAH',r(635),cfg,False))
        p['t']=660000;p['stations'][0]['received_at_ms']=665000
        self.store.setmeta('source_poll_KXTEMPMIAH',{'received':650,'started':649})
        source=self.store.ingest('KXTEMPMIAH',r(670),cfg,False);self.assertEqual(source['first_seen'],670)
        p['v']=68;self.assertIsNone(self.store.ingest('KXTEMPMIAH',r(680),cfg,False))
        self.assertEqual(self.store.rows('SELECT received FROM publications')[0]['received'],680)
    def test_choice_gates_and_side(self):
        m=market();b={m['ticker']:ob()};s={'forecast':81,'source_t':960}
        tr={'errors':{'KXTEMPMIAH':{'5':[0]*100}}};vol={m['ticker']:5}
        x,c=choose([m],b,s,tr,1100,vol,CFG);self.assertIsNotNone(x);self.assertEqual(x['side'],'yes')
        self.assertIsNone(choose([m],b,s,tr,1259,vol,CFG)[0])
        self.assertIsNone(choose([m],b,s,tr,1100,{},CFG)[0])
        self.assertIsNone(choose([m],b,s,{'errors':{}},1100,vol,CFG)[0])
    def test_price_not_qualified(self):
        m=market();s={'forecast':81,'source_t':960};tr={'errors':{'KXTEMPMIAH':{'5':[0]*100}}}
        self.assertIsNone(choose([m],{m['ticker']:ob(.99,.98)},s,tr,1100,{m['ticker']:5},CFG)[0])
    def test_ownership_and_reservations(self):
        p=pick();a=self.store.attempt(1,'early',p,1000);self.assertTrue(a)
        self.assertIsNone(self.store.attempt(2,'early',p,1001));self.assertTrue(self.store.attempt(1,'delay60',p,1000))
        self.assertEqual(self.store.portfolio('early',1000)['contracts'],5)
        self.store.fill(a,.6,1004);self.assertFalse(self.store.fill(a,.6,1005))
        self.assertIsNone(self.store.attempt(3,'early',{**p,'event':'KXTEMPMIAH-E2'},1006))
    def test_settlement_cash_and_no_early_release(self):
        a=self.store.attempt(1,'early',pick(),1000);self.store.fill(a,.6,1004)
        m={'ticker':pick()['ticker'],'result':'yes'};self.store.settle(m,{'received':1100,'id':9});self.assertEqual(len(self.store.portfolio('early',1100)['active']),1)
        m['settlement_ts']='1970-01-01T00:18:00Z';self.store.settle(m,{'received':1200,'id':10});self.store.settle(m,{'received':1300,'id':11})
        p=self.store.portfolio('early',1300);self.assertAlmostEqual(p['cash'],101.866);self.assertEqual(p['settled'][0]['settlement_receipt'],10)
    def test_cash_and_risk_rejection(self):
        self.store.cfg={**CFG,'bankroll':4};self.assertIsNone(self.store.attempt(1,'early',pick(),1000))
        self.store.cfg={**CFG,'daily_budget':3};self.assertIsNone(self.store.attempt(2,'early',pick(),1000))
    def test_config_guard(self):
        with self.assertRaises(RuntimeError):Store(Path(self.temp.name)/'p.sqlite',{**CFG,'contracts':1})
    def test_no_unauthorized_routes(self):
        async def request(route):return await Client().get(route)
        for route in ['/portfolio/orders','/portfolio/balance','/markets/ABC/submit','https://example.com']:
            with self.assertRaises(ValueError):asyncio.run(request(route))
    def test_scalar_settlement(self):
        self.assertEqual(payout({'settlement_ts':'x','settlement_value_dollars':'.25'},'no'),.75)

class Execution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        (root/'frozen_config.json').write_text(json.dumps(CFG));(root/'training.json').write_text(json.dumps({'end':0}))
        self.o=Observer(root);self.o.clock_ok=True;self.now=time.time();self.p=pick(self.now+100)
        self.a=self.o.store.attempt(1,'early',self.p,self.now-3)
        self.row=self.o.store.rows('SELECT * FROM attempts WHERE id=?',(self.a,))[0];self.count=0
    async def asyncTearDown(self):self.o.db.close();self.tmp.cleanup()
    async def execute(self,second_ask=.6):
        async def get(path,params=None):return {'id':1,'body':{'markets':[dict(market(self.now+100),ticker=self.p['ticker'])]}}
        async def batch(tickers):
            self.count+=1;return {'id':self.count+1,'received':time.time(),'books':{self.p['ticker']:ob(second_ask if self.count>1 else .6)}}
        self.o.get=get;self.o.batch=batch
        await self.o.execute([self.row])
    async def test_two_persistent_books_fill(self):
        await self.execute();self.assertEqual(len(self.o.store.rows('SELECT * FROM positions')),1);self.assertEqual(self.count,2)
    async def test_second_book_vanishes(self):
        await self.execute(.7);self.assertFalse(self.o.store.rows('SELECT * FROM positions'));self.assertEqual(self.o.store.rows('SELECT status FROM attempts')[0]['status'],'unfilled')
    async def test_expired_execution_does_not_fill(self):
        self.row['due']=time.time()-10;await self.execute();self.assertEqual(self.count,0)

if __name__=='__main__':unittest.main()
