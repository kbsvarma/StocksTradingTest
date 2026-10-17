import dataclasses,importlib.util,json,math,pickle,random,socket,sqlite3,statistics,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from strategy import RULES,qualifies,tick_cap,book_fill,fee
import paper_bot as pb
from research import P,replay
from portfolio import portfolio

BANDS=[{'start':'0','end':'.1','step':'.001'},{'start':'.1','end':'.9','step':'.01'},{'start':'.9','end':'1','step':'.001'}]

class RuleTests(unittest.TestCase):
    def test_exact_selected_boundaries(self):
        r=RULES[6];f=dict(rem=3,ask=.979,bid=.978,z=3,side=1)
        self.assertTrue(qualifies(r,f));self.assertFalse(qualifies(r,dict(f,z=2.999999)))
        self.assertFalse(qualifies(r,dict(f,ask=.98)));self.assertFalse(qualifies(r,dict(f,rem=2)))
    def test_value_threshold_not_lower_z_only(self):
        r=RULES[9];f=dict(rem=7,ask=.85,bid=.84,z=1.5,side=1)
        self.assertFalse(qualifies(r,f))
        self.assertTrue(qualifies(r,dict(f,ask=.84,bid=.83)))
        self.assertFalse(qualifies(r,dict(f,ask=.84,bid=.80)))
    def test_yes_no_tick_cap(self):
        for side in (1,-1):
            self.assertAlmostEqual(tick_cap(RULES[0],dict(side=side,ask=.945),BANDS),.939)
            self.assertAlmostEqual(tick_cap(RULES[6],dict(side=side,ask=.976),BANDS),.975)
            self.assertAlmostEqual(tick_cap(RULES[9],dict(side=side,ask=.847),BANDS),.85)
    def test_displayed_quantity_and_all_levels_inside_cap(self):
        b=(100,(),((.08,.5),(.07,.6)))
        self.assertIsNone(book_fill(b,1,.925,.85,1))
        got=book_fill(b,1,.93,.85,1);self.assertAlmostEqual(got[0],.925)
        self.assertIsNone(book_fill(b,1,.939,.85,2))
    def test_floor_checks_best_price(self):
        self.assertIsNone(book_fill((0,(),((.16,.1),(.07,100))),1,.94,.85,1))
    def test_fee_rounding(self):
        self.assertAlmostEqual(fee(.95,5),.0167)
        self.assertAlmostEqual(fee(.95,5,.01),.02)

class CausalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms=pickle.loads((P/'features.pickle').read_bytes())
    def test_actual_scanner_control_and_volatility(self):
        source=P.parent/'kalshi_signal95_upgrade_2026_10_02'
        spec=importlib.util.spec_from_file_location('sizing',source/'sizing.py');size=importlib.util.module_from_spec(spec);spec.loader.exec_module(size);sys.modules['sizing']=size
        spec=importlib.util.spec_from_file_location('current_scanner',source/'market_data.py');scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)
        # Independent raw bars for exactly one source window, all four assets.
        bars={a:{} for a in ('BTC','ETH','SOL','XRP')}
        folder=P.parent/'kalshi_restart_audit_2026_09_12/fresh_data'
        for a in bars:
            for line in (folder/f'spot_{a}.jsonl').read_text().splitlines():
                for row in json.loads(line)['rows']:bars[a][int(row[0])]=float(row[4])
        pairs=[(m,f) for m in self.ms for f in m['features'] if m['close']<1789171200 and 3<=f['rem']<=10]
        random.Random(23).shuffle(pairs);checked=0
        for m,f in pairs[:300]:
            T=f['t'];bb=bars[m['asset']]
            if not all(k in bb for k in range(T-3660,T,60)):continue
            rebuilt=pb.feature(T,m['start'],m['close'],float(m['candles'][T]['bc']),float(m['candles'][T]['ac']),bb)
            self.assertAlmostEqual(rebuilt['z'],f['z'],places=9)
            self.assertAlmostEqual(rebuilt['sigma'],f['sigma'],places=12)
            fake_market=dict(ticker=m['ticker'],open_time=__import__('datetime').datetime.fromtimestamp(m['start'],__import__('datetime').timezone.utc).isoformat(),close_time=__import__('datetime').datetime.fromtimestamp(m['close'],__import__('datetime').timezone.utc).isoformat(),exchange_index=2,price_ranges=m['ranges'])
            def get(url):
                if '/markets?' in url:return {'markets':[fake_market]}
                if '/candlesticks?' in url:return {'candlesticks':[{'end_period_ts':T,'yes_bid':{'close_dollars':m['candles'][T]['bc']},'yes_ask':{'close_dollars':m['candles'][T]['ac']}}]}
                if 'coinbase.com' in url:return [[k,0,0,0,bb[k],0] for k in range(T-3660,T,60)]+[[T,0,0,0,1e30,0]]
                if '/orderbook?' in url:return {'orderbook_fp':{'no_dollars' if f['side']==1 else 'yes_dollars':[[str(1-f['ask']),'100']]}}
                raise AssertionError(url)
            with patch.object(scanner,'get',get),patch.object(scanner.time,'time',return_value=T+3):r=scanner.scan(m['asset'],T,set())
            self.assertEqual(r['status']=='paper_fill',qualifies(RULES[0],f))
            checked+=1
        self.assertGreaterEqual(checked,290)
    def test_future_bar_cannot_change_paper_feature(self):
        T=10000020;bb={T-60*i:100+math.sin(i)*.02+i*.01 for i in range(1,100)}
        a=pb.feature(T,T-300,T+600,.95,.96,bb)
        bb.update({T:1e99,T+60:1e-99})
        self.assertEqual(a,pb.feature(T,T-300,T+600,.95,.96,bb))
        del bb[T-120]
        with self.assertRaisesRegex(ValueError,'incomplete'):pb.feature(T,T-300,T+600,.95,.96,bb)
    def test_book_cannot_precede_signal_or_exceed_deadline(self):
        f=dict(t=1000,rem=5,side=1,bid=.95,ask=.96,z=3.2)
        m=dict(ticker='X',asset='BTC',features=[f],ranges=BANDS,close=1300,settled=1330,result=1,candles={})
        b=(1000.2,(),((.04,100),));after=(1035.1,(),((.04,100),))
        rr,_=replay([m],None,{'X':[b,after]},rules=[RULES[6]])
        self.assertFalse(rr)
        rr,_=replay([m],None,{'X':[(1000.3,(),((.04,100),))]},rules=[RULES[6]])
        self.assertEqual(len(rr['premium_z3']),1)

