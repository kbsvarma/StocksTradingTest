import copy,datetime as dt,importlib.util,json,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from paper import *

def signal_fixture(side='yes',asset='BTC',lane='cushion_z2'):
    return dict(asset=asset,ticker='KX'+asset+'15M-TEST',lane=lane,side=side,exchange_index=0 if asset in ('EURUSD','USDCAD') else 2,decision_ts=1000,observed_ts=1001,signal_qualified_ts=1001,close_ts=1300,remaining_minutes=5,signal_bid=.92,signal_ask=.93,z=3.1,signal_qualified=True,price_ranges=[dict(start='0',end='.1',step='.001'),dict(start='.1',end='.9',step='.01'),dict(start='.9',end='1',step='.001')])
def order_fixture(side='yes'):
    return dict(ticker='KXBTC15M-TEST',side=side,accepted_ts=1000,effective_expiry=1010,price='.90',queue_remaining='10',filled='0')
def trade_fixture(side='no',px='.90',qty='3',t=1001):
    return dict(ticker='KXBTC15M-TEST',trade_id='t1',created_time=dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat(),taker_side=side,yes_price_dollars=px,no_price_dollars=str(1-D(px)),count_fp=qty,is_block_trade=False)
def payload(yes=None,no=None):
    return dict(orderbook_fp=dict(yes_dollars=yes or [['.89','12']],no_dollars=no or [['.09','5']]))

class Rules(unittest.TestCase):
    def test_all_current_lane_signals(self):
        for a in ACTIVE_ASSETS:
            for l in ('cushion_z2','premium_z3'):
                s=signal_fixture(asset=a,lane=l);s.update(signal_ask=.96 if l=='premium_z3' else .93,signal_bid=.95 if l=='premium_z3' else .92,z=4)
                with self.subTest(a=a,l=l):self.assertEqual(validate_signal(s),'')
    def test_btc_original_stays_three(self):
        s=signal_fixture();s['z']=2.99;self.assertEqual(validate_signal(s),'z_gate')
    def test_fx_adaptive(self):
        s=signal_fixture(asset='USDCAD');s.update(remaining_minutes=10,close_ts=1600,z=2.2);self.assertEqual(validate_signal(s),'z_gate')
    def test_nan_missing_invalid_rejected(self):
        for k,val in [('z',float('nan')),('signal_ask',1),('side','bad'),('exchange_index',0),('remaining_minutes',0)]:
            s=signal_fixture();s[k]=val;self.assertNotEqual(validate_signal(s),'')
    def test_both_side_three_cent_targets(self):
        for side in ('yes','no'):self.assertEqual(resting_price(signal_fixture(side),'.03'),D('.90'))
    def test_tick_rounds_toward_lower_spend(self):
        for side in ('yes','no'):
            s=signal_fixture(side);s['signal_ask']=.915;self.assertEqual(resting_price(s,'.03'),D('.88'))
    def test_floor_cannot_be_lowered(self):
        s=signal_fixture();s['signal_ask']=.86
        with self.assertRaises(ValueError):resting_price(s,'.03')
    def test_cap_never_raised(self):
        s=signal_fixture();s['signal_ask']=.949
        self.assertLessEqual(resting_price(s,'.001'),D('.939'))
    def test_full_depth_required_for_control(self):
        s=signal_fixture();self.assertIsNotNone(ioc_depth(payload(),s));self.assertIsNone(ioc_depth(payload(no=[['.09','4.99']]),s))
    def test_no_side_complement(self):
        s=signal_fixture('no');b=payload(yes=[['.09','5']],no=[['.89','12']]);self.assertEqual(ioc_depth(b,s),[['0.91','5']])
    def test_control_rejects_outside_cap_and_floor(self):
        self.assertIsNone(ioc_depth(payload(no=[['.06','10']]),signal_fixture()))
        self.assertIsNone(ioc_depth(payload(yes=[['.8','2']],no=[['.2','10']]),signal_fixture()))
    def test_crossed_book_invalid(self):
        with self.assertRaises(ValueError):book(payload(yes=[['.95','1']]),'yes')

