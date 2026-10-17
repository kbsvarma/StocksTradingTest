"""Public GET-only display feed. Never used by the scanner or execution engine.

One process-wide cache serves all dashboard viewers. Refreshes run off the UI
thread. One batched price request replaces eight per-market refreshes.
Listing discovery is paced separately; all traffic yields at minute boundaries.
"""
from copy import deepcopy
from datetime import datetime
import math
import json
from pathlib import Path
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


def public_rows(params):
    if not hasattr(_LOCAL, 'session'):
        _LOCAL.session = requests.Session()
        _LOCAL.session.headers['User-Agent'] = 'kalshi-dashboard-display/2.0'
    response = _LOCAL.session.get(BASE, params=params, timeout=(2, 3), allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError('Public quotes HTTP ' + str(response.status_code))
    payload = response.json()
    if not isinstance(payload.get('markets'), list) or payload.get('cursor'):
        raise ValueError('Incomplete public listings')
    return payload['markets']


def fetch_public(asset, now):
    if asset not in ACTIVE_ASSETS:
        raise ValueError('Asset not enabled on the dashboard')
    params = dict(series_ticker='KX'+asset+'15M', min_close_ts=int(now),
                  max_close_ts=(int(now)//900+2)*900+1, limit=20)
    return normalize(asset, public_rows(params), time.time())


def fetch_batch(tickers, now):
    # Tickers come only from validated public listings, not a guessed schedule.
    if not tickers or len(tickers) > 100 or any(
            not any(t.startswith('KX'+a+'15M-') for a in ACTIVE_ASSETS) for t in tickers):
        raise ValueError('Invalid display ticker batch')
    rows = public_rows(dict(tickers=','.join(sorted(tickers)), limit=100))
    if any(r.get('ticker') not in tickers for r in rows):
        raise ValueError('Unexpected market in display batch')
    received = time.time()
    return {a: normalize(a, rows, received) for a in ACTIVE_ASSETS}


class QuoteFeed:
    def __init__(self, fetcher=fetch_public, clock=time.time, batcher=fetch_batch,
                 health_path=None):
        self.fetcher, self.clock, self.batcher = fetcher, clock, batcher
        self.health_path = Path(health_path) if health_path else None
        self.lock = threading.Lock()
        self.states = {a: dict(markets=[], checked_ts=0, error='', failures=0) for a in ACTIVE_ASSETS}
        self.discovery_due = {a: 0 for a in ACTIVE_ASSETS}
        self.batch_due = self.allowed_at = 0
        self.last_view = float('-inf')
        self.failures = 0
        self.requests = []
        self.worker = None
        self.stop = threading.Event()

    @staticmethod
    def traffic_time(now):
        # Leave the trader's usual first pass (seconds 2-5) clear of display traffic.
        return now+(7-now%60) if now%60 < 7 else now

    def _accept(self, asset, markets, now):
        previous = {m['ticker']: m for m in self.states[asset]['markets']}
        for m in markets:
            if m['quote'] is None and m['ticker'] in previous:
                m['quote'] = previous[m['ticker']].get('quote')
        self.states[asset] = dict(markets=markets, checked_ts=now, error='', failures=0)

    def step(self):
        """At most one public GET. Shared by all viewers; never called by trading."""
        with self.lock:
            now = self.clock()
            if now-self.last_view > 45 or now < self.traffic_time(self.allowed_at) or now < self.traffic_time(now):
                return False
            known = {a: [m['ticker'] for m in s['markets'] if m['close'] > now]
                     for a, s in self.states.items()}
            tickers = {t for ts in known.values() for t in ts}
            due = [a for a in ACTIVE_ASSETS if self.discovery_due[a] <= now]
            # Price refresh has priority over slow periodic listing discovery.
            if tickers and now >= self.batch_due:
                kind, affected = 'batch', [a for a in ACTIVE_ASSETS if known[a]]
            elif due:
                kind, affected = 'discovery', [min(due, key=lambda a: self.discovery_due[a])]
            else:
                return False
            started = now
            self.allowed_at = now+1.25
        error = ''
        try:
            result = self.batcher(tickers, now) if kind == 'batch' else self.fetcher(affected[0], now)
            with self.lock:
                now = self.clock()
                self.failures = 0
                if kind == 'batch':
                    self.batch_due = now+REFRESH_SECONDS
                    for asset in affected:
                        markets = result.get(asset, [])
                        # A missing current listing must not make an old price fresh.
                        expected = {m['ticker'] for m in self.states[asset]['markets'] if m['start'] <= now < m['close']}
                        if not expected.issubset({m['ticker'] for m in markets}):
                            self.states[asset]['error'] = 'Current market omitted by quote feed'
                            self.discovery_due[asset] = min(self.discovery_due[asset], now+2)
                        else:
                            self._accept(asset, markets, now)
                else:
                    asset = affected[0]
                    self._accept(asset, result, now)
                    upcoming = [m['start'] for m in result if m['start'] > now]
                    self.discovery_due[asset] = min(upcoming)+7 if upcoming else now+60
        except Exception as exc:
            error = str(exc)[:160]
            with self.lock:
                now = self.clock()
                self.failures += 1
                delay = min(30, 2**min(self.failures, 5))
                self.allowed_at = now+delay  # Global backoff, no per-asset retry burst.
                if kind == 'batch': self.batch_due = now+delay
                else: self.discovery_due[affected[0]] = now+delay
                for asset in affected:
                    self.states[asset]['error'] = error
                    self.states[asset]['failures'] += 1
        with self.lock:
            self.allowed_at = max(self.allowed_at, self.clock()+1.25)
            self.requests.append(dict(started=started, finished=self.clock(), kind=kind,
                                      assets=affected, error=error))
            self.requests = self.requests[-200:]
            self._health()
        return True

    def _health(self):
        if self.health_path is None: return
        try:
            data = dict(ts=self.clock(), last_view=self.last_view, next_request=self.allowed_at,
                        batch_due=self.batch_due, discovery_due=self.discovery_due,
                        states=self.states, requests=self.requests)
            temporary = self.health_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data))
            temporary.replace(self.health_path)
        except OSError:
            pass  # Display telemetry must never interrupt quotes.

    def _run(self):
        while not self.stop.is_set():
            self.step()
            self.stop.wait(.25)

    def close(self):
        self.stop.set()
        if self.worker: self.worker.join(timeout=6)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self.last_view = now
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name='display-quotes', daemon=True)
                self.worker.start()
            result = deepcopy(self.states)
            for asset, state in result.items():
                due = self.batch_due if state['markets'] else self.discovery_due[asset]
                state['retry_ts'] = self.traffic_time(max(now, self.allowed_at, due))
            return result


def market_view(state, now):
    markets = state.get('markets', [])
    current = next((m for m in markets if m['start'] <= now < m['close']), None)
    upcoming = next((m for m in markets if m['start'] > now), None)
    q = current.get('quote') if current else None
    age = max(0, now-q['ts']) if q else None
    return dict(current=current, upcoming=upcoming, quote=q, age=age,
                delayed=bool(state.get('error')) or (age is not None and (age > STALE_SECONDS or q['ts'] < current['fetched_ts'])),
                error=state.get('error', ''), checked_ts=state.get('checked_ts', 0), retry_ts=state.get('retry_ts'))


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
            _FEED = QuoteFeed(health_path=Path(__file__).resolve().parent/'state/dashboard_quote_status.json')
    return _FEED.snapshot()
