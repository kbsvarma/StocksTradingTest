import unittest
from dataclasses import replace
from research import Rule,fee,quote,side_quote,features,trade,qualifies,rules,fit_models,ts

def candle(t,b=.89,a=.90):
    return dict(t=t,bo=b,ao=a,bc=b,ac=a,bl=b,bh=b,al=a,ah=a)

class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.m=dict(asset='BTC',ticker='test',close=1800,start=900,result=1,
                    br={r:candle(1800-r*60) for r in range(15)})
        self.prices={s:{'BTC':{840:100}} for s in ('coinbase','benchmark')}
        self.means={'BTC':{840:100}}
        self.stats={s:{'BTC':{t:(101,.001) for t in range(900,1800,60)}}
                    for s in ('coinbase','benchmark')}
        self.fs=features(self.m,self.prices,self.means,self.stats)
        self.r=Rule('test','test','plain',.85,.93,3,10)

    def test_no_bid_ask_inversion(self):
        self.assertEqual(side_quote((.1,.12),-1),(.88,.9))
        self.assertIsNone(quote({'bc':.5,'ac':.4}))

    def test_fee_is_upward_to_centicent(self):
        self.assertEqual(fee(.9),.0063)
        self.assertGreaterEqual(fee(.923),.07*.923*(1-.923))

    def test_entry_uses_next_open_not_signal_close(self):
        self.m['br'][9]['ao']=.907
        row=trade(self.m,self.r,self.fs)
        self.assertEqual(row['entry'],.907)
        self.assertEqual(row['ts'],1200)

    def test_capped_order_skips_runaway_price(self):
        for c in self.m['br'].values():c['ao']=.99
        self.assertIsNone(trade(self.m,self.r,self.fs))

    def test_missing_next_candle_cannot_fill(self):
        self.m['br']={}
        self.assertIsNone(trade(self.m,self.r,self.fs))

    def test_longshot_buys_complement(self):
        fs={10:dict(self.fs[10],flips=1)}
        r=replace(self.r,kind='longshot',lo=.10,hi=.12)
        row=trade(self.m,r,fs)
        self.assertEqual(row['side'],-1)
        self.assertAlmostEqual(row['entry'],.11)
        self.assertAlmostEqual(row['pnl'],-.11-fee(.11))

    def test_future_spot_cannot_change_current_z(self):
        old=self.fs[10]['z']['coinbase']
        self.stats['coinbase']['BTC'][1200]=(10000,.00001)
        new=features(self.m,self.prices,self.means,self.stats)
        self.assertEqual(old,new[10]['z']['coinbase'])

    def test_missing_completed_bar_refuses_cushion(self):
        del self.stats['benchmark']['BTC'][1140]
        fs=features(self.m,self.prices,self.means,self.stats)
        r=replace(self.r,kind='cushion',source='benchmark',zmin=1.5)
        self.assertFalse(qualifies(r,fs[10],10))

    def test_unbroken_requires_contiguous_full_history(self):
        self.m['br'].pop(13)
        fs=features(self.m,self.prices,self.means,self.stats)
        r=replace(self.r,kind='unbroken',remhi=8)
        self.assertFalse(qualifies(r,fs[8],8))

    def test_stop_executes_at_next_bid_not_trigger_bid(self):
        self.m['br'][9]['bc']=.35;self.m['br'][9]['ac']=.36
        self.m['br'][8]['bo']=.1;self.m['br'][8]['ao']=.11
        row=trade(self.m,replace(self.r,exit='stop40'),self.fs)
        self.assertEqual(row['exitprice'],.1)

    def test_hold_ignores_intermediate_drawdown(self):
        self.m['br'][9]['bc']=.01
        row=trade(self.m,self.r,self.fs)
        self.assertAlmostEqual(row['pnl'],.1-fee(.9))

    def test_frozen_registry_has_unique_names(self):
        rr=rules();self.assertEqual(len(rr),len({r.name for r in rr}))

    def test_validation_and_test_labels_never_enter_fitting(self):
        markets=[];fs=[]
        for i in range(150):
            markets.append(dict(close=ts('2026-08-20T12:00:00Z') if i<120 else ts('2026-09-05T12:00:00Z'),result=1 if i%5 else -1))
            fs.append({7:dict(self.fs[7])})
        first=fit_models(markets,fs)
        for m in markets[120:]:m['result']*=-1
        second=fit_models(markets,fs)
        self.assertEqual(first,second)
        self.assertEqual(first['benchmark']['n'],120)

if __name__=='__main__':unittest.main()
