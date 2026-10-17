"""Offline, full-input ceiling experiment. No exchange client or credentials.

Production scanner/engine copies run against historical GET fixtures and a fake
exchange. Engine live=True is needed to exercise guards, but its exchange is
strictly in-memory and outbound networking is disabled for the entire replay.
"""
from __future__ import annotations
import bisect
import collections
import datetime as dt
from decimal import Decimal as D, ROUND_CEILING
import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path
import socket
import statistics
import sys
import tempfile
from unittest.mock import patch
from zoneinfo import ZoneInfo

P = Path(__file__).resolve().parent
ROOT = P.parent
ET = ZoneInfo('America/New_York')
ASSETS = ('BTC', 'ETH', 'SOL', 'XRP')


def timestamp(value):
    value = re.sub(r'\.(\d+)', lambda m: '.'+m.group(1)[:6].ljust(6,'0'), value)
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def day(t):
    return dt.datetime.fromtimestamp(t, ET).date().isoformat()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.modules['sizing'] = module('sizing', P / 'reference/sizing.py')
MODULES = {name: (module(name + '_scan', P / name / 'market_data.py'),
                  module(name + '_engine', P / name / 'engine.py'))
           for name in ('reference', 'candidate')}


def load(label, folder, start, end):
    data = dict(label=label, start=start, end=end, markets={}, candles={},
                spots={}, stats={}, books=collections.defaultdict(list))
    for asset in ASSETS:
        for line in (folder / 'fresh_data' / f'markets_{asset}.jsonl').read_text().splitlines():
            m = json.loads(line)
            # Counterfactual current strategy on an earlier exchange route.
            # Normalize administrative route only; preserve all market inputs.
            data['markets'][m['ticker']] = dict(m, asset=asset,
                historical_exchange_index=m.get('exchange_index'), exchange_index=2,
                settlement_ts=dt.datetime.fromtimestamp(timestamp(m.get('settlement_ts') or
                    m.get('settlement_time') or m['close_time']),dt.timezone.utc).isoformat())
        for line in (folder / 'fresh_data' / f'candles_{asset}.jsonl').read_text().splitlines():
            row = json.loads(line)
            data['candles'][row['ticker']] = {c['t']: c for c in row['candles']}
        bars = {}
        for line in (folder / 'fresh_data' / f'spot_{asset}.jsonl').read_text().splitlines():
            for row in json.loads(line)['rows']:
                bars[int(row[0])] = float(row[4])
        data['spots'][asset] = bars
        stats = {}
        for t in range(start // 60 * 60, end, 60):
            keys = list(range(t - 3660, t, 60))
            if not all(k in bars for k in keys):
                continue
            vals = [bars[k] for k in keys]
            sigma = statistics.pstdev(math.log(y / x) for x, y in zip(vals, vals[1:]))
            if sigma > 0:
                stats[t] = (vals[-1], sigma)
        data['stats'][asset] = stats
    if (folder / 'books.jsonl').exists():
        for line in (folder / 'books.jsonl').open():
            row = json.loads(line)
            data['books'][row['ticker']].append(row)
        for rows in data['books'].values():
            rows.sort(key=lambda r: r['ts'])
    data['btimes'] = {k: [r['ts'] for r in v] for k, v in data['books'].items()}
    return data


def make_book(side, price, qty=1000):
    return {'yes_dollars': [] if side == 'yes' else [[str(1-D(str(price))), str(qty)]],
            'no_dollars': [] if side == 'no' else [[str(1-D(str(price))), str(qty)]]}


def scan_fixture(scanner, data, market, t, book, observed):
    c = data['candles'][market['ticker']][t]
    def get(url):
        if '/markets?' in url:
            return {'markets': [market]}
        if '/candlesticks?' in url:
            return {'candlesticks': [{'end_period_ts': t,
                'yes_bid': {'close_dollars': c['bc']}, 'yes_ask': {'close_dollars': c['ac']}}]}
        if 'coinbase.com' in url:
            return [[k, 0, 0, 0, v, 0] for k, v in data['spots'][market['asset']].items()
                    if t-95*60 <= k <= t]
        if '/orderbook?' in url:
            return {'orderbook_fp': book}
        raise AssertionError('Unexpected offline fixture URL: ' + url)
    with patch.object(scanner, 'get', get), patch.object(scanner.time, 'time', lambda: observed):
        return scanner.scan(market['asset'], t, set())


def signals(data):
    rows = []
    quality = collections.Counter()
    for ticker, m in data['markets'].items():
        start, close = int(timestamp(m['open_time'])), int(timestamp(m['close_time']))
        asset = m['asset']
        for t in range(start+300, close-120, 60):
            if not data['start'] <= t < data['end']:
                continue
            quality['decision_minutes'] += 1
            c = data['candles'].get(ticker, {}).get(t)
            try:
                bid, ask = float(c['bc']), float(c['ac'])
            except (KeyError, TypeError, ValueError):
                quality['missing_quote'] += 1
                continue
            if not 0 <= bid <= ask <= 1:
                quality['invalid_quote'] += 1
                continue
            side = 'yes' if bid+ask > 1 else 'no'
            bid, ask = (bid, ask) if side == 'yes' else (1-ask, 1-bid)
            if not .85-1e-8 <= ask < .935-1e-8 or ask-bid > .02000001:
                continue
            quality['wider_price_qualified_minutes'] += 1
            stats = data['stats'][asset].get(t)
            ref = data['spots'][asset].get(start-60)
            if stats is None or ref is None:
                quality['incomplete_spot'] += 1
                continue
            spot, sigma = stats
            z = (1 if side == 'yes' else -1)*(spot/ref-1)/(sigma*math.sqrt((close-t)/60))
            if z < 2:
                continue
            # Validate rebuilt inputs against actual scanner, not ledger z values.
            scanned = scan_fixture(MODULES['candidate'][0], data, m, t,
                                   make_book(side, ask), t+3)
            assert scanned['status'] == 'paper_fill', scanned
            assert scanned['side'] == side and abs(scanned['z']-z) < 1e-10
            assert scanned['strike_proxy'] == ref
            original = scan_fixture(MODULES['reference'][0], data, m, t,
                                    make_book(side, ask), t+3)
            assert original['status'] == ('paper_fill' if ask < .93-1e-8 else 'price_gate')
            scanned['baseline_eligible'] = ask < .93-1e-8
            rows.append(scanned)
    rows.sort(key=lambda s: (s['decision_ts'], ASSETS.index(s['asset'])))
    return rows, dict(quality)


def fills_at(book, side, cap, n):
    chunks = []
    remaining = D(n)
    for p, q in sorted((1-D(p), D(q)) for p, q in
                       book.get('no_dollars' if side == 'yes' else 'yes_dollars', [])):
        if q <= 0:
            continue
        if p < D('.85'):
            return None
        if p > cap:
            break
        take = min(remaining, q)
        chunks.append((take, p))
        remaining -= take
        if remaining == 0:
            return chunks
    return None


def economics(chunks, charge_at_cap=None, precision=D('.0001')):
    cost = sum(q*(charge_at_cap if charge_at_cap is not None else p) for q,p in chunks)
    fee = sum(D('.07')*q*(charge_at_cap if charge_at_cap is not None else p)*
              (1-(charge_at_cap if charge_at_cap is not None else p)) for q,p in chunks)
    # Align combined debit, matching the published direct-member precision.
    debit = ((cost+fee)/precision).to_integral_value(rounding=ROUND_CEILING)*precision
    return cost, debit-cost


class FakeExchange:
    def __init__(self, engine, store, data, clock, starting_cash, delay, charge_cap, precision):
        self.engine, self.store, self.data, self.clock = engine, store, data, clock
        self.starting_cash, self.delay = D(starting_cash), delay
        self.charge_cap, self.precision = charge_cap, precision
        self.current_book = None
        self.executions = {}
    def positions(self):
        return [{'ticker': r['ticker'], 'position_fp': r['filled']}
                for r in self.store.all() if r['status'] in self.engine.ACTIVE and D(r['filled']) > 0]
    def orders(self, **kwargs):
        return []
    def balance(self):
        cash = self.starting_cash
        for r in self.store.all():
            if r['status'] == 'settled':
                cash += D(r['pnl'])
            elif r['status'] in self.engine.ACTIVE:
                cash -= D(r.get('cost', '0'))+D(r.get('fees', '0'))
        return cash
    def book(self, ticker):
        return self.current_book
    def submit(self, record):
        # This object has no transport/client and can only simulate a fill.
        n = D(record['requested_count'])
        chunks = fills_at(self.current_book, record['side'], D(record['favorite_limit']), n)
        assert chunks is not None
        cost, fee = economics(chunks, D(record['favorite_limit']) if self.charge_cap else None, self.precision)
        oid = 'offline-' + record['ticker']
        self.executions[oid] = (dict(record), cost, fee)
        return {'order_id': oid}
    def order(self, oid):
        r, _, _ = self.executions[oid]
        return dict(ticker=r['ticker'], client_order_id=r['client_id'],
                    fill_count_fp=r['requested_count'], remaining_count_fp='0',
                    initial_count_fp=r['requested_count'], status='executed')
    def fills(self, oid):
        r,cost,fee = self.executions[oid]
        price = cost/D(r['requested_count'])
        return [dict(order_id=oid, fill_id=oid, ticker=r['ticker'], count_fp=r['requested_count'],
                     yes_price_dollars=str(price if r['side']=='yes' else 1-price), fee_cost=str(fee))]
    def market(self, ticker):
        m = self.data['markets'][ticker]
        settle = timestamp(m.get('settlement_ts') or m.get('settlement_time') or m['close_time'])
        if self.clock[0] >= max(settle, timestamp(m['close_time'])+30)+self.delay:
            return m
        return dict(m, status='closed', result='')


def observations(data, s, mode):
    t, ticker = s['decision_ts'], s['ticker']
    if mode == 'book':
        times = data['btimes'].get(ticker, [])
        first = bisect.bisect_left(times, t+2)
        checks = 0
        for i in range(first, len(times)):
            when = times[i]
            # Primary first request may complete <=35s. Subsequent historical
            # samples are admitted only <=25s and at most 8 checks in total.
            if when > t+35 or (checks and when > t+25) or checks >= 8:
                break
            row = data['books'][ticker][i]
            checks += 1
            yield when, {'yes_dollars': row['yes'], 'no_dollars': row['no']}
    else:
        c = data['candles'][ticker].get(t+60)
        try:
            price = D(c['ao']) if s['side']=='yes' else 1-D(c['bo'])
        except (KeyError, TypeError, ValueError):
            return
        # Candle-open proxy: price is known historically but precise timestamp
        # and depth at t+3 are NOT proven. Never label this as a recorded book.
        yield t+3, make_book(s['side'], price)


def summarize(rows):
    equity = peak = drawdown = 0.
    daily = collections.defaultdict(float)
    for r in sorted(rows, key=lambda r: (r['settled_ts'], r['ticker'])):
        equity += r['pnl']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        daily[day(r['settled_ts'])] += r['pnl']
    return dict(trades=len(rows), wins=sum(r['win'] for r in rows),
                losses=sum(not r['win'] for r in rows), pnl=sum(r['pnl'] for r in rows),
                fees=sum(r['fees'] for r in rows), drawdown=drawdown, daily=dict(daily))


def run(data, all_signals, variant, mode, n=5, delay=0, charge_cap=False, precision=D('.0001')):
    scanner, engine = MODULES[variant]
    signals_ = [s for s in all_signals if variant=='candidate' or s['baseline_eligible']]
    events = []
    missing = collections.Counter()
    for s in signals_:
        samples = list(observations(data, s, mode))
        if not samples:
            missing['no_book_in_fresh_window' if mode=='book' else 'missing_next_open'] += 1
        for when, book in samples:
            events.append((when, ASSETS.index(s['asset']), s, book))
    events.sort(key=lambda e: e[:2])
    clock = [data['start']]
    skips = collections.Counter(missing)
    done_minutes = set()
    checks = collections.Counter()
    replayed = []
    with tempfile.TemporaryDirectory(prefix='offline_ceiling_') as tmp:
        store = engine.Store(Path(tmp)/'orders.sqlite3')
        fake = FakeExchange(engine, store, data, clock, '50', delay, charge_cap, precision)
        bot = engine.Engine(fake, store, tmp, live=True, clock=lambda: clock[0])
        with patch.object(engine, 'CONTRACTS', n), patch.object(scanner, 'CONTRACTS', n), \
             patch.object(engine, 'today', lambda: day(clock[0])):
            for when, _, s, book in events:
                key = (s['ticker'], s['decision_ts'])
                if key in done_minutes or store.get(s['ticker']):
                    continue
                clock[0] = when
                assert not bot.reconcile()
                fake.current_book = book
                def get(url):
                    assert '/orderbook?' in url
                    return {'orderbook_fp': book}
                with patch.object(scanner, 'get', get), patch.object(scanner.time, 'time', lambda: when):
                    rec = scanner.refresh_book(s)
                checks[key] += 1
                reason = bot.attempt(rec) if rec['status']=='paper_fill' else rec['status']
                if reason == 'ack':
                    assert not bot.reconcile()
                    r = store.get(s['ticker'])
                    m = data['markets'][s['ticker']]
                    won = m['result'] == s['side']
                    cost, fee = D(r['cost']), D(r['fees'])
                    assert cost/n <= D(r['favorite_limit'])
                    replayed.append(dict(ticker=s['ticker'], asset=s['asset'], side=s['side'],
                        decision_ts=s['decision_ts'], entry_ts=when, settled_ts=timestamp(m['settlement_ts']),
                        z=s['z'], signal_ask=s['signal_ask'], count=n, price=float(cost/n),
                        cap=float(D(r['favorite_limit'])), fees=float(fee), win=won,
                        pnl=float(n*D(int(won))-cost-fee), newly_admitted=not s['baseline_eligible']))
                    done_minutes.add(key)
                else:
                    skips[reason] += 1
                    retryable = reason in ('book_outside_limit','insufficient_depth','Insufficient depth inside cap',
                                            'Order reconciliation pending') and rec.get('observed_ask',1)>=.85
                    if not retryable or checks[key]>=8 or when-s['decision_ts']>25:
                        done_minutes.add(key)
            store.db.close()
    return dict(dataset=data['label'], variant=variant, mode=mode, contracts=n, extra_settlement_delay=delay,
                charge_at_cap=charge_cap, fee_precision=str(precision), starting_cash=50,
                signal_minutes=len(signals_), signal_markets=len({s['ticker'] for s in signals_}),
                **summarize(replayed), skips=dict(skips), rows=replayed)


def compare(base, candidate):
    b = {r['ticker']:r for r in base['rows']}
    c = {r['ticker']:r for r in candidate['rows']}
    added = [c[t] for t in c.keys()-b.keys()]
    removed = [b[t] for t in b.keys()-c.keys()]
    common = [dict(ticker=t, delta_pnl=c[t]['pnl']-b[t]['pnl'],
                   entry_time_change=c[t]['entry_ts']-b[t]['entry_ts']) for t in b.keys() & c.keys()
              if abs(c[t]['pnl']-b[t]['pnl'])>1e-9 or c[t]['entry_ts']!=b[t]['entry_ts']]
    delta = candidate['pnl']-base['pnl']
    assert abs(delta-(sum(r['pnl'] for r in added)-sum(r['pnl'] for r in removed)+
                      sum(r['delta_pnl'] for r in common)))<1e-8
    return dict(delta_trades=candidate['trades']-base['trades'], delta_pnl=delta,
                added=summarize(added), removed=summarize(removed),
                added_rows=added, removed_rows=removed, changed_common_rows=common)


def main():
    windows = [('august', ROOT/'kalshi_current_on_august_2026_09_28', 1786173060, 1786514400),
               ('september', ROOT/'kalshi_execution_audit_2026_10_02',
                int(dt.datetime(2026,9,23,tzinfo=ET).timestamp()), int(dt.datetime(2026,10,2,tzinfo=ET).timestamp()))]
    output = {'definition': 'Entry ask ceiling only: <0.930 to <0.935; absolute execution cap remains 0.939.',
              'datasets': {}, 'pairs': []}
    for label, folder, start, end in windows:
        data = load(label, folder, start, end)
        sigs, quality = signals(data)
        output['datasets'][label] = dict(start=start,end=end,markets=len(data['markets']),
            book_snapshots=sum(len(v) for v in data['books'].values()),quality=quality,
            validated_signals=len(sigs), signals=sigs)
        for n in (5,2):
            cases = [('next_open',0,False,D('.0001')),('next_open',0,True,D('.0001'))]
            if data['books']:
                cases = [('book',0,False,D('.0001')),('book',0,True,D('.0001'))]+cases
            if n==5:
                cases += [('book' if data['books'] else 'next_open',60,False,D('.0001')),
                          ('book' if data['books'] else 'next_open',0,False,D('.01'))]
            for mode, delay, charge_cap, precision in cases:
                base = run(data,sigs,'reference',mode,n,delay,charge_cap,precision)
                candidate = run(data,sigs,'candidate',mode,n,delay,charge_cap,precision)
                pair = dict(baseline=base,candidate=candidate,comparison=compare(base,candidate))
                output['pairs'].append(pair)
                print(label,n,mode,'delay',delay,'cap_cost',charge_cap,'fee',precision,
                      f"{base['trades']} {base['wins']}W/{base['losses']}L ${base['pnl']:.4f}",
                      '->',f"{candidate['trades']} {candidate['wins']}W/{candidate['losses']}L ${candidate['pnl']:.4f}",flush=True)
    manifest = json.loads((P/'manifest.json').read_text())
    assert all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h
               for f,h in manifest['stable_checkpoint_files'].items())
    output['stable_checkpoint_unchanged'] = True
    (P/'results.json').write_text(json.dumps(output,indent=2))


if __name__ == '__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in offline replay')):
        main()