class Queue(unittest.TestCase):
    def test_truncated_queue_is_unknown_not_zero(self):
        levels=[(D('.90')+D(i)/10000,D(1)) for i in range(100)]
        with self.assertRaises(ValueError):queue_ahead(levels,D('.89'))
        self.assertEqual(queue_ahead(levels,D('.90')),1)
        self.assertEqual(queue_ahead(levels,D('.92')),0)
    def test_at_price_waits_for_visible_queue(self):
        o=order_fixture();q,_=apply_trade(o,trade_fixture(qty='8'));self.assertEqual(q,0);self.assertEqual(D(o['queue_remaining']),2)
        q,_=apply_trade(o,trade_fixture(qty='3'));self.assertEqual(q,1)
    def test_trade_through_credits_only_print_quantity(self):
        o=order_fixture();q,reason=apply_trade(o,trade_fixture(px='.89',qty='2'));self.assertEqual(q,2);self.assertEqual(reason,'through_bid')
    def test_caps_total_at_five(self):
        o=order_fixture();apply_trade(o,trade_fixture(px='.89',qty='4'));q,_=apply_trade(o,trade_fixture(px='.88',qty='20'));self.assertEqual(q,1);self.assertEqual(D(o['filled']),5)
    def test_partial_fractional_quantity_retained(self):
        o=order_fixture();q,_=apply_trade(o,trade_fixture(px='.89',qty='.27'));self.assertEqual(q,D('.27'))
    def test_wrong_aggressor_no_fill(self):
        self.assertEqual(apply_trade(order_fixture(),trade_fixture(side='yes',px='.89'))[0],0)
    def test_no_side_direction_and_price(self):
        o=order_fixture('no');q,_=apply_trade(o,trade_fixture(side='yes',px='.11',qty='4'));self.assertEqual(q,4)
    def test_no_pre_submit_or_expired_fills(self):
        for t in (999.9,1010,1011):self.assertEqual(apply_trade(order_fixture(),trade_fixture(px='.89',t=t))[0],0)
    def test_block_and_missing_direction_rejected(self):
        for patch in ({'is_block_trade':True},{'taker_side':None},{'no_price_dollars':'.2'},{'count_fp':'NaN'}):
            tr=trade_fixture(px='.89');tr.update(patch);self.assertEqual(apply_trade(order_fixture(),tr)[0],0)
    def test_quote_touch_is_not_fill(self):
        o=order_fixture();book(payload(no=[['.1','100']]),'yes');self.assertEqual(o['filled'],'0')
    def test_queue_cancellation_not_credited(self):
        o=order_fixture();book(payload(yes=[['.89','1']]),'yes');self.assertEqual(o['queue_remaining'],'10')
    def test_cancel_race_lifetime(self):
        o=order_fixture();o['effective_expiry']=1002.25
        self.assertGreater(apply_trade(o,trade_fixture(px='.89',t=1002.2))[0],0)
        self.assertEqual(apply_trade(o,trade_fixture(px='.89',t=1002.3))[0],0)

class Money(unittest.TestCase):
    def test_fees_and_rounding(self):
        self.assertEqual(fee([['.9','5']],1),D('.0315'))
        self.assertEqual(fee([['.9','5']],1,True,'quadratic'),0)
        self.assertEqual(fee([['.9','5']],1,True,'quadratic_with_maker_fees'),D('.0079'))
    def test_fractional_fee_cash_rounding(self):
        levels=[['.901','.27']];cost=D('.901')*D('.27');self.assertEqual((cost+fee(levels,1))%D('.0001'),0)
    def test_pending_resting_reserves_all_five(self):
        existing=[dict(asset=a,state='resting',filled='0',reserve='1',route=2,settled=False) for a in ('ETH','SOL','XRP','NATGAS')]
        self.assertEqual(risk_reason(existing,signal_fixture(),1000,{'2':'100','0':'100'}),'daily_loss_including_open_risk')
    def test_daily_losses_block_more_exposure(self):
        e=[dict(asset='WTI',state='filled',filled='0',settled=True,settled_ts=1000,pnl='-16',route=2)]
        self.assertEqual(risk_reason(e,signal_fixture(),1001,{'2':'100','0':'100'}),'daily_loss_including_open_risk')
    def test_waiting_ioc_also_reserved(self):
        e=[dict(asset='BTC',state='waiting_control',filled='0',reserve='.95',route=2,settled=False)]
        self.assertEqual(risk_reason(e,signal_fixture(),1001,{'2':'100','0':'100'}),'asset_exposure')
    def test_cash_routes_are_separate(self):
        self.assertEqual(risk_reason([],signal_fixture(),1001,{'2':'2','0':'100'}),'route_cash')

class Isolation(unittest.TestCase):
    def test_http_only_allowlisted_public_paths(self):
        from observer import ALLOWED
        for p in ('/portfolio/orders','/markets/ABC/orderbook','/markets/KXBTC15M-X/../orders','https://elsewhere'):
            self.assertIsNone(ALLOWED.fullmatch(p))
        self.assertIsNotNone(ALLOWED.fullmatch('/markets/KXBTC15M-TEST/orderbook'))
    def test_store_receipt_hash_and_immutable_fills(self):
        from observer import Store
        with tempfile.TemporaryDirectory() as d:
            st=Store(Path(d)/'paper.sqlite');st.receipt(dict(started=1,received=2,path='/test',status=200,body={}))
            self.assertEqual(st.db.execute('select count(*) from receipts').fetchone()[0],1);st.db.close()

if __name__=='__main__':unittest.main()