class PortfolioTests(unittest.TestCase):
    def test_risk_reserves_and_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'x.sqlite3';s=pb.Store(path)
            self.assertTrue(s.enter({'ticker':'X'}));s.db.close()
            s=pb.Store(path);self.assertFalse(s.enter({'ticker':'X'}));self.assertEqual(len(s.rows()),1)
        active=[dict(asset=a,status='open',entry=.97,fee=.003) for a in ('BTC','ETH','SOL','XRP')]
        self.assertEqual(pb.risk_reason(active,'DOGE',1000),'contract_cap')
        closed=[dict(asset='BTC',status='settled',settled_day=pb.etday(1000),pnl_after_stress=-4.2)]
        self.assertEqual(pb.risk_reason(closed,'ETH',1000),'daily_loss_budget')
    def test_settlement_not_released_before_observed_time(self):
        row=dict(ticker='A',asset='BTC',entered=100,quantity=1,settled=200,close=180,entry=.96,fee=.003,pnl=.037,pnl_cap=.037,cap=.96,won=1,day='1970-01-01')
        rows={'premium_z3':[row,dict(row,ticker='B',asset='ETH',entered=150),dict(row,ticker='B',asset='ETH',entered=201,settled=300,close=280)]}
        rr,blocked=portfolio(rows,['premium_z3'],max_contracts=1,max_risk=1)
        self.assertEqual([r['entered'] for r in rr],[100,201]);self.assertEqual(blocked['contracts'],1)
    def test_public_transport_rejects_account_urls(self):
        for url in ('https://api.elections.kalshi.com/trade-api/v2/portfolio/positions','https://api.elections.kalshi.com/trade-api/v2/portfolio/orders','http://api.elections.kalshi.com/trade-api/v2/markets','https://example.com/products/BTC'):
            with self.assertRaises(ValueError):pb.get(url)
    def test_unsettled_result_not_booked(self):
        with tempfile.TemporaryDirectory() as d:
            s=pb.Store(Path(d)/'x.sqlite3');s.enter(dict(ticker='X',status='open',close=0))
            with patch.object(pb,'get',return_value={'market':{'status':'open','result':'yes'}}):self.assertEqual(pb.reconcile(s),[])
            self.assertEqual(s.rows()[0]['status'],'open')
    def test_actual_finalized_market_response_settles(self):
        # Use the actual market response from the historical public API cache,
        # rather than inventing its status from the status=settled query filter.
        source=P.parent/'kalshi_restart_audit_2026_09_12/fresh_data/markets_BTC.jsonl'
        with source.open() as inp:market=json.loads(next(inp))
        self.assertEqual(market['status'],'finalized')
        with tempfile.TemporaryDirectory() as d:
            s=pb.Store(Path(d)/'x.sqlite3')
            s.enter(dict(ticker=market['ticker'],status='open',close=pb.timestamp(market['close_time']),
                side=market['result'],entry=.96,fee=.0027))
            with patch.object(pb,'get',return_value={'market':market}),patch.object(pb.time,'time',return_value=pb.timestamp(market['close_time'])+3600):
                self.assertFalse(pb.reconcile(s));self.assertFalse(pb.reconcile(s))
            row=s.rows()[0]
            self.assertEqual(row['status'],'settled');self.assertEqual(row['result'],market['result'])
            self.assertAlmostEqual(row['pnl_after_stress'],.0273)
    def test_paper_entry_settlement_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            s=pb.Store(Path(d)/'x.sqlite3')
            rec=dict(ticker='KXBTC15M-TEST',asset='BTC',t=1000,close=1300,lanes=['premium_z3'],price_ranges=BANDS,
                reference_current_qualifies=False,feature=dict(t=1000,side=1,ask=.96,bid=.95,z=3.2,rem=5))
            with patch.object(pb.time,'time',return_value=1003),patch.object(pb,'get',return_value={'orderbook_fp':{'yes_dollars':[['.95','10']],'no_dollars':[['.04','5']]}}):
                out=pb.observe_book(rec,s);self.assertEqual(out['status'],'simulated_entry')
                self.assertEqual(pb.observe_book(rec,s)['status'],'already_entered')
            with patch.object(pb.time,'time',return_value=1400),patch.object(pb,'get',return_value={'market':{'status':'settled','result':'yes','close_time':'1970-01-01T00:21:40Z'}}):
                self.assertFalse(pb.reconcile(s));self.assertFalse(pb.reconcile(s))
            self.assertEqual(len(s.rows()),1);self.assertEqual(s.rows()[0]['status'],'settled')
            self.assertAlmostEqual(s.rows()[0]['pnl_after_stress'],.0273)
    def test_late_book_is_never_entered(self):
        with tempfile.TemporaryDirectory() as d:
            s=pb.Store(Path(d)/'x.sqlite3');rec=dict(ticker='X',asset='BTC',t=1000)
            with patch.object(pb.time,'time',return_value=1036):
                self.assertEqual(pb.observe_book(rec,s)['status'],'signal_too_old')
            self.assertFalse(s.rows())

if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No network in tests')):unittest.main()
