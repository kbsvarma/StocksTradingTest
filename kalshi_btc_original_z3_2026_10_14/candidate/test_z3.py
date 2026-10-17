import copy
from decimal import Decimal as D
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import engine
import market_data
import runner
from lanes import Z2,Z3,Z3_ARM,Z3_HALT,lane_for
import test_engine as fixtures
from test_engine import signal,LADDER
import test_signals as scanner_fixtures
from dashboard_panel import ledger_summary

def premium(side='yes',ticker='KXBTC15M-TEST'):
    return dict(signal(side,ticker),lane=Z3,z=3,signal_ask=.96,signal_bid=.95)

class StrongCushion(unittest.TestCase):
    setUp=fixtures.Tests.setUp
    tearDown=fixtures.Tests.tearDown
    def arm(self):
        (self.root/Z3_ARM).write_text('5')
        self.ex.book=lambda _:dict(no_dollars=[['.04','5']],yes_dollars=[['.04','5']])
    def test_unarmed_lane_cannot_submit(self):
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion not activated')
        self.assertEqual(self.ex.posted,[])
    def test_old_one_contract_arm_cannot_enable_five(self):
        (self.root/'ARM_Z3_1CT').write_text('1')
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion not activated')
        (self.root/Z3_ARM).write_text('1')
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion not activated')
        self.assertFalse(self.ex.posted)
    def test_four_displayed_contracts_cannot_trigger_five_contract_order(self):
        self.arm();self.ex.book=lambda _:dict(no_dollars=[['.04','4']])
        self.assertEqual(self.engine.attempt(premium()),'Insufficient depth inside cap')
        self.assertFalse(self.ex.posted);self.assertEqual(self.store.all(),[])
    def test_five_contracts_can_span_price_levels_within_cap(self):
        self.arm();self.ex.book=lambda _:dict(no_dollars=[['.04','2'],['.03','3']])
        self.assertEqual(self.engine.attempt(premium()),'ack')
        self.assertEqual(self.ex.posted[0]['requested_count'],'5')
        self.assertEqual(D(self.ex.posted[0]['favorite_limit']),D('.97'))
    @patch.object(engine,'DAILY_LOSS_BUDGET',D('10'))
    def test_two_premium_positions_fit_ten_dollar_budget_but_third_does_not(self):
        self.arm()
        for asset in ('BTC','ETH'):
            s=premium(ticker='KX'+asset+'15M-TEST');s['asset']=asset
            self.assertEqual(self.engine.attempt(s),'ack')
            r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
        s=premium(ticker='KXSOL15M-TEST');s['asset']='SOL'
        self.assertEqual(self.engine.attempt(s),'Daily loss budget including open risk')
        self.assertEqual(len(self.ex.posted),2)
    @patch.object(engine,'DAILY_LOSS_BUDGET',D('10'))
    def test_one_original_plus_one_premium_fits_shared_budget(self):
        s=signal();self.assertEqual(self.engine.attempt(s),'ack')
        r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
        self.arm();s=premium(ticker='KXETH15M-TEST');s['asset']='ETH'
        self.assertEqual(self.engine.attempt(s),'ack')
        r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
        s=premium(ticker='KXSOL15M-TEST');s['asset']='SOL'
        self.assertEqual(self.engine.attempt(s),'Daily loss budget including open risk')
    def test_no_side_float_boundary_matches_research(self):
        self.arm();self.ex.book=lambda _:dict(yes_dollars=[['.07','5']])
        s=premium('no');s.update(signal_ask=1-.07,signal_bid=.92)
        self.assertEqual(self.engine.attempt(s),'ack')
    def test_five_contracts_and_tick_cap_both_sides(self):
        self.arm()
        for side in ('yes','no'):
            s=premium(side,'KX'+('BTC' if side=='yes' else 'ETH')+'15M-TEST')
            s.update(asset='BTC' if side=='yes' else 'ETH',signal_ask=.979,signal_bid=.97)
            self.assertEqual(self.engine.attempt(s),'ack')
            r=self.store.get(s['ticker']);self.assertEqual(r['requested_count'],'5')
            self.assertEqual(D(r['favorite_limit']),D('.975'))
            self.assertEqual(D(r['yes_limit']),D('.975') if side=='yes' else D('.025'))
            r.update(status='open',filled='5');self.store.save(r)
    def test_boundary_filters_and_unapproved_lane(self):
        self.arm()
        for k,v in [('z',2.999),('signal_ask',.9299),('signal_ask',.98),('remaining_minutes',2),('remaining_minutes',11),('signal_bid',.90),('lane','gaussian_edge8')]:
            s=premium();s[k]=v
            with self.subTest(k=k,v=v),self.assertRaises(ValueError):self.engine.attempt(s)
        self.assertEqual(self.ex.posted,[])
    def test_live_book_must_not_weaken_or_exceed_cap(self):
        self.arm()
        for bid,reason in [('.071','Book weakened below floor'),('.02','Insufficient depth inside cap')]:
            self.ex.book=lambda _,bid=bid:dict(no_dollars=[[bid,'1']])
            self.assertEqual(self.engine.attempt(premium()),reason)
        self.assertEqual(self.ex.posted,[])
    def test_premium_cash_does_not_bypass_existing_minimum(self):
        self.arm();self.ex.cash=D('4.99')
        self.assertIn('funding required',self.engine.attempt(premium()))
    def test_premium_reserves_one_dollar_in_shared_daily_budget(self):
        self.arm()
        self.store.save(dict(ticker='prior',status='settled',settled_day=engine.today(),pnl='-19.02'))
        self.assertEqual(self.engine.attempt(premium()),'Daily loss budget including open risk')
    def test_own_daily_budget_cannot_borrow_original_profit(self):
        self.arm()
        for lane,pnl in [(Z2,'40'),(Z3,'-20.1')]:
            self.store.save(dict(ticker=lane,status='settled',lane=lane,pnl=pnl,settled_day=engine.today()))
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion daily loss budget including open risk')
    def test_mixed_reservation_not_95c_for_every_contract(self):
        self.arm()
        self.store.save(dict(ticker='KXETH15M-OPEN',lane=Z3,status='open',requested_count='1',filled='1'))
        self.store.save(dict(ticker='old',status='settled',pnl='-14.26',settled_day=engine.today()))
        self.ex.book=lambda _:dict(no_dollars=[['.08','5']])
        self.assertEqual(self.engine.attempt(signal()),'Daily loss budget including open risk')
    @patch.object(engine,'MAX_RISK',D('19'))
    def test_shared_exposure_cap_is_fee_inclusive(self):
        self.arm();self.ex.pos=[dict(ticker='KXETH15M-FOREIGN',position_fp='14.1')]
        self.assertEqual(self.engine.attempt(premium()),'Exposure cap')
    def test_shared_market_ownership_survives_restart(self):
        self.engine.attempt(signal());r=self.store.get(signal()['ticker']);r['status']='unfilled';self.store.save(r)
        self.arm()
        restarted=engine.Engine(self.ex,self.store,self.root,live=True,clock=lambda:self.now)
        self.assertEqual(restarted.attempt(premium()),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_premium_unknown_blocks_original_and_is_not_reposted(self):
        self.arm();self.ex.raise_post=True
        self.assertEqual(self.engine.attempt(premium()),'unknown')
        self.assertEqual(self.engine.attempt(premium()),'Already attempted this market')
        self.assertEqual(self.engine.attempt(signal(ticker='KXETH15M-TEST')),'Order reconciliation pending')
        self.assertEqual(len(self.ex.posted),1)
    def test_premium_arm_removed_during_book_blocks(self):
        self.arm();old=self.ex.book
        def book(t):
            (self.root/Z3_ARM).unlink();return old(t)
        self.ex.book=book
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion not activated')
        self.assertFalse(self.ex.posted)
    def test_lane_halt_leaves_original_enabled(self):
        self.arm();(self.root/Z3_HALT).touch()
        self.assertEqual(self.engine.attempt(premium()),'Strong cushion manually halted')
        self.ex.book=lambda _:dict(no_dollars=[['.08','5']])
        self.assertEqual(self.engine.attempt(signal()),'ack')
    def test_premium_settlement_actual_fees_loss_and_idempotence(self):
        self.arm();s=premium('no');self.engine.attempt(s);r=self.store.get(s['ticker'])
        self.ex.remote=[dict(order_id='oid',ticker=r['ticker'],client_order_id=r['client_id'],status='canceled',initial_count_fp='5',fill_count_fp='.4',remaining_count_fp='0')]
        self.ex.fs=[dict(order_id='oid',ticker=r['ticker'],fill_id='f',count_fp='.4',yes_price_dollars='.04',fee_cost='.0011')]
        self.now=1400;self.engine.reconcile();self.engine.reconcile()
        got=self.store.get(s['ticker']);self.assertEqual(got['status'],'settled')
        self.assertEqual(D(got['pnl']),D('-.3851'))
        self.assertEqual(got['lane'],Z3);self.assertEqual(len(self.ex.posted),1)
    def test_premium_ioc_payload_in_crypto_shard(self):
        self.arm();self.engine.attempt(premium('no'));r=self.ex.posted[0]
        class Client:
            def _request(self,method,path,body):self.body=body;self.path=path;return {}
        c=Client();engine.Exchange(c).submit(r)
        self.assertEqual(c.path,'/portfolio/events/orders')
        self.assertEqual(c.body['count'],'5.00');self.assertEqual(c.body['exchange_index'],2)
        self.assertEqual(c.body['side'],'ask');self.assertEqual(c.body['time_in_force'],'immediate_or_cancel')
    def test_ui_attribution_does_not_reclassify_old_trades(self):
        rows=[dict(status='settled',side='yes',result='yes',pnl='.1'),dict(status='settled',side='no',result='yes',pnl='-.96',lane=Z3)]
        self.assertEqual(lane_for(rows[0]).name,Z2)
        self.assertEqual(ledger_summary(rows)['losses'],1)
        self.assertEqual(ledger_summary([r for r in rows if lane_for(r).name==Z3])['net'],D('-.96'))
    def test_status_reports_separate_lanes(self):
        self.arm()
        with patch.object(runner,'STATE',self.root):d=runner.status(self.engine,{},'LIVE')
        self.assertTrue(d['lanes'][Z3]['enabled'])
        self.assertEqual(d['lanes'][Z3]['contracts'],5)
        self.assertEqual(d['lanes'][Z2]['contracts'],5)

class Scanner(unittest.TestCase):
    def test_premium_same_feature_as_research(self):
        T=12000;responses=scanner_fixtures.PaperProbeTests().responses(T)
        m=responses[0]['markets'][0];m.update(exchange_index=2,notional_value_dollars='1.0000',price_ranges=LADDER)
        c=responses[1]['candlesticks'][0];c['yes_bid']['close_dollars']='.94';c['yes_ask']['close_dollars']='.95'
        responses[3]['orderbook_fp']={'no_dollars':[['.05','5']]}
        with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=T+3):
            r=market_data.scan('BTC',T,set(),lane=Z3)
        self.assertEqual(r['status'],'paper_fill');self.assertEqual(r['count'],5);self.assertGreaterEqual(r['z'],3)
    def test_quote_cache_never_caches_execution_book(self):
        calls=[]
        def get(u):calls.append(u);return len(calls)
        cache=runner.MinuteQuotes(get);cache.begin(1000)
        self.assertEqual(cache('x/candles'),cache('x/candles'))
        self.assertNotEqual(cache('x/orderbook'),cache('x/orderbook'))
        cache.begin(1060);self.assertEqual(cache('x/candles'),4)

if __name__=='__main__':unittest.main()
