"""Read-only analysis of this review's consistent paper snapshot."""
from pathlib import Path
import collections
import datetime as dt
import json
import sqlite3
import statistics
import urllib.request

P = Path(__file__).resolve().parent
db = sqlite3.connect((P / 'paper_snapshot.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
db.row_factory = sqlite3.Row
def rows(q, args=()):
    return [dict(x) for x in db.execute(q, args)]
def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()
a = json.loads((P/'audit.json').read_text())
s = json.loads((P/'supplemental_audit.json').read_text())
c = json.loads((P/'capture.json').read_text())
prev = json.loads((P/'prior_review_state.json').read_text())
cutoff = dt.datetime.fromisoformat(prev['last_checked_utc']).timestamp()
cfg = json.loads((P/'frozen_config.json').read_text())
positions = rows('SELECT * FROM positions ORDER BY entered,id')
attempts = {x['id']: x for x in rows('SELECT * FROM attempts')}
result = {'captured_utc': c['captured_utc'], 'paper_only': True}
assert not c['deployment_mismatches']
gap_review=json.loads((P/'schedule_gap_review.json').read_text())
assert a['issues']==gap_review['explained_issues'] and not gap_review['unresolved_issues']
result['schedule_gap_review']='schedule_gap_review.json'
result['conservative_continuous_poll_latency']=gap_review['conservative_continuous_poll_latency']
assert not a['later_selected_point_corrections']
assert not s['eligible_selected_keys_in_removal_or_replacement_set']
for p in positions:
    assert abs(p['friction']-cfg['base_friction' if p['scenario']=='base' else 'stress_friction'])<1e-12
result['execution'] = {}
for scenario in ['base','stress']:
    aa = [x for x in attempts.values() if x['scenario']==scenario]
    checks = [x for x in a['position_checks'] if x['scenario']==scenario]
    result['execution'][scenario] = {
        'attempted_matches':len({x['event'] for x in aa}),
        'attempts':len(aa), 'filled_matches':len(checks),
        'decision_to_fill_median_seconds':statistics.median(x['decision_to_fill_seconds'] for x in checks),
        'decision_to_fill_max_seconds':max(x['decision_to_fill_seconds'] for x in checks),
        'minimum_depth_in_passing_books':min(x['minimum_depth_inside_cap'] for x in checks)}
pairs = []
normal_only = []
for p in [x for x in positions if x['scenario']=='base']:
    other = next((x for x in positions if x['scenario']=='stress' and x['event']==p['event']),None)
    if other is None:
        normal_only.append({'event':p['event'],'net':p['net']})
        continue
    pairs.append({'event':p['event'], 'same_decision':attempts[p['attempt']]['boundary']==attempts[other['attempt']]['boundary'],
                  'both_settled':p['settled'] is not None and other['settled'] is not None,
                  'normal_net':p['net'],'delayed_net':other['net']})
result['paired_matches'] = pairs
result['normal_only'] = normal_only
result['pair_summary'] = {}
for same in [True,False]:
    pp = [x for x in pairs if x['same_decision']==same and x['both_settled']]
    result['pair_summary']['same_decision' if same else 'different_decision'] = {
        'settled_matches':len(pp), 'normal_net':sum(x['normal_net'] for x in pp),
        'delayed_net':sum(x['delayed_net'] for x in pp)}
ids = {x['id'] for x in s['new_http_failures']}
recoveries = [x for x in a['http']['recoveries'] if x['receipt'] in ids]
assert all(x['recovery_receipt'] for x in recoveries)
assert all(x['recovered_within_20s'] for x in recoveries if x['boundary_utc'])
result['new_http_recoveries'] = recoveries
result['http_failure_types'] = dict(collections.Counter('candles' if x['boundary_utc'] else 'score' if 'game_stats' in x['event'] else 'catalogue' for x in recoveries))
result['new_empty_payload_episodes'] = [x for x in s['empty_200_payload_episodes_after_points'] if x['first_empty_received']>cutoff]
result['new_candle_publication_gaps'] = [x for x in s['new_health'] if x['kind']=='candle_not_published']
old_audit = json.loads((P.parent.parent / prev['last_artifact']).read_text())
new_missing = set(a['prematch_coverage']['missing_priors'])-set(old_audit['prematch_coverage']['missing_priors'])
result['new_missing_prior_evidence'] = {}
for event in sorted(new_missing):
    evidence=s['missing_prior_evidence'][event]
    changes=[x for x in evidence['history'] if x['kind']=='schedule_changed']
    last=changes[-1]
    old_start,new_start=json.loads(last['detail'])
    fallback=int((new_start-300)//60)*60
    assert last['at']>fallback+20
    assert not next((x for x in evidence['history'] if x['kind']=='prior_captured' and x['at']>last['at']),None)
    result['new_missing_prior_evidence'][event]={
        'final_schedule_change_received':utc(last['at']), 'old_start':utc(old_start), 'new_start':utc(new_start),
        'new_five_minute_capture_boundary':utc(fallback),
        'seconds_late_for_fallback_deadline':last['at']-fallback-20,
        'first_point':utc(evidence['first_point']['source']),
        'prior_capture_receipts':[json.loads(x['detail'])['receipt'] for x in evidence['history'] if x['kind']=='prior_captured'],
        'conclusion':'Repeated schedule moves cleared prior captures. Final backward move arrived after the new fallback deadline. Frozen protocol exclusion; no qualified trade or hypothetical PnL inferred.'}
with urllib.request.urlopen('http://127.0.0.1:8516/',timeout=10) as response:
    result['dashboard_http_status']=response.status
assert result['dashboard_http_status']==200
result['disposition']='DONT_NOTIFY: routine additional paper outcomes; no new milestone, unexplained operational failure or required user action. Both scenarios currently negative; no edge established.'
(P/'operations_analysis.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['TENNIS90 PAPER TRADE LEDGER — one contract each; USD; no financial orders',
       'Scenario | Market | Side | Entered UTC | Entry | Fee | Friction | Outcome | Net | Settlement receipt']
for p in positions:
    outcome='open' if p['settled'] is None else 'win' if p['payout']==1 else 'loss' if p['payout']==0 else 'scalar'
    net='pending' if p['net'] is None else f"{p['net']:+.4f}"
    lines.append(f"{p['scenario']} | {p['ticker']} | {p['side']} | {utc(p['entered'])} | {p['price']:.4f} | {p['fee']:.4f} | {p['friction']:.4f} | {outcome} | {net} | {p['settlement_receipt']}")
(P/'TRADE_LEDGER.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['paired_matches','new_http_recoveries']},indent=2))
