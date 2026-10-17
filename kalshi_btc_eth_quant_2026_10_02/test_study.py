import json, math, unittest, tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import study as s
from pass2 import complement
import forward_capture as capture

class ResearchChecks(unittest.TestCase):
    def test_volatility_excludes_forming_and_future_bars(self):
        T=120*60
        bars={t:100*math.exp(.0001*math.sin(t/60)) for t in range(0,181*60,60)}
        original=s.stats({'BTC':bars})['BTC'][T]
        changed={t:(v*100 if t>=T else v) for t,v in bars.items()}
        self.assertEqual(original,s.stats({'BTC':changed})['BTC'][T])
        self.assertEqual(original[0],bars[T-60])

    def test_missing_completed_bar_is_not_forward_filled(self):
        bars={t:100+math.sin(t/60) for t in range(0,121*60,60)}
        del bars[100*60]
        self.assertNotIn(120*60,s.stats({'BTC':bars})['BTC'])

    def test_complement_uses_executable_ask(self):
        f={'bid':.80,'ask':.82,'side':1,'z':2,'pbasic':.72,'prich':.71,'zaverage':2.2}
        no=complement(f)
        self.assertAlmostEqual(no['ask'],.20)
        self.assertAlmostEqual(no['bid'],.18)
        self.assertAlmostEqual(no['pbasic'],.28)
        self.assertEqual(no['side'],-1)
        self.assertEqual(no['zaverage'],-2.2)

    def test_no_side_consumes_yes_bids_and_full_size(self):
        book=(0,((.80,2),(.79,3)),((.18,100),))
        self.assertIsNone(s.book_fill(book,-1,.20,.10,5))
        entry,paid=s.book_fill(book,-1,.21,.10,5)
        self.assertAlmostEqual(entry,.206)
        self.assertGreater(paid,0)

    def test_shared_risk_blocks_third_five_contract_candidate(self):
        rows={}
        for lane,asset in [('current_signal95','BTC'),('premium_z3','ETH'),('extra','SOL')]:
            rows[lane]=[dict(ticker=asset,asset=asset,entered=1000,settled=2000,close=1900,
                quantity=5,entry=.9,fee=.04,won=1,pnl=.46,pnl_cap=.46,day='1970-01-01')]
        selected,blocked=s.resim(rows,s.BASE+['extra'])
        self.assertEqual([r['asset'] for r in selected],['BTC','ETH'])
        self.assertEqual(blocked['daily'],1)

    def test_probability_fit_ignores_later_outcomes(self):
        def market(close,result,x):
            return dict(asset='BTC',close=close,result=result,features=[dict(side=1,ask=.81,bid=.8,xbasic=x[:5],xrich=x)])
        mm=[market(s.FIT_END-100,-1,[1,1.4,2,0,0,0,.1,.2,.1,0,4]),market(s.FIT_END-50,1,[1,1.5,3,0,0,0,.2,.1,.1,0,5]),market(s.FIT_END+100,1,[1,1.7,4,0,0,0,.1,.1,.1,0,6])]
        with patch.object(s,'save'):
            first=s.fit_models(mm)
            mm[-1]['result']=-1
            second=s.fit_models(mm)
        self.assertEqual(first,second)

    def test_baseline_matches_validated_production_engine(self):
        out=json.loads((s.P/'quote_summary.json').read_text())['later']['baseline']
        self.assertEqual(out['n'],302)
        self.assertEqual(out['losses'],4)
        self.assertAlmostEqual(out['net'],65.9172)

    def test_capture_only_sends_market_data_subscriptions(self):
        msgs=capture.subscriptions(['KXBTC15M-test','KXETH15M-test'])
        for m in msgs:self.assertEqual(m['cmd'],'subscribe')
        channels={c for m in msgs for c in m['params']['channels']}
        self.assertEqual(channels,{'cfbenchmarks_value','cfbenchmarks_value_5hz','orderbook_delta','trade'})
        with self.assertRaises(ValueError):capture.subscriptions(['KXBTC15M-test','KXBTC15M-another'])

    def test_sequence_gap_marks_unreliable_book_interval(self):
        previous={}
        self.assertIsNone(capture.gap(previous,{'sid':2,'seq':1}))
        self.assertIsNone(capture.gap(previous,{'sid':2,'seq':2}))
        self.assertEqual(capture.gap(previous,{'sid':2,'seq':4}),{'sid':2,'previous':2,'current':4})

    def test_capture_excludes_account_and_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'status.json'
            p.write_text(json.dumps(dict(ts=1,mode='LIVE',scans=[{'z':2}],account={'secret':'private'},trades=['private'])))
            row=capture.decision_snapshot(p)
            self.assertEqual(row['scans'],[{'z':2}])
            self.assertNotIn('account',row)
            self.assertNotIn('trades',row)

if __name__=='__main__':unittest.main()
