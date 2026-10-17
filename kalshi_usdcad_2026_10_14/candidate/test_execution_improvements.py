import unittest
import json
from pathlib import Path
from unittest.mock import patch,Mock
from runner import MinuteQuotes,scan_ready
from execution_retry import retry_allowed
from lanes import Z2,Z3
import test_engine as fixtures
import market_data
import test_signals as scanner_fixtures

URL='https://api.elections.kalshi.com/trade-api/v2/series/KXBTC15M/markets/TEST/candlesticks?end_ts=12000'
def candle(bid='.90',ask='.91',T=12000):
    return {'candlesticks':[{'end_period_ts':T,'yes_bid':{'close_dollars':bid},'yes_ask':{'close_dollars':ask}}]}

class CacheTests(unittest.TestCase):
    def test_missing_candle_recovers_then_valid_quote_is_frozen(self):
        responses=iter([{'candlesticks':[]},candle(),candle('.91','.92')]);calls=[]
        def get(u):calls.append(u);return next(responses)
        cache=MinuteQuotes(get);cache.begin(12000)
        self.assertEqual(cache(URL),{'candlesticks':[]})
        self.assertEqual(cache(URL),candle())
        self.assertEqual(cache(URL),candle());self.assertEqual(len(calls),2)
        cache.begin(12060);self.assertEqual(cache(URL),candle('.91','.92'))
    def test_missing_quote_and_wrong_minute_are_not_cached(self):
        for bad in [candle(None,'.91'),candle('.90',None),candle(T=11940),candle('NaN','.91'),candle('.95','.94')]:
            with self.subTest(bad=bad):
                responses=iter([bad,candle()]);cache=MinuteQuotes(lambda u:next(responses))
                self.assertEqual(cache(URL),bad);self.assertEqual(cache(URL),candle())
    def test_zero_and_one_prices_are_valid_and_frozen(self):
        for c in [candle('0','0'),candle('1','1')]:
            self.assertTrue(MinuteQuotes.cacheable(URL,c))
    def test_scanner_recovers_candle_using_frozen_other_inputs(self):
        T=12000;responses=scanner_fixtures.PaperProbeTests().responses(T);calls=[]
        def get(u):
            calls.append(u)
            if '/candlesticks?' in u:return {'candlesticks':[]} if sum('/candlesticks?' in x for x in calls)==1 else responses[1]
            if '/markets?' in u:return responses[0]
            if 'coinbase' in u:return responses[2]
            return responses[3]
        cache=MinuteQuotes(get);cache.begin(T)
        with patch.object(market_data,'get',cache),patch.object(market_data.time,'time',return_value=T+3):
            self.assertEqual(market_data.scan('BTC',T,set())['status'],'decision_candle_unavailable')
            self.assertEqual(market_data.scan('BTC',T,set())['status'],'paper_fill')
        self.assertEqual(sum('/markets?' in u for u in calls),1)
        self.assertEqual(sum('/candlesticks?' in u for u in calls),2)

