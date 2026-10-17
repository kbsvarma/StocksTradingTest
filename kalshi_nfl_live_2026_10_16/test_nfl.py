import copy,json,tempfile,unittest
from pathlib import Path
from core import *
from store import Store
from stream_transport import BookState,FillState,StreamTransport,subscription
P=Path(__file__).resolve().parent
CFG=json.loads((P/'config.json').read_text())
T=ts('2026-10-18T16:30:00Z');EV='KXNFLGAME-26OCT18HOUJAC';TICK=EV+'-HOU'
def market(team):return dict(ticker=EV+'-'+team,rules_secondary='A tie pays $0.50.',status='active',exchange_index=0,open_time='2026-10-01T00:00:00Z',close_time='2026-10-20T00:00:00Z',price_ranges=[dict(start='0',end='1',step='.01')])
def play(clock=200,home=10,away=20,wall=T-40):return dict(id='p1',seq=1,wall=wall,period=4,clock=clock,home=home,away=away,received=T-2,version=1)
def pick():return dict(event=EV,ticker=TICK,side='yes',team='HOU',ask='.92',bid='.91',cap='.93',yes_limit='.93',boundary=int(T),state=play(),quantity=5,exchange_index=0,subaccount=0)
def raw(p):return dict(plays=[dict(id=p['id'],sequenceNumber=str(p['seq']),wallclock=dt.datetime.fromtimestamp(p['wall'],dt.timezone.utc).isoformat(),period=dict(number=p['period']),clock=dict(displayValue='3:20'),homeScore=p['home'],awayScore=p['away'])])
def receipt(p,at):return dict(body=raw(p),received=at,started=at-.05,duration=.05,status=200,headers={},error=None)
def book():return dict(yes_dollars=[['.91','10']],no_dollars=[['.08','3'],['.07','4']])
class Strategy(unittest.TestCase):
    def choose(self,ask='.92',spread='.01',vol='5',p=None):
        ms=[market('HOU'),market('JAC')];c={TICK:{int(T):dict(yes_bid=dict(close_dollars=str(dec(ask)-dec(spread))),yes_ask=dict(close_dollars=ask),volume_fp=vol)}}
        return choose(dict(event=EV,teams={'home':'JAX','away':'HOU'}),ms,c,p or play(),int(T))
    def test_frozen_signal(self):
        p,why=self.choose();self.assertIsNone(why);self.assertEqual(p['quantity'],5);self.assertEqual(dec(p['cap']),dec('.93'))
    def test_quote_boundaries(self):
        for ask,ok in [('.899',False),('.90',True),('.94',True),('.95',False)]:self.assertEqual(self.choose(ask=ask)[0] is not None,ok)
    def test_spread_and_volume(self):self.assertIsNone(self.choose(spread='.03')[0]);self.assertIsNone(self.choose(vol='4.99')[0])
    def test_clock_tie_overtime(self):
        for p in [play(clock=301),play(clock=0),play(home=20)]:self.assertIsNone(self.choose(p=p)[0])
        p=play();p['period']=5;self.assertIsNone(self.choose(p=p)[0])
    def test_depth_vwap(self):
        f,w=depth(book(),'yes','.93');self.assertIsNone(w);self.assertEqual(dec(f['cost']),dec('4.62'))
    def test_depth_cap_and_size(self):self.assertEqual(depth(book(),'yes','.92')[1],'insufficient_depth_inside_cap')
    def test_crossed_rejected(self):b=book();b['yes_dollars']=[['.95','10']];self.assertEqual(depth(b,'yes','.96')[1],'crossed_book')
    def test_no_coordinates(self):p=pick();p.update(side='no',yes_limit='.07');q=order_payload(p,'x');self.assertEqual((q['side'],q['price'],q['count']),('ask','0.0700','5.00'))
    def test_wrong_size_route(self):
        for k,v in [('quantity',10),('exchange_index',2),('ticker','KXBTC15M-X'),('subaccount',1)]:
            p=pick();p[k]=v
            with self.assertRaises(ValueError):order_payload(p,'x')
    def test_fees(self):self.assertEqual(fee('.92'),dec('.0258'))
    def test_bad_quotes(self):self.assertIsNone(quote(None));self.assertIsNone(quote(dict(yes_bid=dict(close_dollars='NaN'),yes_ask=dict(close_dollars='x'))))
    def test_no_future_source(self):
        p=play();future=play(home=30);future.update(received=T+1,version=2);self.assertEqual(state_at([p,future],T,CFG)['away'],20)
    def test_source_age(self):self.assertIsNone(state_at([play(wall=T-121)],T,CFG));self.assertIsNone(state_at([play(wall=T-29)],T,CFG))
    def test_live_unfinished_drive(self):
        p=raw(play())['plays'][0];self.assertEqual(len(parse_plays(dict(drives=dict(previous=[],current=dict(plays=[p]))))),1)
    def test_duplicate_source_consistency(self):
        p=raw(play())['plays'][0];self.assertEqual(len(parse_plays(dict(plays=[p],drives=dict(current=dict(plays=[p]))))),1)
        q=copy.deepcopy(p);q['homeScore']=99
        with self.assertRaises(RuntimeError):parse_plays(dict(plays=[q],drives=dict(current=dict(plays=[p]))))
