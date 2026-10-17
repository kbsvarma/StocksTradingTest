"""USD/CAD scope, routing, entry rules, execution and dashboard regressions."""
import dataclasses
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal as D

import lanes
import market_data
import runner
import dashboard_panel
from engine import Engine, Store
from test_engine import signal
from test_multiasset import RoutingExchange
import test_multiasset


class UsdCad(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'orders.db');self.ex=RoutingExchange()
        self.e=Engine(self.ex,self.store,self.root,live=True,clock=lambda:1010)
        (self.root/lanes.Z3_ARM).write_text('5')
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def arm(self):(self.root/lanes.USDCAD_ARM).write_text('5')
    def sig(self,lane=lanes.Z2):
        s=signal(ticker='KXUSDCAD15M-TEST');s.update(asset='USDCAD',exchange_index=0,lane=lane)
        if lane==lanes.Z3:s.update(signal_bid=.95,signal_ask=.96,z=3.1)
        return s
    def test_scope_and_old_rules_exactly_preserved(self):
        spec=importlib.util.spec_from_file_location('usdcad_prior',Path(__file__).resolve().parents[1]/'original/lanes.py')
        old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
        self.assertEqual(lanes.ACTIVE_ASSETS,old.ACTIVE_ASSETS+('USDCAD',))
        self.assertNotIn('AUDUSD',market_data.ASSETS)
        self.assertEqual(lanes.BASE_NONCRYPTO_ASSETS,old.BASE_NONCRYPTO_ASSETS)
        for a in old.ALL_ASSETS:
            for name in old.LANES:
                r=dict(asset=a,lane=name)
                self.assertEqual(dataclasses.asdict(lanes.lane_for(r)),dataclasses.asdict(old.lane_for(r)))
                self.assertEqual(lanes.route_for(r),old.route_for(r))
    def test_frozen_adaptive_rules_and_caps(self):
        for name,base in ((lanes.Z2,1.75),(lanes.Z3,2.75)):
            r=lanes.lane_for(self.sig(name))
            self.assertEqual((r.contracts,r.min_minutes,r.max_minutes),(5,1,10))
            self.assertEqual(r.cap,D('.939') if name==lanes.Z2 else D('.975'))
            for minute in range(1,11):self.assertAlmostEqual(r.threshold(minute),max(.5,base+.5*math.log2(minute/5)))
    def test_old_arms_do_not_enable_cad(self):
        (self.root/lanes.NONCRYPTO_ARM).write_text(json.dumps(dict(version=1,contracts=5,assets=list(lanes.BASE_NONCRYPTO_ASSETS))))
        (self.root/lanes.WTI_ARM).write_text('5')
        self.assertIn('not activated',self.e.attempt(self.sig()))
        self.assertEqual(self.ex.posted,[])
    def test_bad_arm_or_halt_blocks(self):
        for value in ('','1','10','5.0'):
            (self.root/lanes.USDCAD_ARM).write_text(value)
            self.assertIn('not activated',self.e.attempt(self.sig()))
        self.arm();(self.root/'HALT_USDCAD').touch()
        self.assertIn('halted',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
    def test_default_route_and_five_contract_order(self):
        self.arm();self.ex.cash_by_route[2]=D('0')
        self.assertEqual(self.e.attempt(self.sig()),'ack')
        self.assertEqual(self.ex.read_routes,[0])
        self.assertEqual(self.ex.posted[0]['exchange_index'],0)
        self.assertEqual(self.ex.posted[0]['requested_count'],'5')
    def test_zero_fx_cash_cannot_use_crypto_cash(self):
        self.arm();self.ex.cash_by_route[0]=D('0')
        self.assertIn('funding required',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
    def test_wrong_route_blocks_before_submission(self):
        self.arm();s=self.sig();s['exchange_index']=2
        self.assertIn('exchange changed',self.e.attempt(s));self.assertFalse(self.ex.posted)
    def test_one_market_owned_across_both_lanes(self):
        self.arm();self.assertEqual(self.e.attempt(self.sig()),'ack')
        self.assertEqual(self.e.attempt(self.sig(lanes.Z3)),'Already attempted this market')
        self.assertEqual(len(self.ex.posted),1)
    def test_shared_contract_and_daily_risk_caps(self):
        self.arm()
        self.ex.pos=[dict(ticker='KXBTC15M-OTHER',position_fp='21',exchange_index=2)]
        self.assertEqual(self.e.attempt(self.sig()),'25-contract cap')
        self.ex.pos=[dict(ticker='KXBTC15M-OTHER',position_fp='16',exchange_index=2)]
        self.assertEqual(self.e.attempt(self.sig()),'Daily loss budget including open risk')
        self.assertFalse(self.ex.posted)
    def test_late_arm_removal_blocks(self):
        self.arm();old=self.ex.book
        def book(t):
            (self.root/lanes.USDCAD_ARM).unlink();return old(t)
        self.ex.book=book
        self.assertIn('not activated',self.e.attempt(self.sig()));self.assertFalse(self.ex.posted)
    def test_entire_cent_tick_caps_both_sides_and_lanes(self):
        from engine import limit_price
        for name,cap in ((lanes.Z2,D('.93')),(lanes.Z3,D('.97'))):
            for side in ('yes','no'):
                s=self.sig(name);s.update(side=side,signal_ask=.94 if name==lanes.Z2 else .97,
                    price_ranges=[dict(start='0',end='1',step='.01')])
                px,cost=limit_price(s);self.assertLessEqual(cost,cap)
    def test_threshold_enforced_by_engine(self):
        self.arm();s=self.sig();s.update(remaining_minutes=10,z=2.1)
        with self.assertRaises(ValueError):self.e.attempt(s)
        self.assertFalse(self.ex.posted)
        s.update(remaining_minutes=1,z=1)
        self.assertEqual(self.e.attempt(s),'ack')
    def test_ui_and_effective_status(self):
        self.assertEqual(dashboard_panel.GROUPS['USD/CAD'],('USDCAD',))
        self.assertIn('Canadian',dashboard_panel.ASSET_NAMES['USDCAD'])
        with patch.object(runner,'STATE',self.root):
            s=runner.asset_status(self.e,[],'LIVE')['USDCAD']
            self.assertFalse(s['enabled']);self.assertEqual(s['exchange_index'],0)
            self.arm();s=runner.asset_status(self.e,[],'LIVE')['USDCAD'];self.assertTrue(s['enabled'])
        self.assertEqual(s['rules'][lanes.Z3]['z_slope'],.5)
        self.assertEqual((runner.CONTRACTS,runner.MAX_CONTRACTS,runner.MAX_RISK,runner.DAILY_LOSS_BUDGET),(5,25,25,20))


class CadSource(test_multiasset.PythFinality):
    """Run every source-finality regression against CAD as well as original fixtures."""
    def setUp(self):
        super().setUp();self.asset='USDCAD'
        self.market.update(ticker='KXUSDCAD15M-TEST',exchange_index=0)
    def test_scanner_computes_profile_from_source(self):
        quote=dict(end_period_ts=self.T,yes_bid=dict(close_dollars='.91'),yes_ask=dict(close_dollars='.92'))
        responses=[dict(markets=[self.market]),dict(candlesticks=[quote]),self.raw(),dict(orderbook_fp=dict(no_dollars=[['.08','5']]))]
        with patch.object(market_data,'PYTH',self.p),patch.object(market_data,'get',side_effect=responses),patch('time.time',return_value=self.T+2):
            row=market_data.scan(self.asset,self.T,set())
        self.assertEqual(row['status'],'paper_fill');self.assertEqual(row['required_z'],1.75)
        self.assertEqual(row['exchange_index'],0);self.assertEqual(row['underlying_evidence']['bars_count'],61)


if __name__=='__main__':unittest.main()
