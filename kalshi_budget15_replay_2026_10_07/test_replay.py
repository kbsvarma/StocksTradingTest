"""Invariant tests for risk replay, including partial fills and concurrent signals."""
import unittest
from datetime import datetime
from replay import replay, NAMES, D, ET, metrics

T=datetime(2026,10,6,22,0,tzinfo=ET).timestamp()

def candidate(asset,lane=0,t=T,settled=T+600,won=1,quantity=5,suffix='ONE'):
    return dict(ticker=f'KX{asset}15M-{suffix}',asset=asset,lane=NAMES[lane],
        entered=t,settled=settled,quantity=quantity,entry='.94' if lane else '.90',fee='0',won=won)

class RiskReplayTests(unittest.TestCase):
    def test_competing_signals_share_one_budget(self):
        seed=[dict(ticker='OLD',status='settled',pnl='-.5738',settled_day='2026-10-06')]
        rows=[candidate('BTC',t=T-1)]+[candidate(a,1) for a in ('ETH','SOL','XRP')]
        ten,*_=replay(rows,10,stress=0,seed=seed)
        fifteen,*_=replay(rows,15,stress=0,seed=seed)
        self.assertEqual(len(ten),1)
        self.assertEqual(len(fifteen),2)
    def test_partial_fill_still_reserves_requested_five(self):
        rows=[candidate('BTC',quantity=1)]+[candidate(a,1) for a in ('ETH','SOL')]
        rr,_,_,capital=replay(rows,10,stress=0)
        self.assertEqual(len(rr),2)
        self.assertEqual(capital['max_open_risk'],9.75)
        self.assertEqual(capital['max_open_contracts'],10)
    def test_one_owner_for_overlapping_lanes(self):
        rows=[candidate('BTC',1),candidate('BTC',0)]
        rr,*_=replay(rows,15,stress=0)
        self.assertEqual(len(rr),1)
        self.assertEqual(rr[0]['lane'],NAMES[0])
    def test_existing_asset_exposure_blocks_other_ticker(self):
        rr,blocked,*_=replay([candidate('BTC'),candidate('BTC',t=T+1,suffix='TWO')],15,stress=0)
        self.assertEqual(len(rr),1)
        self.assertEqual(blocked['Asset already has exposure'],1)
    def test_daily_budget_resets_at_et_midnight(self):
        midnight=datetime(2026,10,7,0,0,tzinfo=ET).timestamp()
        seed=[dict(ticker='OLD',status='settled',pnl='-8',settled_day='2026-10-06')]
        rows=[candidate('BTC',t=midnight-1,settled=midnight+600),candidate('BTC',t=midnight,settled=midnight+600)]
        rr,*_=replay(rows,10,stress=0,seed=seed)
        self.assertEqual(len(rr),1)
        self.assertEqual(rr[0]['entered'],midnight)
    def test_loss_and_open_risk_both_count(self):
        rows=[candidate('BTC',t=T,settled=T+1,won=0),candidate('ETH',t=T+2),candidate('SOL',t=T+3)]
        rr,*_=replay(rows,10,stress=0)
        self.assertEqual([r['asset'] for r in rr],['BTC','ETH'])
        self.assertEqual(metrics(rr)['net'],-4)
    def test_cash_floor_prevents_new_positions(self):
        rr,blocked,*_=replay([candidate('BTC')],15,stress=0,initial_cash='4.99')
        self.assertFalse(rr)
        self.assertEqual(blocked['Insufficient free cash'],1)
    def test_settlement_delay_keeps_risk_reserved(self):
        rows=[candidate('BTC',settled=T+1),candidate('ETH',t=T+2,settled=T+100),candidate('SOL',t=T+3,settled=T+100)]
        fast,*_=replay(rows,10,stress=0)
        slow,*_=replay(rows,10,stress=0,settlement_delay=60)
        self.assertEqual(len(fast),3)
        self.assertEqual(len(slow),2)

if __name__=='__main__':unittest.main()
