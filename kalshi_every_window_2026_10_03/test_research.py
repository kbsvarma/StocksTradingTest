"""Focused tests for causal timestamps, binary accounting and fill mechanics."""
import copy, json, math, pickle, unittest
from decimal import Decimal, ROUND_CEILING
import numpy as np
import screen as s
import maker
import validate_candidates as v
import forward_book_check as fb
import features
import paper
import tempfile
from pathlib import Path

class ResearchTests(unittest.TestCase):
    def test_fees_exact_decimal_grid(self):
        for ticks in (1,17,100,490,500,650,939,975,999):
            p=Decimal(ticks)/1000
            for n in (1,5):
                expected=(Decimal('.07')*p*(1-p)*n).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
                self.assertAlmostEqual(s.fee(float(p),n),float(expected),places=12)
    def test_complementary_sides(self):
        m={'candles':{120:{'bo':'.61','ao':'.63'}},'close':900}
        self.assertEqual(s.execution(m,60,1),(0.63,63))
        self.assertAlmostEqual(s.execution(m,60,-1)[0],.39)
        self.assertEqual(s.execution(m,60,1,False),(0.61,63))
        self.assertAlmostEqual(s.execution(m,60,-1,False)[0],.37)
    def test_no_final_price_as_late_fill(self):
        m={'candles':{900:{'bc':'.99','ac':'1.0','bo':'.99','ao':'1.0'}},'close':900}
        self.assertIsNone(s.execution(m,840,1))
        self.assertIsNone(s.execution(m,840,-1,delay=True))
    def test_maker_touch_not_fill(self):
        r={'created_time':'1970-01-01T00:01:02Z','taker_side':'no','yes_price_dollars':'.5','no_price_dollars':'.5','count_fp':'100000'}
        self.assertEqual(maker.through([r],60,1,.5)[0],0)
        r['yes_price_dollars']='.49';r['no_price_dollars']='.51'
        self.assertEqual(maker.through([r],60,1,.5)[0],1)
    def test_wrong_aggressor_does_not_fill(self):
        r={'created_time':'1970-01-01T00:01:02Z','taker_side':'yes','yes_price_dollars':'.49','no_price_dollars':'.51','count_fp':'10000'}
        self.assertEqual(maker.through([r],60,1,.5)[0],0)
    def test_latency_and_cancel_window(self):
        rr=[]
        for t in [.5,1.1,61.1]:
            rr.append({'_t':60+t,'taker_side':'no','yes_price_dollars':'.49','no_price_dollars':'.51','count_fp':'.25'})
        q,when,early=maker.through(rr,60,1,.5)
        self.assertEqual(q,.25);self.assertEqual(early,.25)
    def test_timestamp_fractional_precision(self):
        for t in ['1970-01-01T00:00:01.12Z','1970-01-01T00:00:01.120000Z','1970-01-01T00:00:01.120000000Z']:
            self.assertAlmostEqual(maker.timestamp(t),1.12)
    def test_multilevel_book_depth_and_direction(self):
        b=(123,[(.55,.4),(.54,2)],[(.43,.4),(.42,2)])
        self.assertAlmostEqual(v.fill(b,1)[0],.576)
        self.assertAlmostEqual(v.fill(b,-1)[0],.456)
        self.assertIsNone(v.fill(b,1,3))
        self.assertAlmostEqual(fb.walk({'yes':dict(b[1]),'no':dict(b[2])},1),.576)
    def test_simple_trade_accounting_and_settlement(self):
        m=dict(ticker='T',asset='BTC',start=0,close=900,settled=909,result=-1,cohort='fit',candles={120:{'bo':'.61','ao':'.63'}})
        f=dict(t=60)
        tr=s.trade(m,f,-1);self.assertAlmostEqual(tr['pnl'],1-.39-s.fee(.39));self.assertEqual(tr['exit_ts'],909)
        tr=s.trade(m,f,1);self.assertAlmostEqual(tr['pnl'],-.63-s.fee(.63))
    def test_future_quote_changes_cannot_change_entry_side(self):
        ms=pickle.loads((s.P/'prepared.pickle').read_bytes());m=next(m for m in ms if m['asset']=='BTC' and m['cohort']=='test')
        f=m['new_features'][12];before=[s.side(f,p) for p in s.POLICIES]
        altered=copy.deepcopy(m);altered['result']*=-1
        for t,c in altered['candles'].items():
            if t>f['t']:c.update(bo='.001',ao='.002',bc='.999',ac='1.0')
        self.assertEqual(before,[s.side(altered['new_features'][12],p) for p in s.POLICIES])
    def test_online_no_future_settlements(self):
        x=json.loads((s.P/'online_models.json').read_text())
        for models in x.values():
            for day,model in models.items():self.assertLess(model['latest_settlement'],int(day))
    def test_frozen_models_finite_and_symmetric(self):
        x=json.loads((s.P/'models.json').read_text())
        for model in x.values():
            coef=np.array(model['coef']);scale=np.array(model['scale'])
            self.assertTrue(np.isfinite(coef).all());self.assertTrue((scale>0).all());self.assertLess(abs(coef[0]),1e-10)
    def test_data_denominator_complete_quarters(self):
        ms=pickle.loads((s.P/'prepared.pickle').read_bytes());keys=[m['ticker'] for m in ms]
        self.assertEqual(len(keys),len(set(keys)))
        for m in ms:
            self.assertEqual(m['close']-m['start'],900)
            for rem,f in m['new_features'].items():self.assertEqual(f['t'],m['close']-60*rem)
    def test_paper_btc_priority_and_deadline(self):
        offers=[dict(asset='BTC',edge=-.01),dict(asset='ETH',edge=.02)]
        self.assertEqual(paper.choose(offers,8,True)[0][0]['asset'],'ETH')
        offers[0]['edge']=.011
        self.assertEqual(paper.choose(offers,8,True)[0][0]['asset'],'BTC')
        offers[0]['edge']=-.01;offers[1]['edge']=-.02
        self.assertEqual(paper.choose(offers,2,True)[0],[])
        self.assertEqual(paper.choose(offers,1,True)[0][0]['asset'],'BTC')
        self.assertEqual(paper.choose(offers,1,False)[0],[])
    def test_paper_full_quantity_and_zero_endpoints(self):
        b={'orderbook_fp':{'yes_dollars':[['.55','.4'],['.54','2']], 'no_dollars':[['.43','.4'],['.42','2']]}}
        self.assertAlmostEqual(paper.price(b,1)['price'],.576)
        self.assertIsNone(paper.price(b,1,3))
        self.assertIsNone(paper.price({'orderbook_fp':{'no_dollars':[['0','2000']]}},1))
    def test_paper_unique_cycle_and_cash(self):
        with tempfile.TemporaryDirectory() as d:
            db=paper.init_db(Path(d)/'paper.db')
            row=('force',900,'T','BTC',120,123,1,.6,.0168,.02,0,'open',None,None)
            db.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row)
            db.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row);db.commit()
            st=paper.stats(db)['force'];self.assertEqual(st['entries'],1);self.assertAlmostEqual(st['virtual_cash'],100-.6-.0168-.0025)
            db.close()
    def test_paper_transport_rejects_private_paths(self):
        with self.assertRaises(ValueError):paper.Public().get('/portfolio/positions')
        with self.assertRaises(ValueError):paper.Public().get('/portfolio/orders')
    def test_completed_feature_parity_and_missing_bar(self):
        bars={a:{} for a in ('BTC','ETH')}
        for folder in s.FOLDERS:
            for a in bars:
                path=folder/('spot_'+a+'.jsonl')
                if path.exists():
                    for line in path.open():
                        for row in json.loads(line)['rows']:bars[a][int(row[0])]=float(row[4])
        ms=pickle.loads((s.P/'prepared.pickle').read_bytes());count=0
        for m in ms[::11]:
            for rem in (14,8,1):
                f=m['new_features'].get(rem)
                if not f or 'xrich' not in f:continue
                a=m['asset'];other='ETH' if a=='BTC' else 'BTC';q=s.quote(m,f['t']);prev=s.quote(m,f['t']-60)
                fresh=features.build(f['t'],m['close'],m['start'],q[0],q[1],prev,bars[a],bars[other])
                self.assertTrue(np.allclose(fresh['xrich'],f['xrich'],rtol=1e-11,atol=1e-11));count+=1
                if count==1:
                    shortened={t:v for t,v in bars[a].items() if t!=f['t']-120}
                    self.assertIsNone(features.build(f['t'],m['close'],m['start'],q[0],q[1],prev,shortened,bars[other]))
                    future=dict(bars[a]);future[f['t']]=1e100
                    unchanged=features.build(f['t'],m['close'],m['start'],q[0],q[1],prev,future,bars[other])
                    self.assertEqual(fresh,unchanged)
        self.assertGreater(count,2500)

if __name__=='__main__':unittest.main()
