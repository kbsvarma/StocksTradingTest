import json,unittest
from decimal import Decimal as D
from unittest.mock import patch
import engine,runner
from lanes import *
import test_multiasset as fixtures
from test_engine import signal

class WTI(unittest.TestCase):
    tearDown=fixtures.RouteRisk.tearDown
    sig=fixtures.RouteRisk.sig
    def setUp(self):
        fixtures.RouteRisk.setUp(self);(self.root/WTI_ARM).write_text('5')
    def test_frozen_wti_profile(self):
        a,b=(lane_for(dict(asset='WTI',lane=l)) for l in (Z2,Z3))
        self.assertEqual((a.z,b.z,a.floor,b.floor,a.min_minutes,b.min_minutes,a.max_minutes),(1.5,2.5,D('.85'),D('.95'),3,3,10))
        self.assertEqual((a.cap,b.cap),(D('.939'),D('.975')))
    def test_existing_arm_stays_valid_without_wti(self):
        (self.root/WTI_ARM).unlink()
        self.assertEqual(asset_permission(self.root,'COPPER'),'');self.assertEqual(asset_permission(self.root,'EURUSD'),'')
        self.assertIn('not activated',self.e.attempt(self.sig('WTI')));self.assertFalse(self.ex.posted)
    def test_own_arm_and_halt(self):
        (self.root/'HALT_WTI').touch();self.assertIn('halted',self.e.attempt(self.sig('WTI')))
        self.assertEqual(asset_permission(self.root,'COPPER'),'')
    def test_standard_low_z_routes_to_commodity_balance(self):
        s=self.sig('WTI');s['z']=1.5
        self.assertEqual(self.e.attempt(s),'ack');self.assertEqual(self.ex.posted[0]['exchange_index'],2)
        self.assertEqual(self.ex.posted[0]['requested_count'],'5')
    def test_premium_floor_95_is_enforced(self):
        s=self.sig('WTI',Z3);s.update(signal_ask=.94,signal_bid=.93,z=2.5)
        with self.assertRaises(ValueError):self.e.attempt(s)
        s.update(signal_ask=.95,signal_bid=.94)
        self.ex.book=lambda _:dict(no_dollars=[['.05','5']])
        self.assertEqual(self.e.attempt(s),'ack')
    def test_time_and_z_boundaries(self):
        for remaining,z in [(2,5),(11,5),(5,1.4999)]:
            s=self.sig('WTI');s.update(remaining_minutes=remaining,z=z)
            with self.assertRaises(ValueError):self.e.attempt(s)
    def test_wti_arm_removed_after_book_stops_submission(self):
        book=self.ex.book
        def remove(t):
            (self.root/WTI_ARM).unlink();return book(t)
        self.ex.book=remove;self.assertIn('not activated',self.e.attempt(self.sig('WTI')));self.assertFalse(self.ex.posted)
    def test_cross_route_three_positions_fit_fifteen_fourth_blocks(self):
        self.assertEqual(engine.DAILY_LOSS_BUDGET,D(15))
        for a in ('BTC','EURUSD','WTI'):
            s=self.sig(a);self.assertEqual(self.e.attempt(s),'ack')
            r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
        self.assertEqual(self.e.attempt(self.sig('COPPER')),'Daily loss budget including open risk')
        self.assertEqual(len(self.ex.posted),3)
    def test_three_premium_positions_reserve_full_fifteen(self):
        self.ex.book=lambda _:dict(no_dollars=[['.04','5']])
        for a in ('BTC','EURUSD','WTI'):
            s=self.sig(a,Z3);s.update(z=4,signal_ask=.96,signal_bid=.95)
            self.assertEqual(self.e.attempt(s),'ack');r=self.store.get(s['ticker']);r.update(status='open',filled='5');self.store.save(r)
        self.assertEqual(self.e.attempt(dict(self.sig('COPPER',Z3),z=4,signal_ask=.96,signal_bid=.95)),'Exposure cap')
    def test_cash_deposit_does_not_reset_realized_loss(self):
        self.ex.cash_by_route={0:D(1000),2:D(1000)}
        self.store.save(dict(ticker='old',status='settled',pnl='-10.26',settled_day=engine.today()))
        self.assertEqual(self.e.attempt(self.sig('WTI')),'Daily loss budget including open risk')
    def test_status_reports_wti_and_new_risk_limit(self):
        with patch.object(runner,'STATE',self.root):d=runner.status(self.e,{},'LIVE')
        self.assertTrue(d['assets']['WTI']['enabled']);self.assertEqual(d['daily_loss_budget'],'15');self.assertEqual(d['multiasset_version'],2)
        self.assertEqual(d['max_risk'],'19.00');self.assertEqual(d['max_contracts'],20)

if __name__=='__main__':unittest.main()
