"""First-settlement audit. Reads only the retained immutable paper snapshot."""
import collections
import datetime as dt
import hashlib
import json
import sqlite3
import statistics
import sys
import zlib
from pathlib import Path

R = Path(__file__).resolve().parent
P = R.parents[1]
sys.path.insert(0, str(P))
from engine import CITIES, SCENARIOS, book, choose, day, digest, fee, payout, ts

cfg = json.loads((P / 'frozen_config.json').read_text())
training = json.loads((P / 'training.json').read_text())
audit = json.loads((R / 'audit.json').read_text())
assert hashlib.sha256((R / 'snapshot.sqlite').read_bytes()).hexdigest() == audit['snapshot_sha256']
db = sqlite3.connect((R / 'snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row
def rows(q, args=()):
    return [dict(r) for r in db.execute(q, args)]
receipts = {r['id']: r for r in rows('select * from receipts')}
meta = {r['key']: json.loads(r['value']) for r in rows('select * from meta')}
assert meta['config_hash'] == digest(cfg)
for name, expected in cfg['frozen_files'].items():
    assert hashlib.sha256((P / name).read_bytes()).hexdigest() == expected, name
def payload(rid):
    h = receipts[rid]['hash']
    raw = zlib.decompress(db.execute('select body from payloads where hash=?', (h,)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest() == h
    return json.loads(raw)
def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
attempts = rows('select * from attempts order by created,id')
positions = rows('select * from positions order by id')
by_attempt = {p['attempt']: p for p in positions}
checks = {a['id']: rows('select * from book_checks where attempt=? order by phase', (a['id'],)) for a in attempts}
verified_decisions = 0
for d in rows('select * from decisions order by id'):
    evidence = json.loads(d['data'])
    books = {b['ticker']: b for b in payload(evidence['postbook_receipt'])['orderbooks']}
    boundary = evidence['volume_boundary']
    volumes = {m['market_ticker']: sum(float(c.get('volume_fp') or 0) for c in m['candlesticks'] if boundary-600 < c['end_period_ts'] <= boundary) for m in payload(evidence['volume_receipt'])['markets']}
    markets = [m for m in payload(evidence['catalogue_receipt'])['markets'] if m['event_ticker'] == d['event']]
    pick, rejected = choose(markets, books, evidence['source'], training, d['created'], volumes, cfg)
    assert pick == evidence['pick'], d['id']
    assert ('eligible' if pick else 'no_edge_or_price') == d['reason'], d['id']
    verified_decisions += 1

execution = []
ends = {}
for a in attempts:
    p = json.loads(a['data'])
    assert p['qty'] == cfg['contracts'] == 5
    assert p['friction'] == cfg['friction'][a['scenario']] * p['qty']
    assert abs(a['reserve'] - (p['qty']*p['cap'] + fee(p['cap'], p['qty']) + p['friction'])) < 1e-9
    result = {'attempt': a['id'], 'decision': a['decision'], 'scenario': a['scenario'], 'ticker': p['ticker'], 'side': p['side'], 'status': a['status'], 'reason': a['reason'], 'original_ask': p['ask'], 'original_cap': p['cap'], 'books': []}
    for b in checks[a['id']]:
        r = receipts[b['receipt']]
        raw = next(x for x in payload(r['id'])['orderbooks'] if x['ticker'] == p['ticker'])
        calc, why = book(raw, p['side'], p['cap'], p['qty'])
        assert why == b['reason'], a['id']
        assert r['status'] == 200 and r['error'] is None
        assert a['due'] <= r['started'] <= r['received'] <= a['due'] + cfg['execution_deadline']
        assert r['duration'] <= cfg['max_http'] and float(json.loads(r['headers']).get('Age') or 0) <= 1
        assert r['received'] < p['close']
        result['books'].append({'phase': b['phase'], 'receipt': r['id'], 'started_utc': utc(r['started']), 'received_utc': utc(r['received']), **calc})
    if a['status'] == 'filled':
        pos = by_attempt[a['id']]
        assert len(checks[a['id']]) == 2
        b0, b1 = checks[a['id']]
        assert receipts[b1['receipt']]['started'] - receipts[b0['receipt']]['received'] >= cfg['second_book_seconds']
        assert pos['price'] == max(p['ask'], *(b['vwap'] for b in result['books'])) <= p['cap']
        assert pos['friction'] == p['friction'] and pos['qty'] == p['qty']
        assert pos['fee'] == fee(pos['price'], pos['qty'])
        assert pos['cost'] <= a['reserve'] + 1e-9
        settlement = receipts[pos['settlement_receipt']]
        market = next(x for x in payload(settlement['id'])['markets'] if x['ticker'] == pos['ticker'])
        assert settlement['received'] == pos['settled'] > pos['entered']
        assert payout(market, pos['side']) * pos['qty'] == pos['payout']
        assert abs(pos['net'] - (pos['payout'] - pos['price']*pos['qty'] - pos['fee'] - pos['friction'])) < 1e-9
        result['trade'] = pos
        result['settlement_result'] = market.get('result')
        result['decision_to_fill_seconds'] = pos['entered'] - a['created']
        result['source_first_seen_to_fill_seconds'] = pos['entered'] - p['source']['first_seen']
        ends[a['id']] = pos['entered']
    elif a['status'] == 'unfilled':
        if checks[a['id']]:
            assert checks[a['id']][-1]['reason'] == a['reason']
            ends[a['id']] = checks[a['id']][-1]['received']
        else:
            assert a['reason'] == 'closed_before_execution' and a['scenario'] == 'canonical'
            pub = rows('select * from publications where series=? and source=? order by received,id limit 1', (a['series'], p['source']['source_t']))[0]
            assert a['due'] is None and pub['received'] >= p['close']
            result['publication_after_close_seconds'] = pub['received'] - p['close']
            ends[a['id']] = p['close']
    elif a['status'] == 'blocked':
        pending = [old for old in attempts if old['id'] < a['id'] and old['scenario'] == a['scenario'] and old['series'] == a['series'] and old['status'] != 'blocked' and old['created'] <= a['created'] < ends[old['id']]]
        assert a['reason'] == 'owned_or_city_pending' and pending
        result['pending_owner_attempts'] = [x['id'] for x in pending]
    execution.append(result)

# Reconstruct the obligations immediately before each accepted attempt. Receipt
# completion times bound release; no pending execution was close to a risk cap.
risk = []
for a in attempts:
    if a['status'] == 'blocked':
        continue
    pending = [old for old in attempts if old['id'] < a['id'] and old['scenario'] == a['scenario'] and old['status'] != 'blocked' and old['created'] <= a['created'] < ends[old['id']]]
    entered = [p for p in positions if p['scenario'] == a['scenario'] and p['entered'] < a['created']]
    active = [p for p in entered if p['settled'] > a['created']]
    settled = [p for p in entered if p['settled'] <= a['created']]
    committed = sum(x['reserve'] for x in pending) + sum(x['cost'] for x in active) + a['reserve']
    contracts = 5*(len(pending)+len(active)+1)
    daily = sum(p['net'] for p in settled if day(p['settled']) == day(a['created']))
    cash = cfg['bankroll'] - sum(p['cost'] for p in entered) + sum(p['payout'] for p in settled)
    free = cash - sum(x['reserve'] for x in pending) - a['reserve']
    assert contracts <= cfg['max_contracts'] and committed <= cfg['max_exposure']
    assert daily - committed >= -cfg['daily_budget'] and free >= cfg['min_cash']
    risk.append({'attempt': a['id'], 'contracts_after_reservation': contracts, 'committed_after_reservation': committed, 'free_cash_after_reservation': free})

paired = []
for signal in audit['qualified_signals']:
    aa = [a for a in attempts if a['decision'] == signal['decision_id']]
    series = aa[0]['series']
    p = json.loads(aa[0]['data'])
    source = p['source']
    pub = rows('select * from publications where series=? and source=? order by received,id limit 1', (series, source['source_t']))[0]
    first_r = receipts[source['first_seen_receipt']]
    pub_r = receipts[pub['receipt']]
    same_url = [r for r in receipts.values() if r['url'] == pub_r['url'] and r['status'] == 200 and r['received'] < pub_r['received']]
    previous_publication_poll = max(same_url, key=lambda r: r['received'])
    old = next((x for x in payload(previous_publication_poll['id'])['timeseries'] if x['t']/1000 == source['source_t']), None)
    assert old is None or old.get('v') is None
    first_data = json.loads(rows('select data from sources where series=? and source=? order by received,id limit 1', (series,source['source_t']))[0]['data'])
    prior_source_poll = first_data.get('previous_poll') or {}
    # These are observation intervals, not independently verified vendor clocks.
    bounds = {'source_observation_interval_utc': [utc(prior_source_poll['started']), utc(first_r['received'])], 'canonical_observation_interval_utc': [utc(previous_publication_poll['started']) if old else None, utc(pub_r['received'])], 'previous_canonical_poll_has_source_minute': old is not None, 'observed_receipt_lead_seconds': pub_r['received']-first_r['received'], 'lead_interval_seconds': [previous_publication_poll['started']-first_r['received'] if old else None, pub_r['received']-prior_source_poll['started']], 'bound_limitation': 'Previous response omitted the source minute; no continuous publication lower bound.' if old is None else 'Bounds are polling observations; publication or correction between polls cannot be resolved.'}
    paired.append({**signal, **bounds, 'all_scenarios_attempted': all(a['status']!='blocked' for a in aa), 'outcomes': [{'scenario':a['scenario'],'attempt':a['id'],'status':a['status'],'net':by_attempt[a['id']]['net'] if a['id'] in by_attempt else 0} for a in aa]})

polling = {}
for series, city in CITIES.items():
    polls = sorted([r for r in receipts.values() if f'/live_data/weather/{city}?' in r['url']], key=lambda r:r['received'])
    gaps = [{'before_receipt':x['id'],'after_receipt':y['id'],'seconds':y['received']-x['received']} for x,y in zip(polls,polls[1:]) if y['received']-x['received']>cfg['max_source_poll_gap']]
    polling[series] = {'polls':len(polls),'last_poll_utc':utc(polls[-1]['received']),'gaps_over_45_seconds':gaps}
earlier = json.loads((R.parent/'2026_10_13_124611'/'operations_audit.json').read_text())
seen = set()
gap_censoring = []
for row in rows('select * from sources order by received,id'):
    source_data = json.loads(row['data'])
    key = (row['series'], row['source'])
    if key in seen:
        continue
    seen.add(key)
    prior = source_data.get('previous_poll')
    if prior and row['received'] - prior['received'] > cfg['max_source_poll_gap']:
        assert row['censored'] == 1
        gap_censoring.append({'id':row['id'],'series':row['series'],'gap_seconds':row['received']-prior['received'],'censored':True})
out = {'captured_utc':audit['captured_utc'],'snapshot_sha256':audit['snapshot_sha256'],'all_decisions_replayed':verified_decisions,'attempts_verified':execution,'admission_risk_verified':risk,'same_signal_scenario_comparisons':paired,'source_polling':polling,'new_minutes_after_long_poll_gap_verified_censored':gap_censoring,'chicago_complete_source_gaps':earlier['chicago_complete_source_gaps'],'fee_configurations':{k:{'valid':v['valid'],'receipt':v['receipt'],'fee_type':v['data']['fee_type'],'fee_multiplier':v['data']['fee_multiplier']} for k,v in meta.items() if k.startswith('fee_')},'changes':'No strategy, observer, configuration, ledger or existing bot changes. Review artifacts only.','limits':['All executions are simulations, not actual orders.','NYC delayed books improved to 45c/42c, but frozen worst-of-signal pricing charges 61c; headline scenario P&L does not measure the full advantage of better later quotes.','A later LA source reattempt was blocked in both comparison scenarios by the previous pending attempt; do not treat it as a fully paired comparison.','No estimate of missed profitable opportunities during missing-station or polling gaps is available.','Positive net from three settlements is not evidence of a repeatable edge.']}
(R/'milestone_audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'decisions_verified':verified_decisions,'attempts_verified':len(execution),'fully_paired_signals':sum(x['all_scenarios_attempted'] for x in paired),'max_reserved_contracts':max(x['contracts_after_reservation'] for x in risk),'max_reserved_cost':max(x['committed_after_reservation'] for x in risk),'polling':polling},indent=2))
