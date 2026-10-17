import itertools
import unittest
from decimal import Decimal as D
from unittest.mock import patch
import engine
import runner
import test_multiasset as fixtures
from lanes import Z2,Z3,WTI_ARM,ALL_ASSETS,lane_for

class Capacity25(unittest.TestCase):
    setUp=fixtures.RouteRisk.setUp
    tearDown=fixtures.RouteRisk.tearDown
    sig=fixtures.RouteRisk.sig
    def profit(self,value,lane=Z2):
        self.store.save(dict(ticker='prior-'+lane,lane=lane,status='settled',pnl=str(value),settled_day=engine.today()))
    def opened(self,asset,lane=Z2):
        s=self.sig(asset,lane)
        row=self.store.get(s['ticker']) or s
        self.store.save(dict(row,status='open',requested_count='5',filled='5'))
    def test_effective_caps_status_and_entry_rules(self):
        self.assertEqual((engine.CONTRACTS,engine.MAX_CONTRACTS,engine.MAX_RISK,engine.DAILY_LOSS_BUDGET),(5,D(25),D(25),D(20)))
        self.assertEqual((engine.Z3_MAX_CONTRACTS,engine.Z3_MAX_RISK,engine.Z3_DAILY_BUDGET),(D(25),D(25),D(25)))
        with patch.object(runner,'STATE',self.root):status=runner.status(self.e,{},'LIVE')
        self.assertEqual((status['max_contracts'],status['max_risk'],status['daily_loss_budget']),(25,'25.00','20'))
    def test_four_fake_submissions_fit_twenty_fifth_waits(self):
        for asset in ('BTC','ETH','COPPER','EURUSD'):
            s=self.sig(asset)
            self.assertEqual(self.e.attempt(s),'ack')
            self.opened(asset)
        self.assertEqual(self.e.attempt(self.sig('NATGAS')),'Daily loss budget including open risk')
        self.assertEqual(len(self.ex.posted),4)
    def test_each_fifth_position_mix_requires_exact_realized_profit(self):
        assets=('BTC','ETH','COPPER','NATGAS','EURUSD')
        for lanes in itertools.product((Z2,Z3),repeat=5):
            with self.subTest(lanes=lanes):
                self.store.db.execute('delete from orders');self.store.db.commit()
                for a,lane in zip(assets[:4],lanes[:4]):self.opened(a,lane)
                required=sum((D(5)*lane_for(dict(asset=a,lane=l)).reserve for a,l in zip(assets,lanes)),D(0))-D(20)
                s=self.sig(assets[-1],lanes[-1]);self.profit(required-D('.0001'))
                self.assertEqual(self.e.entry_guard(s,{},[],D(50)),'Daily loss budget including open risk')
                self.profit(required)
                self.assertEqual(self.e.entry_guard(s,{},[],D(50)),'')
    def test_five_premium_positions_no_longer_hit_old_lane_cap(self):
        self.profit(5)
        for a in ('BTC','ETH','COPPER','NATGAS'):self.opened(a,Z3)
        self.assertEqual(self.e.entry_guard(self.sig('EURUSD',Z3),{},[],D(50)),'')
    def test_sixth_position_blocked_even_with_large_profit(self):
        self.profit(100)
        for a in ('BTC','ETH','COPPER','NATGAS','EURUSD'):self.opened(a)
        self.assertEqual(self.e.entry_guard(self.sig('SOL'),{},[],D(1000)),'25-contract cap')
    def test_premium_lane_daily_loss_still_cannot_borrow_other_profit(self):
        self.profit(10);self.profit(D('-.01'),Z3)
        for a in ('BTC','ETH','COPPER','NATGAS'):self.opened(a,Z3)
        self.assertEqual(self.e.entry_guard(self.sig('EURUSD',Z3),{},[],D(50)),'Strong cushion daily loss budget including open risk')
    def test_cash_deposit_does_not_override_twenty_daily_loss_guard(self):
        self.profit(D('-15.26'))
        self.assertEqual(self.e.entry_guard(self.sig('EURUSD'),{},[],D(1000)),'Daily loss budget including open risk')
    def test_foreign_route_positions_count_toward_shared_capacity(self):
        self.profit(5)
        self.assertEqual(self.e.entry_guard(self.sig('BTC'),{'KXEURUSD15M-OTHER':D(20)},[],D(50)),'')
        self.assertEqual(self.e.entry_guard(self.sig('BTC'),{'KXEURUSD15M-OTHER':D('20.01')},[],D(50)),'25-contract cap')
    def test_exact_twenty_dollar_all_premium_reservation(self):
        for a in ('BTC','ETH','COPPER'):self.opened(a,Z3)
        s=self.sig('EURUSD',Z3)
        self.assertEqual(self.e.entry_guard(s,{},[],D(50)),'')
        self.profit(D('-.0001'))
        self.assertEqual(self.e.entry_guard(s,{},[],D(50)),'Daily loss budget including open risk')

if __name__=='__main__':unittest.main()