class Ledger(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db',CFG)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def test_bootstrap_excluded(self):self.s.ingest(EV,receipt(play(),T));self.assertEqual(self.s.db.execute('SELECT reason FROM plays').fetchone()[0],'startup')
    def test_first_arrival_and_correction(self):
        p=play();self.s.ingest(EV,receipt(p,T));p['id']='p2';p['seq']=2;p['wall']=T+1
        self.s.ingest(EV,receipt(p,T+2));p['home']=21;self.s.ingest(EV,receipt(p,T+4))
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT reason FROM plays ORDER BY received')],['startup','clean','revision'])
        old,_=self.s.source_at(EV,T+3);new,_=self.s.source_at(EV,T+5);self.assertEqual(old[0]['home'],10);self.assertEqual(new[0]['home'],21)
    def test_source_receipt_gap(self):self.s.ingest(EV,receipt(play(),T));self.assertIsNone(self.s.source_at(EV,T+9)[1])
    def test_missing_play_not_reused(self):
        self.s.ingest(EV,receipt(play(),T));r=receipt(play(),T+2);r['body']['plays']=[];self.s.ingest(EV,r);self.assertEqual(self.s.source_at(EV,T+3)[0],[])
    def test_game_dedup_both_routes(self):
        self.assertIsNotNone(self.s.reserve(pick(),'verify',T)[0]);p=pick();p.update(ticker=EV+'-JAC',side='no',boundary=int(T)+60)
        self.assertEqual(self.s.reserve(p,'verify',T+60)[1],'already_owned')
    def test_retry_only_after_zero_on_new_signal(self):
        i,_=self.s.reserve(pick(),'verify',T);self.s.fail(i,'no_depth');self.assertEqual(self.s.reserve(pick(),'verify',T)[1],'already_attempted')
        p=pick();p['boundary']+=60;self.assertIsNotNone(self.s.reserve(p,'verify',T+60)[0])
    def test_settlement_cost_and_tie(self):
        i,_=self.s.reserve(pick(),'verify',T);self.s.finish_simulated(i,dec('.92'),T,{})
        self.s.settle(dict(ticker=TICK,status='finalized',settlement_ts=T+100,result='scalar',settlement_value_dollars='.5'),T+100)
        r=self.s.db.execute('SELECT * FROM orders').fetchone();self.assertEqual(dec(r['net']),dec('-2.1758'))
    def test_unknown_keeps_risk(self):
        i,_=self.s.reserve(pick(),'live',T);self.s.db.execute("UPDATE orders SET status='unknown' WHERE id=?",(i,));self.s.db.commit();self.s.fail(i,'anything');self.assertEqual(self.s.db.execute('SELECT status FROM orders').fetchone()[0],'unknown')
        p=pick();p['event']='other';self.assertEqual(self.s.reserve(p,'live',T)[1],'unresolved_order')
    def test_budget_counts_open_risk(self):
        for n in range(4):p=pick();p['event']=str(n);self.assertIsNotNone(self.s.reserve(p,'verify',T)[0])
        p=pick();p['event']='fifth';self.assertEqual(self.s.reserve(p,'verify',T)[1],'daily_loss_limit')
class Transport(unittest.TestCase):
    def frame(self,seq=1,kind='orderbook_snapshot'):
        return dict(type=kind,sid=1,seq=seq,sending_ts_ms=T*1000,msg=dict(market_ticker=TICK,yes_dollars_fp=[['.91','10']],no_dollars_fp=[['.92','10']]))
    def test_snapshot_coordinates(self):b=BookState();b.apply(self.frame(),T,1);self.assertEqual(b.view(TICK,2)['no_dollars'],[['0.08','10']])
    def test_gap_invalidates_every_book(self):
        b=BookState();b.apply(self.frame(),T,1)
        with self.assertRaises(ValueError):b.apply(self.frame(3),T,2)
        self.assertIsNone(b.view(TICK,2))
    def test_stale_frame(self):
        with self.assertRaises(ValueError):BookState().apply(self.frame(),T+2,1)
    def test_expired_snapshot(self):b=BookState();b.apply(self.frame(),T,1);self.assertIsNone(b.view(TICK,27))
    def test_multiple_outcomes_and_games_retained(self):
        import time
        s=StreamTransport(lambda:{},start=False)
        for t in [TICK,EV+'-JAC','KXNFLGAME-26OCT18BALCLE-BAL']:s.watch(t,time.time()+60)
        self.assertEqual(len(s.targets),3)
    def test_forbidden_channel(self):
        with self.assertRaises(ValueError):subscription(1,'pyth')
    def test_no_fill_is_not_zero_confirmation(self):self.assertIsNone(FillState().confirmed(dict(status='canceled'),dict(order_id='x',ticker=TICK)))
if __name__=='__main__':unittest.main()
