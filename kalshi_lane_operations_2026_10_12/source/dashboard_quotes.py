"""Public GET-only display feed. Never used by the scanner or execution engine.

One process-wide cache serves all dashboard viewers. Refreshes run off the UI
thread, at most once per ten seconds, with bounded concurrency and no retries.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import math
import threading
import time

import requests

from lanes import ACTIVE_ASSETS

BASE = 'https://api.elections.kalshi.com/trade-api/v2/markets'
REFRESH_SECONDS = 10
STALE_SECONDS = 30
_LOCAL = threading.local()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def probability(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 1 else None
    except (TypeError, ValueError):
        return None


def normalize(asset, rows, received):
    """Keep only this series' actual 15-minute listings, never future dummy quotes."""
    result = []
    for raw in rows:
        try:
            start, close = timestamp(raw['open_time']), timestamp(raw['close_time'])
            ticker = raw['ticker']
            if not ticker.startswith('KX' + asset + '15M-') or close-start != 900:
                continue
            if raw.get('status') not in ('active', 'initialized'):
                continue
            m = dict(ticker=ticker, start=start, close=close, status=raw['status'],
                     fetched_ts=received, quote=None)
            bid = probability(raw.get('yes_bid_dollars'))
            ask = probability(raw.get('yes_ask_dollars'))
            if (raw['status'] == 'active' and start <= received < close
                    and bid is not None and ask is not None and bid <= ask and ask > 0):
                # Derive NO from the complementary YES book, as the scanner does.
                side = 'yes' if bid+ask > 1 else 'no'
                m['quote'] = dict(yes_ask=ask, no_ask=1-bid, side=side,
                                  ask=ask if side == 'yes' else 1-bid, ts=received)
            result.append(m)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            continue
    return sorted(result, key=lambda m: m['close'])


def fetch_public(asset, now):
    if asset not in ACTIVE_ASSETS:
        raise ValueError('Asset not enabled on the dashboard')
    if not hasattr(_LOCAL, 'session'):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers['User-Agent'] = 'kalshi-dashboard-display/1.0'
    # Include the current AND next listed market. No guessed weekend schedule.
    params = dict(series_ticker='KX'+asset+'15M', min_close_ts=int(now),
                  max_close_ts=(int(now)//900+2)*900+1, limit=20)
    response = _LOCAL.session.get(BASE, params=params, timeout=(2, 3), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError('Public quotes HTTP ' + str(response.status_code))
    payload = response.json()
    if not isinstance(payload.get('markets'), list) or payload.get('cursor'):
        raise ValueError('Incomplete public listings')
    return normalize(asset, payload['markets'], time.time())


class QuoteFeed:
    def __init__(self, fetcher=fetch_public, clock=time.time):
        self.fetcher, self.clock = fetcher, clock
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='display-quotes')
        self.states = {a: dict(markets=[], checked_ts=0, error='', failures=0) for a in ACTIVE_ASSETS}
        self.pending = set()
        self.next_refresh = {a: 0 for a in ACTIVE_ASSETS}

    def _refresh(self, asset):
        try:
            markets = self.fetcher(asset, self.clock())
            with self.lock:
                previous = {m['ticker']: m for m in self.states[asset]['markets']}
                # A missing quote may retain its own last quote, explicitly aged;
                # never transfer a quote to another market at a quarter-hour rollover.
                for m in markets:
                    if m['quote'] is None and m['ticker'] in previous:
                        m['quote'] = previous[m['ticker']].get('quote')
                self.states[asset] = dict(markets=markets, checked_ts=self.clock(), error='', failures=0)
        except Exception as exc:
            with self.lock:
                state = self.states[asset]
                state['error'] = str(exc)[:160]
                state['failures'] += 1
                self.next_refresh[asset] = self.clock()+min(60, 10*2**min(state['failures'], 3))
        finally:
            with self.lock:
                self.pending.discard(asset)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            for asset in ACTIVE_ASSETS:
                if asset not in self.pending and now >= self.next_refresh[asset]:
                    self.pending.add(asset)
                    self.next_refresh[asset] = now+REFRESH_SECONDS
                    self.pool.submit(self._refresh, asset)
            return deepcopy(self.states)


def market_view(state, now):
    markets = state.get('markets', [])
    current = next((m for m in markets if m['start'] <= now < m['close']), None)
    upcoming = next((m for m in markets if m['start'] > now), None)
    q = current.get('quote') if current else None
    age = max(0, now-q['ts']) if q else None
    return dict(current=current, upcoming=upcoming, quote=q, age=age,
                delayed=bool(state.get('error')) or (age is not None and (age > STALE_SECONDS or q['ts'] < current['fetched_ts'])),
                error=state.get('error', ''), checked_ts=state.get('checked_ts', 0))


def entry_window(markets, rule, now):
    """Describe the existing completed-minute timing rule, without evaluating signals."""
    decision = math.floor(now/60)*60
    for m in sorted(markets, key=lambda item: item['close']):
        first = max(m['start'], m['close']-60*rule.max_minutes)
        last = m['close']-60*rule.min_minutes
        # Scanner uses the current completed-minute timestamp, inclusive at both ends.
        if m['close'] <= now or decision > last:
            continue
        if decision < first:
            return dict(state='waiting', opens=first, last=last, seconds=max(0, first-now), ticker=m['ticker'])
        return dict(state='open', opens=first, last=last, seconds=0, ticker=m['ticker'])
    return dict(state='unlisted', opens=None, last=None, seconds=None, ticker=None)


def duration(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02d}m' if hours else f'{minutes}m {seconds:02d}s'


_FEED = None
_FEED_LOCK = threading.Lock()


def get_quotes():
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = QuoteFeed()
    return _FEED.snapshot()
