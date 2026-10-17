"""Audit immutable paper receipts, never the active observer or database."""
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib
from zoneinfo import ZoneInfo

from engine import plays, point_key, probability

P = Path(__file__).resolve().parent
db = sqlite3.connect((P / 'paper_snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row
audit = json.loads((P / 'audit.json').read_text())
capture = json.loads((P / 'capture.json').read_text())
previous = json.loads((P / 'prior_review_state.json' if (P / 'prior_review_state.json').exists() else P / 'review_state.json').read_text())
cutoff = dt.datetime.fromisoformat(previous['last_checked_utc']).timestamp()
cache = {}

def body(h):
    if h not in cache:
        raw = zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?', (h,)).fetchone()[0])
        assert hashlib.sha256(raw).hexdigest() == h
        cache[h] = json.loads(raw)
    return cache[h]

def rows(sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]

def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()

removed = []
empty_after_points = []
empty_pending = {}
suspect_keys = set()
last = {}
for s in rows('SELECT * FROM snapshots ORDER BY event,received,id'):
    payload = body(s['hash'])
    es = {point_key(e): e for e in plays(payload)}
    prev = last.get(s['event'], {})
    if not payload and prev:
        entry = empty_pending.setdefault(s['event'], {'event': s['event'], 'first_empty_received': s['received'],
            'receipts': [], 'previous_keys': set(prev)})
        entry['receipts'].append(s['receipt'])
        continue
    if es and s['event'] in empty_pending:
        entry = empty_pending.pop(s['event'])
        oldkeys = entry.pop('previous_keys')
        entry.update(recovered_receipt=s['receipt'], recovered_received=s['received'],
                     recovery_seconds=s['received']-entry['first_empty_received'],
                     previous_key_count=len(oldkeys), recovered_previous_keys=len(oldkeys & es.keys()))
        empty_after_points.append(entry)
    missing = prev.keys() - es.keys()
    if missing:
        added = es.keys() - prev.keys()
        removed.append({'event': s['event'], 'receipt': s['receipt'], 'received_utc': utc(s['received']),
                        'removed': [prev[k] for k in missing], 'added': [es[k] for k in added]})
        suspect_keys.update((s['event'], k) for k in missing | added)
    last[s['event']] = es

selected_suspects = []
ownership = []
for d in rows("SELECT * FROM decisions WHERE reason='eligible' ORDER BY boundary,event"):
    data = json.loads(d['data'])
    chosen = next(e for e in plays(body(data['snapshot']['hash']))
                  if float(e['wall_clock']) == float(data['pick']['source_t']) and e['event_type'] == 'point')
    if (d['event'], point_key(chosen)) in suspect_keys:
        selected_suspects.append({'event': d['event'], 'boundary': d['boundary']})
    for scenario in ('base', 'stress'):
        attempt = rows('SELECT id,status,reason FROM attempts WHERE event=? AND boundary=? AND scenario=?',
                       (d['event'], d['boundary'], scenario))
        # A later qualified minute is correctly skipped after this scenario
        # already owns the match or has an earlier still-pending attempt.
        owned = rows('SELECT attempt FROM positions WHERE event=? AND scenario=? AND entered<=?',
                     (d['event'], scenario, d['received']))
        pending = rows('''SELECT a.id FROM attempts a LEFT JOIN positions p ON p.attempt=a.id
                          WHERE a.event=? AND a.scenario=? AND a.boundary<? AND a.created<=?
                          AND (a.due>=? OR p.entered>? OR
                               (SELECT MAX(received) FROM book_checks WHERE attempt=a.id)>?)''',
                       (d['event'], scenario, d['boundary'], d['received'], d['received'], d['received'], d['received']))
        skip_receipt = rows("SELECT at,detail FROM health WHERE event=? AND kind='portfolio_skip' AND detail=? AND at BETWEEN ? AND ?",
                            (d['event'], scenario+':owned_or_pending', d['received'], d['received']+1))
        assert attempt or owned or pending, (d['event'], d['boundary'], scenario)
        assert attempt or skip_receipt
        ownership.append({'event': d['event'], 'boundary': d['boundary'], 'scenario': scenario,
                          'attempt': attempt, 'already_owned': owned, 'earlier_pending': pending, 'skip_receipt': skip_receipt})

points = rows('SELECT * FROM points')
clean = [p['upper_delay'] for p in points if not p['censored'] and not p['revision']
         and p['upper_delay'] >= 0 and (p['event'], p['point_key']) not in suspect_keys]
clean.sort()
priors = {}
for event in audit['prematch_coverage']['missing_priors']:
    priors[event] = {
        'history': rows("SELECT at,kind,detail FROM health WHERE event=? AND kind IN ('prior_captured','schedule_changed') ORDER BY at", (event,)),
        'first_point': rows('SELECT MIN(source) source, MIN(first_seen) received FROM points WHERE event=?', (event,))[0],
        'decision_reasons': rows('SELECT reason,COUNT(*) n FROM decisions WHERE event=? GROUP BY reason', (event,))}

et = ZoneInfo('America/New_York')
started = json.loads(db.execute("SELECT value FROM meta WHERE key='started'").fetchone()[0])
dates = (dt.datetime.fromtimestamp(capture['captured_ts'], et).date() - dt.datetime.fromtimestamp(started, et).date()).days + 1
daily = {}
for scenario in ('base', 'stress'):
    p = rows('SELECT * FROM positions WHERE scenario=? ORDER BY entered', (scenario,))
    counts = collections.Counter(dt.datetime.fromtimestamp(x['entered'], et).date().isoformat() for x in p)
    daily[scenario] = {'entries_by_et_date': dict(counts), 'observed_calendar_dates_including_partial': dates,
                       'entries_per_observed_calendar_date': len(p)/dates,
                       'entries_per_elapsed_24h': len(p)/((capture['captured_ts']-started)/86400),
                       'limitation': 'First and current dates may be partial; no established daily frequency. Status field trades_per_calendar_day is elapsed-24h normalization.'}

result = {
    'captured_utc': capture['captured_utc'],
    'previous_review_utc': previous['last_checked_utc'],
    'point_key_removals': removed,
    'empty_200_payload_episodes_after_points': empty_after_points,
    'eligible_selected_keys_in_removal_or_replacement_set': selected_suspects,
    'latency_excluding_observed_key_replacements': {'n': len(clean), 'p50': clean[int(.5*(len(clean)-1))],
                                                 'p95': clean[int(.95*(len(clean)-1))], 'max': max(clean)},
    'eligible_scenario_ownership_checks': ownership,
    'missing_prior_evidence': priors,
    'calendar_frequency': daily,
    'new_health': rows('SELECT * FROM health WHERE at>? ORDER BY at', (cutoff,)),
    'new_http_failures': rows('SELECT id,received,status,error FROM receipts WHERE received>? AND (error IS NOT NULL OR status!=200)', (cutoff,)),
    'new_attempts': rows('SELECT * FROM attempts WHERE created>?', (cutoff,)),
    'new_settlements': rows('SELECT event,scenario,settled,payout,net,settlement_receipt FROM positions WHERE settled>?', (cutoff,)),
    'upcoming': rows('SELECT event,start,prior,last_poll FROM events WHERE done=0 ORDER BY start')}
cfg = json.loads((P / 'frozen_config.json').read_text())
impact = []
for episode in empty_after_points:
    for d in rows('SELECT * FROM decisions WHERE event=? AND boundary>=? AND boundary<?',
                  (episode['event'], episode['first_empty_received'], episode['recovered_received'])):
        data = json.loads(d['data'])
        prev = rows('SELECT * FROM snapshots WHERE event=? AND received<? ORDER BY received DESC LIMIT 1',
                    (episode['event'], episode['first_empty_received']))[0]
        item = {'event': d['event'], 'boundary': d['boundary'], 'actual_reason': d['reason'],
                'actual_snapshot': data.get('snapshot'), 'prior_valid_snapshot_age': d['boundary']-prev['received']}
        if data.get('prior'):
            state, reason = probability(plays(body(prev['hash'])), data['prior'], d['boundary'], cfg)
            item.update(diagnostic_last_nonempty_state=state, diagnostic_last_nonempty_reason=reason)
        item['limitation'] = 'Diagnostic only: no entry or PnL imputed; retaining older snapshots would need an explicit frozen-protocol review.'
        impact.append(item)
result['empty_payload_decision_impact'] = impact
assert not selected_suspects
(P / 'supplemental_audit.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({'removal_events': len(removed), 'ownership_checks': len(ownership),
                  'new_http_failures': len(result['new_http_failures']), 'new_attempts': len(result['new_attempts']),
                  'new_settlements': result['new_settlements'], 'latency': result['latency_excluding_observed_key_replacements'],
                  'frequency': daily, 'upcoming_events': len(result['upcoming'])}, indent=2))
