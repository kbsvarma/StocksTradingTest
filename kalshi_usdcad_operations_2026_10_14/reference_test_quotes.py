import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P/'original'))
sys.path.insert(0, str(P/'candidate'))
import dashboard_quotes as quotes
import dashboard_panel as panel
from lanes import lane_for, Z2, Z3

START = 1791820800  # An exact UTC quarter-hour.


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def raw(asset='BTC', start=START, **kwargs):
    return dict(ticker=f'KX{asset}15M-{start}', open_time=iso(start), close_time=iso(start+900),
                status='active', yes_bid_dollars='.60', yes_ask_dollars='.62') | kwargs


def state(now, asset='BTC'):
    return dict(markets=quotes.normalize(asset, [raw(asset), raw(asset, START+900, status='initialized')], now),
                checked_ts=now, error='')


class HTML:
    def __init__(self):self.parts=[]
    def html(self, value):self.parts.append(value)
    def caption(self, value):self.parts.append(value)
    def expander(self, *args):return self
    def dataframe(self, *args, **kwargs):pass
    def __enter__(self):return self
    def __exit__(self, *args):pass


class QuotesTests(unittest.TestCase):
    def test_normalize_future_quotes_not_shown(self):
        s=state(START+60)
        self.assertIsNone(s['markets'][1]['quote'])
        self.assertAlmostEqual(s['markets'][0]['quote']['ask'], .62)
        self.assertAlmostEqual(s['markets'][0]['quote']['no_ask'], .40)

    def test_favorite_switch_does_not_change_scanner(self):
        m=quotes.normalize('BTC',[raw(yes_bid_dollars='.08', yes_ask_dollars='.09')],START+300)[0]
        self.assertEqual(m['quote']['side'],'no')
        self.assertAlmostEqual(m['quote']['ask'],.92)

    def test_bad_inputs_are_not_prices(self):
        for bid,ask in [('NaN','.5'),('.7','.5'),('0','0'),('-.1','.8'),('.5','inf')]:
            with self.subTest(bid=bid,ask=ask):
                self.assertIsNone(quotes.normalize('BTC',[raw(yes_bid_dollars=bid,yes_ask_dollars=ask)],START+1)[0]['quote'])
        self.assertEqual(quotes.normalize('BTC',[raw('ETH'), {}, raw(close_time=iso(START+3600))],START+1),[])

    def test_rollover_never_reuses_previous_market_quote(self):
        s=state(START+899)
        before=quotes.market_view(s,START+899)
        after=quotes.market_view(s,START+900)
        self.assertIsNotNone(before['quote'])
        self.assertNotEqual(before['current']['ticker'],after['current']['ticker'])
        self.assertIsNone(after['quote'])
        self.assertIsNone(quotes.market_view(s,START+1800)['current'])

    def test_delayed_age_and_failure(self):
        s=state(START+300)
        self.assertFalse(quotes.market_view(s,START+320)['delayed'])
        self.assertTrue(quotes.market_view(s,START+331)['delayed'])
        s['error']='timeout'
        self.assertTrue(quotes.market_view(s,START+301)['delayed'])

    def test_entry_boundaries_match_current_scanner(self):
        for asset in ('BTC','ETH','NATGAS','EURUSD','WTI'):
            for lane in (Z2,Z3):
                rule=lane_for(dict(asset=asset,lane=lane))
                markets=state(START+1,asset)['markets']
                for offset in range(0,900):
                    now=START+offset;decision=now//60*60
                    expected=rule.min_minutes <= (START+900-decision)/60 <= rule.max_minutes
                    self.assertEqual(quotes.entry_window(markets,rule,now)['state']=='open',expected,(asset,lane,offset))

    def test_countdown_points_to_actual_next_listing(self):
        rule=lane_for(dict(asset='BTC',lane=Z2));s=state(START+120)
        first=quotes.entry_window(s['markets'],rule,START+120)
        self.assertEqual(first['seconds'],180)
        next_=quotes.entry_window(s['markets'],rule,START+780)
        self.assertEqual(next_['opens'],START+1200)
        self.assertEqual(next_['seconds'],420)
        self.assertEqual(quotes.entry_window(s['markets'][:1],rule,START+780)['state'],'unlisted')

    def test_closed_session_does_not_guess_next_window(self):
        rule=lane_for(dict(asset='NATGAS',lane=Z2))
        self.assertEqual(quotes.entry_window([],rule,START)['state'],'unlisted')

    def test_refresh_retains_same_market_quote_and_backs_off(self):
        clock=[START+300];results=[state(clock[0])['markets']]
        def fetch(asset,now):
            if isinstance(results[0],Exception):raise results[0]
            return copy.deepcopy(results[0])
        feed=quotes.QuoteFeed(fetch,lambda:clock[0])
        try:
            feed._refresh('BTC');clock[0]+=10
            results[0]=RuntimeError('HTTP 429');feed._refresh('BTC')
            self.assertEqual(feed.states['BTC']['failures'],1)
            self.assertGreaterEqual(feed.next_refresh['BTC'],clock[0]+20)
            self.assertIsNotNone(quotes.market_view(feed.states['BTC'],clock[0])['quote'])
            results[0]=quotes.normalize('BTC',[raw(yes_bid_dollars=None)],clock[0])
            feed._refresh('BTC')
            self.assertTrue(quotes.market_view(feed.states['BTC'],clock[0])['delayed'])
            self.assertEqual(feed.states['BTC']['failures'],0)
        finally:feed.pool.shutdown(wait=True)

    def test_many_viewers_share_inflight_nonblocking_requests(self):
        release=threading.Event();calls=[]
        def fetch(asset,now):
            calls.append(asset);release.wait(2);return []
        feed=quotes.QuoteFeed(fetch,lambda:START)
        try:
            began=time.monotonic()
            for _ in range(20):feed.snapshot()
            self.assertLess(time.monotonic()-began,.5)
            self.assertEqual(len(feed.pending),len(quotes.ACTIVE_ASSETS))
        finally:release.set();feed.pool.shutdown(wait=True)
        self.assertEqual(len(calls),len(quotes.ACTIVE_ASSETS))

    def test_http_is_public_get_only_and_validated(self):
        class Response:
            status_code=200
            def json(self):return dict(markets=[raw()],cursor='')
        class Session:
            def get(self,*args,**kwargs):self.call=(args,kwargs);return Response()
        session=Session()
        with patch.object(quotes._LOCAL,'session',session,create=True),patch.object(quotes.time,'time',return_value=START+10):
            self.assertEqual(len(quotes.fetch_public('BTC',START+10)),1)
            args,kwargs=session.call
            self.assertEqual(args,(quotes.BASE,))
            self.assertFalse(kwargs['allow_redirects'])
            self.assertNotIn('headers',kwargs)
            with self.assertRaises(ValueError):quotes.fetch_public('COPPER',START)

    def test_render_outside_window_still_shows_prices(self):
        now=START+120;fake=HTML()
        scans=[dict(asset='BTC',lane=Z2,status='outside_window',ticker=raw()['ticker'],observed_ts=now,remaining_minutes=13)]
        d=dict(scans=scans);before=copy.deepcopy(d)
        with patch.object(panel,'get_quotes',return_value={'BTC':state(now)}):panel.render_markets(fake,d,[],('BTC',),None,now)
        html=''.join(fake.parts)
        for expected in ('62.0¢','YES ask','Next entry in 3m 00s','Opens ','Outside entry window','Public quote'):
            self.assertIn(expected,html)
        self.assertEqual(d,before)

    def test_render_separates_live_quote_signal_and_holding(self):
        now=START+360;fake=HTML();ticker=raw()['ticker']
        d=dict(scans=[dict(asset='BTC',lane=Z2,status='cushion_gate',ticker=ticker,signal_ask=.919,side='no',z=1.5,required_z=2,observed_ts=now,decision_ts=now)])
        rows=[dict(asset='BTC',lane=Z2,status='open',ticker=ticker,filled=5,entry='.9',side='no')]
        with patch.object(panel,'get_quotes',return_value={'BTC':state(now)}):panel.render_markets(fake,d,rows,('BTC',),None,now)
        html=''.join(fake.parts)
        for expected in ('62.0¢','Scanner used 91.9¢ NO','Position entry 90.0¢ NO','Holding 5 contracts','Entry window open'):self.assertIn(expected,html)

    def test_old_signal_not_attached_to_new_market(self):
        now=START+920;fake=HTML()
        s=dict(markets=quotes.normalize('BTC',[raw(start=START+900)],now),checked_ts=now,error='')
        d=dict(scans=[dict(asset='BTC',lane=Z2,status='cushion_gate',ticker=raw()['ticker'],signal_ask=.919,side='no',z=1.5,observed_ts=now)])
        with patch.object(panel,'get_quotes',return_value={'BTC':s}):panel.render_markets(fake,d,[],('BTC',),None,now)
        html=''.join(fake.parts)
        self.assertIn('New market · awaiting scanner',html)
        self.assertNotIn('Scanner used',html)
        self.assertNotIn('z 1.50',html)


if __name__=='__main__':unittest.main(verbosity=2)
