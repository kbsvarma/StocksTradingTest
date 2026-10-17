import asyncio,json,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
from core import *
from executor import Executor,validated_terminal
from store import Store
from test_nfl import CFG,T,EV,TICK,pick,play,receipt,book
def order(**kwargs):
    return dict(dict(order_id='o1',ticker=TICK,client_order_id='c1',status='executed',initial_count_fp='5',fill_count_fp='5',remaining_count_fp='0',maker_fill_cost_dollars='0',taker_fill_cost_dollars='4.6',maker_fees_dollars='0',taker_fees_dollars='.0258',outcome_side='yes',book_side='bid',exchange_index=0,subaccount_number=0),**kwargs)
def fill(**kwargs):return dict(dict(fill_id='f1',order_id='o1',ticker=TICK,count_fp='5',yes_price_dollars='.92',fee_cost='.0258'),**kwargs)
class Reconciliation(unittest.TestCase):
    def check(self,o=None,fs=None,p=None):return validated_terminal(o or order(),p or pick(),'c1','o1',[fill()] if fs is None else fs)
    def test_complete(self):self.assertEqual(self.check()['cost'],'4.6258')
    def test_partial_is_owned(self):
        r=self.check(order(status='canceled',fill_count_fp='2',taker_fill_cost_dollars='1.84',taker_fees_dollars='.0104'),[fill(count_fp='2',fee_cost='.0104')]);self.assertEqual((r['status'],r['quantity']),('filled','2'))
    def test_confirmed_zero(self):
        r=self.check(order(status='canceled',fill_count_fp='0',taker_fill_cost_dollars='0',taker_fees_dollars='0'),[]);self.assertEqual(r['status'],'zero')
    def test_absence_is_not_confirmation(self):
        with self.assertRaises(ValueError):self.check(fs=[])
    def test_order_mismatch(self):
        for k,v in [('order_id','wrong'),('client_order_id','wrong'),('ticker','KXBTC15M-X'),('outcome_side','no'),('book_side','ask'),('exchange_index',2),('subaccount_number',1),('initial_count_fp','10'),('remaining_count_fp','1'),('status','resting')]:
            with self.assertRaises(ValueError):self.check(order(**{k:v}))
    def test_duplicate_dedup(self):self.assertEqual(self.check(fs=[fill(),fill()])['quantity'],'5')
    def test_conflicting_duplicate(self):
        with self.assertRaises(ValueError):self.check(fs=[fill(),fill(count_fp='6')])
    def test_bad_cost_or_missing_receipt(self):
        for fs in [[fill(count_fp='4')],[fill(fee_cost='0')],[fill(yes_price_dollars='.99')],[fill(order_id='else')]]:
            with self.assertRaises(ValueError):self.check(fs=fs)
    def test_no_price_coordinate(self):
        p=pick();p.update(side='no',yes_limit='.07');r=self.check(order(outcome_side='no',book_side='ask'),[fill(yes_price_dollars='.08')],p);self.assertEqual(r['cost'],'4.6258')
    def test_newer_adverse_score_blocks_old_leader(self):
        old=play();new=play(home=30,wall=T-1);new.update(seq=2,id='2',received=T-.5)
        self.assertEqual(leader(state_at([old,new],T,CFG)),'away');self.assertEqual(current_leader([old,new],T),'home')
class Submission(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();s=Store(Path(self.tmp.name)/'test.db',CFG);s.ingest(EV,receipt(play(),T-2));p=pick();p['decision_received']=T
        self.s=s;self.ident,_=s.reserve(p,'live',T)
        e=Executor.__new__(Executor);e.store=s;e.db=s.db;e.cfg=CFG;e.root=Path(self.tmp.name);e.processing=set();e.armed=lambda:True
        e.account=dict(start=T,cash='100',positions=[],resting=[])
        e.o=types.SimpleNamespace(ntp=True,clock_checked=T,last_report=dict(heartbeat=T,operational_checks_pass=True,games=[dict(event=EV,operational_checks_pass=True)]),stream=types.SimpleNamespace(book=lambda t:book()))
        e.get=AsyncMock(return_value=dict(market=dict(status='active',exchange_index=0)));e.write=AsyncMock(side_effect=TimeoutError('lost acknowledgement'));self.e=e
    async def asyncTearDown(self):self.s.db.close();self.tmp.cleanup()
    async def test_timeout_keeps_reservation_and_never_resubmits(self):
        with patch('time.time',return_value=T):
            await self.e.submit(self.ident,dict(event=EV));r=self.s.db.execute('SELECT * FROM orders').fetchone();self.assertEqual(r['status'],'unknown');self.assertGreater(dec(r['reserve']),4)
            await self.e.submit(self.ident,dict(event=EV));self.e.write.assert_awaited_once()
    async def test_disarmed_has_no_network_mutation(self):
        self.e.armed=lambda:False
        with patch('time.time',return_value=T):await self.e.submit(self.ident,dict(event=EV))
        self.e.write.assert_not_awaited();self.assertEqual(self.s.db.execute('SELECT status FROM orders').fetchone()[0],'zero')
    async def test_unverified_game_blocks(self):
        self.e.o.last_report['games']=[]
        with patch('time.time',return_value=T):await self.e.submit(self.ident,dict(event=EV))
        self.e.write.assert_not_awaited()
    async def test_account_cash_buffer_blocks(self):
        self.e.account['cash']='30'
        with patch('time.time',return_value=T):await self.e.submit(self.ident,dict(event=EV))
        self.e.write.assert_not_awaited()
    async def test_late_decision_blocks(self):
        with patch('time.time',return_value=T+4):await self.e.submit(self.ident,dict(event=EV))
        self.e.write.assert_not_awaited()
if __name__=='__main__':unittest.main()
