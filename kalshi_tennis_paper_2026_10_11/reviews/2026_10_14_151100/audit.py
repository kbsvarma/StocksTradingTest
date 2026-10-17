"""Read-only receipt audit of the immutable 2026-10-12 18:46 paper snapshot.

Run with Python 3 from any directory. No network, order transport, or live DB.
"""
import collections
import datetime as dt
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import urlparse, parse_qs
import zlib

from engine import plays, probability, choose, book_fill, payout, digest, point_key, ts, quote

P = Path(__file__).resolve().parent
cfg = json.loads((P / 'frozen_config.json').read_text())
capture = json.loads((P / 'capture.json').read_text())
now = capture['captured_ts']
db = sqlite3.connect((P / 'paper_snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row


def rows(sql, args=()):
    return [dict(x) for x in db.execute(sql, args)]


def body(h):
    raw = zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?', (h,)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest() == h
    return json.loads(raw)


def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def quantiles(values):
    a = sorted(values)
    return dict(n=len(a), **{str(q): a[int(q * (len(a) - 1))] if a else None for q in (.5, .9, .95, .99, 1)})


def money(x):
    return Decimal(str(x))


def equivalent(a, b):
    # Python/platform floating-point summation may differ in the last bit.
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    if isinstance(a, (float, int)) and isinstance(b, (float, int)):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


receipts = {r['id']: r for r in rows('SELECT * FROM receipts')}
events = {r['event']: r for r in rows('SELECT * FROM events')}
attempts = {r['id']: r for r in rows('SELECT * FROM attempts')}
decisions = rows('SELECT * FROM decisions ORDER BY boundary,event')
positions = rows('SELECT * FROM positions ORDER BY settled,entered')
meta = {r['key']: json.loads(r['value']) for r in rows('SELECT * FROM meta')}
report = {'captured_utc': capture['captured_utc'], 'snapshot_sha256': hashlib.sha256((P / 'paper_snapshot.sqlite').read_bytes()).hexdigest(),
          'integrity': db.execute('PRAGMA integrity_check').fetchone()[0], 'issues': []}
assert report['snapshot_sha256'] == capture['snapshot_sha256']
assert digest(cfg) == meta['config_hash']
assert hashlib.sha256((P / 'model.py').read_bytes()).hexdigest() == cfg['model_sha256']
report['health'] = {'service': capture['service'], 'clock': capture['clock'], 'heartbeat_age_seconds': now - meta['heartbeat'],
                    'catalogue_age_seconds': now - meta['catalogue_last_success'], 'elapsed_hours': (now - meta['started']) / 3600,
                    'config_hash': meta['config_hash']}

# Independently recompute accounting with decimal arithmetic and receipt outcomes.
report['portfolios'] = {}
for scenario in ('base', 'stress'):
    pp = [p for p in positions if p['scenario'] == scenario]
    settled = [p for p in pp if p['settled'] is not None]
    aa = [a for a in attempts.values() if a['scenario'] == scenario]
    net = sum((money(p['net']) for p in settled), Decimal(0))
    equity = peak = dd = Decimal(0)
    for p in settled:
        equity += money(p['net']); peak = max(peak, equity); dd = max(dd, peak - equity)
    by_series = {}
    for series in cfg['series']:
        ss = [p for p in settled if events[p['event']]['series'] == series]
        by_series[series] = {'settled': len(ss), 'wins': sum(p['payout'] == 1 for p in ss), 'losses': sum(p['payout'] == 0 for p in ss),
                             'net': float(sum((money(p['net']) for p in ss), Decimal(0)))}
    report['portfolios'][scenario] = {'attempted': len(aa), 'filled': sum(a['status'] == 'filled' for a in aa), 'settled': len(settled),
        'open': len(pp) - len(settled), 'pending': sum(a['status'] == 'pending' for a in aa), 'wins': sum(p['payout'] == 1 for p in settled),
        'losses': sum(p['payout'] == 0 for p in settled), 'net': float(net), 'drawdown': float(dd),
        'fees': float(sum((money(p['fee']) for p in settled), Decimal(0))), 'friction': float(sum((money(p['friction']) for p in settled), Decimal(0))),
        'by_series': by_series, 'unfilled': dict(collections.Counter(a['reason'] for a in aa if a['status'] == 'unfilled'))}

# Rerun each eligible decision using only the originally received inputs.
report['eligible_decisions'] = []
for d in decisions:
    if d['reason'] != 'eligible':
        continue
    data = json.loads(d['data']); prior = data['prior']; snap = data['snapshot']; pick = data['pick']
    assert snap['received'] <= d['boundary']
    assert 0 <= d['boundary'] - snap['received'] <= cfg['max_score_poll_gap']
    es = plays(body(snap['hash']))
    assert prior['received'] < min(float(e['wall_clock']) for e in es)
    pr = receipts[prior['receipt']]
    assert pr['received'] == prior['received'] and prior['target_t']+2 <= pr['received'] <= prior['target_t']+20
    assert prior['target_t'] == int((prior['scheduled_start']-prior['minutes']*60)//60)*60 and prior['minutes'] in (5,30)
    prior_quotes = [quote(c) for m in body(pr['hash'])['markets'] for c in m['candlesticks'] if c['end_period_ts'] == prior['target_t']]
    assert any(q and q[1]-q[0] <= .1 and .02 < sum(q)/2 < .98 and
               (math.isclose(sum(q)/2, prior['probability']) or math.isclose(1-sum(q)/2, prior['probability'])) for q in prior_quotes)
    state, reason = probability(es, prior, d['boundary'], cfg)
    assert reason is None and state == data['probability']
    cr = receipts[data['candle_receipt']]
    candles = {m['market_ticker']: {int(c['end_period_ts']): c for c in m['candlesticks']} for m in body(cr['hash'])['markets']}
    markets = json.loads(events[d['event']]['info'])['markets']
    recomputed, reason = choose(markets, candles, state, prior, d['boundary'], pick['mult'], cfg)
    assert reason is None and equivalent(recomputed, pick)
    assert d['received'] <= d['boundary'] + cfg['decision_deadline'] and cr['received'] <= d['received']
    report['eligible_decisions'].append({'event': d['event'], 'boundary': d['boundary'], 'decision_lag_seconds': d['received'] - d['boundary'],
                                       'score_age_seconds': d['boundary'] - float(state['source_t']), 'edge': pick['edge']})

report['position_checks'] = []
for p in positions:
    a = attempts[p['attempt']]; pick = json.loads(a['data'])
    d = next(d for d in decisions if d['event'] == a['event'] and d['boundary'] == a['boundary'])
    prior = json.loads(d['data'])['prior']
    assert abs(a['due'] - a['created'] - cfg['book_delays'][a['scenario']]) < 1e-6
    checks = rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase', (a['id'],))
    assert len(checks) == 2 and [b['phase'] for b in checks] == [0, 1]
    bs = []
    for b in checks:
        r = receipts[b['receipt']]
        result, reason = book_fill(body(r['hash']), pick['side'], pick['cap'])
        assert reason is None and b['reason'] is None and result == json.loads(b['data'])
        assert a['due'] <= r['started'] <= r['received'] <= a['due'] + cfg['book_timeout']
        assert r['duration'] <= cfg['max_http_seconds']
        age = json.loads(r['headers']).get('Age'); assert age is None or float(age) <= 1
        bs.append({**result, 'request_started': r['started'], 'received': r['received']})
    assert bs[1]['request_started'] - bs[0]['received'] >= cfg['second_book_delay']
    market_checks = [r for r in receipts.values() if urlparse(r['url']).path.endswith('/markets/'+p['ticker'])
                     and a['due'] <= r['started'] <= r['received'] <= bs[0]['request_started'] and not r['error']]
    assert market_checks
    mr = max(market_checks, key=lambda r:r['received']); market = body(mr['hash'])['market']
    assert market['status'] == 'active' and not market.get('settlement_ts') and ts(market['close_time']) > mr['received']
    assert p['price'] == max(pick['ask'], *(b['vwap'] for b in bs))
    fee = (Decimal('.07') * money(pick['mult']) * money(p['price']) * (1-money(p['price']))).quantize(Decimal('.0001'), rounding=ROUND_CEILING)
    assert fee == money(p['fee'])
    assert abs(money(p['cost']) - money(p['price']) - fee - money(p['friction'])) < Decimal('0.000000000001')
    settlement = {}
    if p['settled'] is not None:
        settlement = body(receipts[p['settlement_receipt']]['hash'])['market']
        assert payout(settlement, p['side']) == p['payout']
        assert abs(money(p['net']) - money(p['payout']) + money(p['cost'])) < Decimal('0.000000000001')
    chosen = next(e for e in plays(body(json.loads(d['data'])['snapshot']['hash']))
                  if float(e['wall_clock']) == float(pick['source_t']) and e['event_type'] == 'point')
    revisions = rows('SELECT * FROM points WHERE event=? AND point_key=? AND first_seen>?', (p['event'], point_key(chosen), d['received']))
    report['position_checks'].append({'event': p['event'], 'scenario': p['scenario'], 'ticker': p['ticker'], 'side': p['side'],
        'entered_utc': utc(p['entered']), 'price': p['price'], 'fee': p['fee'], 'friction': p['friction'], 'net': p['net'],
        'settlement_result': settlement.get('result'), 'settlement_receipt': p['settlement_receipt'],
        'decision_to_first_book_seconds': bs[0]['received'] - a['created'], 'decision_to_fill_seconds': p['entered'] - a['created'],
        'book_spacing_seconds': bs[1]['request_started'] - bs[0]['received'], 'minimum_depth_inside_cap': min(b['available'] for b in bs),
        'score_age_seconds': a['boundary'] - float(pick['source_t']), 'later_chosen_point_versions': len(revisions),
        'prior_minutes': prior['minutes'], 'prior_received_utc': utc(prior['received'])})

# Recovered HTTP failures: compare exact URLs and original deadline, not a later
# historical candle fetch. Failed receipts remain in the record.
http_failures = [r for r in receipts.values() if r['error'] or r['status'] != 200]
report['http'] = {'requests': len(receipts), 'failures': len(http_failures), 'durations_seconds': quantiles([r['duration'] for r in receipts.values()]), 'recoveries': []}
for r in http_failures:
    q = parse_qs(urlparse(r['url']).query); boundary = int(q['end_ts'][0]) if 'end_ts' in q else None
    following = [rr for rr in receipts.values() if rr['url'] == r['url'] and rr['id'] > r['id'] and rr['status'] == 200 and not rr['error']]
    good = min(following, key=lambda rr: rr['received']) if following else None
    event = q['market_tickers'][0].split(',')[0].rsplit('-', 1)[0] if 'market_tickers' in q else urlparse(r['url']).path
    dd = next((d for d in decisions if d['event'] == event and d['boundary'] == boundary), None)
    report['http']['recoveries'].append({'receipt': r['id'], 'event': event, 'boundary_utc': utc(boundary) if boundary else None, 'status': r['status'],
        'recovery_receipt': good['id'] if good else None, 'recovered_within_20s': bool(good and good['received'] <= boundary+20) if boundary else None,
        'seconds_from_failure_to_recovery': good['received']-r['received'] if good else None,
        'recovery_lag_from_boundary': good['received']-boundary if good and boundary else None, 'decision_reason': dd['reason'] if dd else 'prematch_prior_or_absent'})

# Audit failed paper execution against its actual later book, including cap
# rejection and whether a later independently qualified attempt filled.
report['unfilled_attempts'] = []
for a in attempts.values():
    if a['status'] != 'unfilled': continue
    pick = json.loads(a['data'])
    checks = rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase', (a['id'],))
    assert checks
    b = checks[-1]; r = receipts[b['receipt']]; raw = body(r['hash'])
    fill, reason = book_fill(raw, pick['side'], pick['cap'])
    assert fill is None and reason == b['reason'] == a['reason']
    asks = sorted((round(1-float(price),4),float(qty)) for price,qty in
                  raw['orderbook_fp'][('no' if pick['side']=='yes' else 'yes')+'_dollars'] if float(qty)>0)
    later = next((p for p in positions if p['scenario']==a['scenario'] and p['event']==a['event']), None)
    normal = next((p for p in positions if p['scenario']=='base' and p['event']==a['event']), None)
    report['unfilled_attempts'].append({'attempt': a['id'], 'event': a['event'], 'scenario':a['scenario'], 'side':pick['side'],
       'ticker':pick['ticker'], 'reason':reason, 'cap':pick['cap'], 'signal_ask':pick['ask'],
       'book_receipt':r['id'], 'best_subsequent_ask':asks[0][0] if asks else None,
       'available_inside_cap':sum(q for price,q in asks if price<=pick['cap']+1e-8),
       'later_filled_attempt':later['attempt'] if later else None, 'later_filled_side':later['side'] if later else None,
       'later_filled_ticker':later['ticker'] if later else None, 'normal_net':normal['net'] if normal else None})

points = rows('SELECT * FROM points')
clean = [p for p in points if not p['censored'] and not p['revision'] and p['upper_delay'] >= 0]
report['feed'] = {'point_versions': len(points), 'revised_versions': sum(p['revision'] for p in points),
                  'censored_versions': sum(p['censored'] for p in points), 'uncensored_first_arrival_seconds': quantiles([p['upper_delay'] for p in clean]),
                  'poll_interval_uncertainty_seconds': cfg['score_poll_seconds'], 'clean_arrivals_over_90s': sum(p['upper_delay'] > 90 for p in clean)}

# Reconstruct the schedule known at each poll from its actual milestone receipts.
schedule = collections.defaultdict(list)
for r in receipts.values():
    if urlparse(r['url']).path.endswith('/milestones') and not r['error']:
        for m in body(r['hash']).get('milestones', []):
            event = m.get('details', {}).get('main_game_event_ticker')
            if event in events:
                start = dt.datetime.fromisoformat(m['start_date'].replace('Z', '+00:00')).timestamp()
                schedule[event].append((r['received'], start))
poll_gaps = []; ordinary_gaps = []; event_audit = []
for event, e in events.items():
    snaps = rows('SELECT * FROM snapshots WHERE event=? ORDER BY received', (event,))
    first_point = min((p['source'] for p in points if p['event'] == event), default=None)
    prior = json.loads(e['prior']) if e['prior'] else None
    gap_details = []
    for prev, cur in zip(snaps, snaps[1:]):
        gap = cur['received'] - prev['received']
        if gap <= cfg['max_score_poll_gap']:
            ordinary_gaps.append(gap); continue
        schedules = [(t, s) for t, s in schedule[event] if prev['received'] <= t <= cur['received']]
        prior_schedule = [(t, s) for t, s in schedule[event] if t <= prev['received']]
        if prior_schedule: schedules.insert(0, prior_schedule[-1])
        rescheduled_outside_active = any(t < s-120 for t, s in schedules)
        detail = {'event': event, 'gap_seconds': gap, 'from_utc': utc(prev['received']), 'to_utc': utc(cur['received']),
                  'schedule_moved_outside_poll_window': rescheduled_outside_active,
                  'before_first_observed_point': first_point is None or cur['received'] < first_point}
        poll_gaps.append(detail); gap_details.append(detail)
    event_audit.append({'event': event, 'series': e['series'], 'done': e['done'], 'scheduled_start_utc': utc(e['start']),
        'snapshots': len(snaps), 'first_point_utc': utc(first_point) if first_point else None,
        'prior_validated': bool(prior and prior.get('validated')), 'prior_invalid': prior.get('invalid') if prior else None,
        'prior_received_before_first_point': bool(first_point and prior and prior['received'] < first_point),
        'latest_poll_age_seconds': now-e['last_poll'] if e['last_poll'] else None,
        'is_currently_scheduled_active': not e['done'] and e['start']-120 <= now <= e['start']+43200,
        'schedule_changes': sum(a[1] != b[1] for a,b in zip(schedule[event], schedule[event][1:]))})
report['feed']['ordinary_poll_gaps_seconds'] = quantiles(ordinary_gaps)
report['feed']['gaps_over_30s'] = poll_gaps
report['events'] = event_audit
point_events = [e for e in event_audit if e['first_point_utc']]
report['prematch_coverage'] = {'matches_with_observed_points': len(point_events),
    'valid_priors': sum(e['prior_validated'] for e in point_events),
    'missing_priors': [e['event'] for e in point_events if not e['prior_validated']],
    'known_limitations': {
        'KXWTAMATCH-26OCT11SAKVEK': 'Earlier due candles lacked usable quotes; a prior was captured 07:05:07 UTC and cleared after the 07:07:59 schedule move. Final move to 07:15 was observed 07:13:10 after the new 07:10 fallback window. No retrospective recapture.',
        'KXWTAMATCH-26OCT11PARTAR': 'Markets opened 03:06 UTC, after scheduled start 03:00 and both frozen prematch capture windows; discovered 03:07:10 UTC, before first point 03:08:47. No late input substituted.',
        'KXWTAMATCH-26OCT11TJEITO': 'Repeated schedule changes reset captured priors before score polling began. Final move from 04:50 to 04:45 was observed 04:42:51 UTC, after the new 04:40 fallback capture window. No retrospective recapture or backfilled trade.'}}
report['decisions'] = dict(collections.Counter(d['reason'] for d in decisions))
report['eligible_unique_matches'] = len(set(d['event'] for d in decisions if d['reason'] == 'eligible'))
report['health_event_counts'] = dict(collections.Counter(r['kind'] for r in rows('SELECT kind FROM health')))
report['milestones'] = {'normal_settled': report['portfolios']['base']['settled'], 'preliminary_20_reached': report['portfolios']['base']['settled'] >= 20,
    'main_14day_100_reached': now-meta['started'] >= 14*86400 and report['portfolios']['base']['settled'] >= 100,
    'entry_cutoff_utc': utc(meta['started']+28*86400)}
report['issues'] += [{'kind':'http_not_recovered_within_decision_deadline','detail':x} for x in report['http']['recoveries'] if x['recovered_within_20s'] is False or x['recovery_receipt'] is None]
report['issues'] += [{'kind':'unexplained_poll_gap','detail':x} for x in poll_gaps if not (x['schedule_moved_outside_poll_window'] and x['before_first_observed_point'])]
report['later_selected_point_corrections'] = [p for p in report['position_checks'] if p['later_chosen_point_versions']]
(P / 'audit.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('events','position_checks','eligible_decisions')}, indent=2))
