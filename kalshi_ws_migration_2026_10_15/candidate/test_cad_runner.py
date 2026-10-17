"""Full offline runner: fresh CAD minute requalification and fresh-book checks."""
import json,unittest
from unittest.mock import patch
import runner
from engine import Engine
import test_bounded_reads as cases
class CadRunner(unittest.TestCase):
    setUp=cases.FreshCAD.setUp
    tearDown=cases.FreshCAD.tearDown
    def run_rollover(self,qualifies=True,depth_reappears=False):
        root=self.root/'runner';root.mkdir()
        for name in ('ARM_5CT','ARM_Z3_5CT','ARM_USDCAD_5CT'):(root/name).write_text('5')
        clock={'t':1023.0};scans=[];refresh=[];self.ex.outcomes=['0','5']
        def scan(asset,T,entered,lane):
            if lane!='cushion_z2':return dict(asset=asset,lane=lane,status='no_current_market',decision_ts=T)
            scans.append((T,set(entered)))
            if self.s['ticker'] in entered:return dict(self.s,decision_ts=T,status='already_entered')
            r=dict(self.s,decision_ts=T,remaining_minutes=(1620-T)/60)
            if T>1020:r.update(signal_ask=.93,signal_bid=.92,z=2.8)
            if T>1020 and not qualifies:r.update(status='cushion_gate',z=1)
            if T>1020 and depth_reappears:r['status']='book_outside_limit'
            return r
        def refresh_book(s):
            refresh.append(s['decision_ts'])
            return dict(s,status='book_outside_limit' if s['decision_ts']==1020 else 'paper_fill')
        def sleep(_):
            clock['t']+=1.2
            if clock['t']>1088:raise KeyboardInterrupt()
        with patch.object(runner,'STATE',root),patch.object(runner,'legacy_safe',return_value=''),\
             patch.object(runner.Credentials,'load',return_value=None),patch.object(runner,'make_stream',return_value=None),patch.object(runner,'KalshiClientV2'),\
             patch.object(runner,'Exchange',return_value=self.ex),\
             patch.object(runner,'Engine',side_effect=lambda *a,**k:Engine(*a,clock=lambda:clock['t'],**k)),\
             patch.object(runner.market_data,'ASSETS',('USDCAD',)),patch.object(runner.market_data,'warm_sources',return_value={}),\
             patch.object(runner.market_data,'scan',side_effect=scan),\
             patch.object(runner.market_data,'refresh_book',side_effect=refresh_book),\
             patch.object(runner.market_data,'get'),patch.object(runner.time,'time',side_effect=lambda:clock['t']),\
             patch.object(runner.time,'sleep',side_effect=sleep),patch.object(runner.signal,'signal'),\
             patch('sys.argv',['runner.py','--live']):
            with self.assertRaises(KeyboardInterrupt):runner.main()
        self.assertTrue(any(t==1080 and self.s['ticker'] not in entered for t,entered in scans))
        self.assertEqual(len(self.ex.posted),2 if qualifies else 1)
        if qualifies:
            self.assertEqual(self.ex.posted[-1]['signal']['decision_ts'],1080)
            self.assertEqual(self.ex.posted[-1]['signal']['signal_ask'],.93)
            self.assertEqual(self.ex.posted[-1]['requested_count'],'5')
        if depth_reappears:self.assertIn(1080,refresh)
        d=json.loads((root/'status.json').read_text());self.assertEqual(d['usdcad_fresh_retry_version'],1)
    def test_runner_requalifies_after_minute_rollover(self):self.run_rollover()
    def test_runner_does_not_reuse_old_signal_when_new_z_fails(self):self.run_rollover(qualifies=False)
    def test_runner_keeps_checking_new_minute_book_without_reusing_old_signal(self):self.run_rollover(depth_reappears=True)

