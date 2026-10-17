import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_usdcad_2026_10_14/candidate'))
sys.path.insert(0,str(P/'candidate'))
import dashboard_quotes as q
import dashboard_panel as panel
spec=importlib.util.spec_from_file_location('legacy',P/'reference_test_quotes.py')
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)

class PreservedBehavior(legacy.QuotesTests):
    # Scheduling was replaced deliberately; covered by new tests below.
    test_refresh_retains_same_market_quote_and_backs_off=None
    test_many_viewers_share_inflight_nonblocking_requests=None

class BatchFeedTests(unittest.TestCase):
    def setUp(self):
        self.clock=[legacy.START+307];self.calls=[]
        def fetch(a,t):
            self.calls.append(('discovery',a,t))
            return q.normalize(a,[legacy.raw(a),legacy.raw(a,legacy.START+900,status='initialized')],t)
        def batch(tickers,t):
            self.calls.append(('batch',set(tickers),t))
            return {a:q.normalize(a,[legacy.raw(a),legacy.raw(a,legacy.START+900,status='initialized')],t) for a in q.ACTIVE_ASSETS}
        self.f=q.QuoteFeed(fetch,lambda:self.clock[0],batch)
        self.f.last_view=self.clock[0]
    def run_for(self,seconds):
        for _ in range(seconds*4):
            self.f.last_view=self.clock[0];self.f.step();self.clock[0]+=.25
    def test_discovery_paced_then_all_eight_share_one_request(self):
        self.run_for(140)
        discoveries=[c for c in self.calls if c[0]=='discovery']
        batches=[c for c in self.calls if c[0]=='batch']
        self.assertEqual(len(discoveries),len(q.ACTIVE_ASSETS))
        self.assertEqual(len(batches[-1][1]),2*len(q.ACTIVE_ASSETS))
        self.assertLess(len(self.calls),25) # Old feed: about 112 requests/140s.
        self.assertTrue(all(b[2]-a[2]>=1.25 for a,b in zip(self.calls,self.calls[1:])))
        self.assertTrue(all(c[2]%60>=7 for c in self.calls))
        self.assertTrue(all(not q.market_view(s,self.clock[0])['delayed'] for s in self.f.states.values()))
    def test_429_global_backoff_then_recovery_keeps_age(self):
        self.run_for(20)
        saved=copy.deepcopy(self.f.states);real=self.f.batcher
        self.f.batcher=lambda *a:(_ for _ in ()).throw(RuntimeError('Public quotes HTTP 429'))
        self.clock[0]=self.f.batch_due;self.f.last_view=self.clock[0]
        self.assertTrue(self.f.step())
        self.assertEqual(self.f.allowed_at,self.clock[0]+2)
        self.assertEqual(saved['BTC']['markets'],self.f.states['BTC']['markets'])
        self.assertFalse(self.f.step())
        self.clock[0]+=2;self.assertTrue(self.f.step())
        self.assertEqual(self.f.allowed_at,self.clock[0]+4)
        self.f.batcher=real;self.clock[0]+=4;self.assertTrue(self.f.step())
        self.assertEqual(self.f.states['BTC']['error'],'')
        self.assertEqual(self.f.failures,0)
    def test_repeated_failures_capped_without_burst(self):
        self.f.fetcher=lambda *a:(_ for _ in ()).throw(TimeoutError('timeout'))
        self.run_for(150)
        self.assertLess(len(self.f.requests),12)
        self.assertTrue(all(r['error']=='timeout' for r in self.f.requests))
        self.assertLessEqual(self.f.allowed_at-self.clock[0],30)
    def test_missing_current_market_never_freshens_old_price(self):
        self.run_for(20);before=copy.deepcopy(self.f.states['USDCAD'])
        real=self.f.batcher
        def batch(tickers,t):
            out=real(tickers,t);out['USDCAD']=[];return out
        self.f.batcher=batch;self.clock[0]=self.f.batch_due;self.f.step()
        self.assertEqual(self.f.states['USDCAD']['checked_ts'],before['checked_ts'])
        self.assertIn('omitted',self.f.states['USDCAD']['error'])
    def test_missing_quote_retains_original_timestamp(self):
        self.run_for(20);before=copy.deepcopy(self.f.states['BTC'])
        now=self.clock[0]+1
        self.f._accept('BTC',q.normalize('BTC',[legacy.raw(yes_bid_dollars=None)],now),now)
        self.assertEqual(self.f.states['BTC']['markets'][0]['quote']['ts'],before['markets'][0]['quote']['ts'])
        self.assertTrue(q.market_view(self.f.states['BTC'],now)['delayed'])
    def test_idle_viewers_stop_requests_and_resume(self):
        self.run_for(20);self.clock[0]+=46
        self.assertFalse(self.f.step());self.f.last_view=self.clock[0]
        self.clock[0]=q.QuoteFeed.traffic_time(self.clock[0]);self.assertTrue(self.f.step())
    def test_no_listing_retries_and_does_not_starve_others(self):
        real=self.f.fetcher
        self.f.fetcher=lambda a,t:[] if a=='WTI' else real(a,t)
        self.run_for(140)
        self.assertGreaterEqual(sum(r['assets']==['WTI'] for r in self.f.requests),2)
        self.assertTrue(self.f.states['USDCAD']['markets'])
    def test_health_local_file_contains_failures_and_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            self.f.health_path=Path(d)/'health.json';self.run_for(20)
            health=json.loads(self.f.health_path.read_text())
            self.assertEqual(len(health['states']),len(q.ACTIVE_ASSETS))
            self.assertIn('started',health['requests'][0]);self.assertIn('error',health['requests'][0])
    def test_many_viewers_do_not_spawn_duplicate_fetchers(self):
        release=threading.Event();calls=[]
        def fetch(*args):calls.append(args);release.wait(2);return []
        f=q.QuoteFeed(fetch,lambda:legacy.START+307,lambda *a:{})
        try:
            began=time.monotonic()
            for _ in range(30):f.snapshot()
            self.assertLess(time.monotonic()-began,.5)
            time.sleep(.05);self.assertEqual(len(calls),1)
        finally:release.set();f.close()
    def test_batch_public_get_uses_exact_tickers_and_no_auth(self):
        class Response:
            status_code=200
            def json(self):return dict(markets=[legacy.raw()],cursor='')
        class Session:
            def get(self,*args,**kwargs):self.call=(args,kwargs);return Response()
        session=Session();tickers={legacy.raw()['ticker']}
        with patch.object(q._LOCAL,'session',session,create=True),patch.object(q.time,'time',return_value=legacy.START+307):
            out=q.fetch_batch(tickers,legacy.START+307)
        args,kwargs=session.call
        self.assertEqual(args,(q.BASE,));self.assertEqual(kwargs['params']['tickers'],','.join(tickers))
        self.assertNotIn('headers',kwargs);self.assertFalse(kwargs['allow_redirects'])
        self.assertIsNotNone(out['BTC'][0]['quote'])
        with self.assertRaises(ValueError):q.fetch_batch({'KXCOPPER15M-1'},legacy.START)
    def test_429_reason_and_retry_visible_with_old_price(self):
        now=legacy.START+307;s=legacy.state(now-29);s.update(error='Public quotes HTTP 429',retry_ts=now+4)
        h=legacy.HTML()
        with patch.object(panel,'get_quotes',return_value={'BTC':s}):panel.render_markets(h,{},[],('BTC',),None,now)
        html=''.join(h.parts)
        for text in ('Quote delayed','29s','Kalshi rate limit (HTTP 429)','next refresh in 0m 04s'):self.assertIn(text,html)

if __name__=='__main__':unittest.main(verbosity=2)