class SchedulingTests(unittest.TestCase):
    def test_eight_checks_no_longer_discard_remaining_time(self):
        self.assertTrue(retry_allowed('paper_fill','Insufficient depth inside cap',8,13.7,False))
        self.assertTrue(retry_allowed('book_outside_limit','',18,24.9,False))
        self.assertFalse(retry_allowed('book_outside_limit','',24,24.9,False))
        self.assertFalse(retry_allowed('book_outside_limit','',10,25.001,False))
    def test_no_retry_after_order_or_weakened_book_or_risk_rejection(self):
        for checks in (1,8,18):
            self.assertFalse(retry_allowed('paper_fill','ack',checks,20,True))
            self.assertFalse(retry_allowed('book_outside_limit','Book weakened below floor',checks,20,False))
            self.assertFalse(retry_allowed('paper_fill','Exposure cap',checks,20,False))
    def test_z3_keeps_original_priority_timing(self):
        self.assertFalse(scan_ready(Z3,'BTC',set(),{(Z2,'BTC'):7},13))
        self.assertTrue(scan_ready(Z3,'BTC',set(),{(Z2,'BTC'):8},13))
        self.assertTrue(scan_ready(Z3,'BTC',{(Z2,'BTC')},{(Z2,'BTC'):1},3))
    def test_queued_retries_cannot_start_after_deadline(self):
        self.assertFalse(scan_ready(Z2,'BTC',set(),{(Z2,'BTC'):8},25.01))
        self.assertFalse(scan_ready(Z3,'BTC',{(Z2,'BTC')},{(Z3,'BTC'):1},25.01))
        self.assertFalse(scan_ready(Z2,'BTC',{(Z2,'BTC')},{},3))
    def test_late_valid_book_can_submit_only_once(self):
        f=fixtures.Tests();f.setUp()
        try:
            original=f.ex.book;f.ex.book=lambda _:dict(no_dollars=[['.01','100']])
            s=fixtures.signal()
            for n in range(1,9):
                f.now=1003+(n-1)*1.4
                self.assertEqual(f.engine.attempt(s),'Insufficient depth inside cap')
                self.assertEqual(f.store.all(),[])
            f.now=1018
            f.ex.book=original
            self.assertEqual(f.engine.attempt(s),'ack')
            self.assertEqual(f.engine.attempt(s),'Already attempted this market')
            self.assertEqual(len(f.ex.posted),1)
        finally:f.tearDown()

    def test_two_recorded_late_books_pass_the_unmodified_execution_engine(self):
        root=Path(__file__).resolve().parents[1]
        data=json.loads((root/'inputs.json').read_text())
        observations=[json.loads(line) for line in (root/'observer.jsonl').read_text().splitlines()]
        for ticker in ['KXBTC15M-26OCT030030-30','KXBTC15M-26OCT030945-45']:
            f=fixtures.Tests();f.setUp()
            try:
                s=dict(next(e for e in data['events'] if e.get('ticker')==ticker and e.get('lane')==Z2 and e.get('z',0)>=2),status='paper_fill')
                book=next(o for o in observations if o.get('ticker')==ticker and o['decision_ts']==s['decision_ts']
                          and 15<o['observed_ts']-s['decision_ts']<=25 and o['depth']['5']['enough'] and not o['depth']['5']['below_floor'])
                side='no_dollars' if s['side']=='yes' else 'yes_dollars'
                f.ex.book=lambda _,side=side,book=book:{side:book['levels']}
                f.now=book['observed_ts']
                if s['z'] < 3:
                    with self.assertRaisesRegex(ValueError,'signal gates failed'):
                        f.engine.attempt(s)
                    self.assertFalse(f.ex.posted)
                    continue
                self.assertEqual(f.engine.attempt(s),'ack')
                self.assertEqual(f.ex.posted[0]['requested_count'],'5')
                self.assertLessEqual(float(f.ex.posted[0]['favorite_limit']),.939)
                self.assertEqual(f.engine.attempt(s),'Already attempted this market')
                self.assertEqual(len(f.ex.posted),1)
            finally:f.tearDown()

class TransportTests(unittest.TestCase):
    def test_public_get_reuses_session_and_keeps_timeout(self):
        response=Mock();response.json.return_value={'markets':[]};session=Mock();session.get.return_value=response
        with patch.object(market_data,'_PUBLIC_SESSION',session):
            self.assertEqual(market_data.get(market_data.BASE+'/markets'),{'markets':[]})
            self.assertEqual(market_data.get(market_data.BASE+'/markets'),{'markets':[]})
        self.assertEqual(session.get.call_count,2)
        session.get.assert_called_with(market_data.BASE+'/markets',timeout=15)
        self.assertEqual(response.raise_for_status.call_count,2)
    def test_disallowed_host_is_rejected_before_transport(self):
        with patch.object(market_data,'_PUBLIC_SESSION') as session:
            for url in ['http://api.elections.kalshi.com/trade-api/v2/markets','https://evil.invalid','https://api.exchange.coinbase.com.evil.invalid/candles']:
                with self.assertRaises(ValueError):market_data.get(url)
            session.get.assert_not_called()

if __name__=='__main__':unittest.main()
