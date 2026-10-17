from decimal import Decimal as D
from unittest.mock import patch
import unittest
import test_engine
from test_engine import signal
import market_data
import test_signals

class ScalingTests(unittest.TestCase):
    setUp = test_engine.Tests.setUp
    tearDown = test_engine.Tests.tearDown
    acknowledged = test_engine.Tests.acknowledged
    def test_status_displays_scaled_limits(self):
        import runner
        row=runner.status(self.engine,{},'LIVE')
        self.assertEqual((row['contracts'],row['max_contracts'],row['max_risk'],row['daily_loss_budget']),(2,8,'7.60','10'))
    def test_two_contract_win_and_loss(self):
        for side,expected in [('yes',D('.154')),('no',D('-1.846'))]:
            with self.subTest(side=side):
                self.store.db.execute('DELETE FROM orders');self.store.db.commit()
                self.now=1010;r=self.acknowledged(side=side,fill='2');self.now=1400
                self.assertEqual(self.engine.reconcile(),[])
                self.assertEqual(D(self.store.get(r['ticker'])['pnl']),expected)
    def test_two_level_book_and_partial_fill_no_topup(self):
        self.ex.book=lambda t:dict(no_dollars=[['.08','1'],['.075','1']])
        r=self.acknowledged(fill='1.5',status='canceled')
        self.assertEqual(r['requested_count'],'2')
        self.assertEqual(self.engine.reconcile(),[])
        self.assertEqual(D(self.store.get(r['ticker'])['filled']),D('1.5'))
        self.engine.attempt(signal());self.assertEqual(len(self.ex.posted),1)
    def test_one_contract_depth_is_not_enough(self):
        self.ex.book=lambda t:dict(no_dollars=[['.08','1']])
        self.assertEqual(self.engine.attempt(signal()),'Insufficient depth inside cap')
        self.assertFalse(self.ex.posted)
    def test_second_contract_outside_limit(self):
        self.ex.book=lambda t:dict(no_dollars=[['.08','1'],['.05','1']])
        self.assertEqual(self.engine.attempt(signal()),'Insufficient depth inside cap')
    def test_two_dollar_cash_floor(self):
        self.ex.cash=D('1.99');self.assertIn('funding required',self.engine.attempt(signal()))
        self.assertFalse(self.ex.posted)
    def test_four_two_contract_markets_fit_cap(self):
        for a in ['BTC','ETH','SOL','XRP']:
            s=signal(ticker='KX'+a+'15M-TEST');s['asset']=a
            self.assertEqual(self.engine.attempt(s),'ack')
            r=self.store.get(s['ticker']);r.update(status='open',filled='2');self.store.save(r)
        self.assertEqual(len(self.ex.posted),4)
    def test_seven_existing_contracts_block_two_more(self):
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='7')]
        self.assertEqual(self.engine.attempt(signal()),'Eight-contract cap')
    def test_legacy_one_contract_record_reconciles(self):
        r=self.acknowledged(fill='1');r.pop('requested_count');self.store.save(r)
        self.ex.remote[0]['initial_count_fp']='1'
        self.now=1400;self.assertEqual(self.engine.reconcile(),[])
        self.assertEqual(D(self.store.get(r['ticker'])['pnl']),D('.074'))
    def test_legacy_record_cannot_accept_two_fills(self):
        r=self.acknowledged(fill='2');r.pop('requested_count');self.store.save(r)
        self.assertTrue(self.engine.reconcile())
    def test_daily_budget_includes_all_open_risk(self):
        self.store.save(dict(ticker='old',status='settled',pnl='-2.5',settled_day=__import__('engine').today()))
        self.ex.pos=[dict(ticker='KXETH15M-OTHER',position_fp='6')]
        self.assertEqual(self.engine.attempt(signal()),'Daily loss budget including open risk')
    def test_scanner_returns_per_contract_price_for_two(self):
        T=12000;responses=test_signals.PaperProbeTests().responses(T)
        with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=T+1):
            row=market_data.scan('BTC',T,set())
        self.assertEqual(row['status'],'paper_fill');self.assertEqual(row['count'],2)
        self.assertAlmostEqual(row['entry'],.9)
    def test_scanner_rejects_single_contract_depth(self):
        T=12000;responses=test_signals.PaperProbeTests().responses(T)
        responses[-1]['orderbook_fp']['no_dollars']=[['.10','1']]
        with patch.object(market_data,'get',side_effect=responses),patch.object(market_data.time,'time',return_value=T+1):
            row=market_data.scan('BTC',T,set())
        self.assertEqual(row['status'],'insufficient_depth')

if __name__=='__main__':unittest.main()
