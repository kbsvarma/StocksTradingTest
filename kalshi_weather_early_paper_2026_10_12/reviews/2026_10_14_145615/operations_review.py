"""Read-only incremental paper audit, bounded by the review.py capture time.

Never writes the observer database or modifies frozen strategy files/services.
Preserves the original existing-bot baseline and explains separately authorized
deployment differences with their dated receipt.
"""
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import zlib
from urllib.parse import urlparse

R = Path(__file__).resolve().parent
P = R.parent.parent
sys.path.insert(0, str(P))
from engine import CITIES, choose, digest

audit = json.loads((R/'audit.json').read_text())
previous = json.loads((P/'review_state.json').read_text())
(R/'review_state.before.json').write_text(json.dumps(previous,indent=2)+'\n')
start = dt.datetime.fromisoformat(previous['last_checked_utc']).timestamp()
end = dt.datetime.fromisoformat(audit['captured_utc']).timestamp()
cfg = json.loads((P/'frozen_config.json').read_text())
training = json.loads((P/'training.json').read_text())
db = sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True)
db.row_factory = sqlite3.Row
db.execute('BEGIN')
def rows(query,args=()):
    return [dict(x) for x in db.execute(query,args)]
def payload(rid):
    r = db.execute('SELECT hash FROM receipts WHERE id=?',(rid,)).fetchone()
    raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash']
    return json.loads(raw)
assert digest(cfg)==audit['config_hash']
for name, expected in cfg['frozen_files'].items():
    assert hashlib.sha256((P/name).read_bytes()).hexdigest()==expected
new_decisions=rows('SELECT * FROM decisions WHERE created>? AND created<=? ORDER BY id',(start,end))
for d in new_decisions:
    evidence=json.loads(d['data'])
    books={b['ticker']:b for b in payload(evidence['postbook_receipt'])['orderbooks']}
    boundary=evidence['volume_boundary']
    volumes={m['market_ticker']:sum(float(c.get('volume_fp') or 0) for c in m['candlesticks'] if boundary-600<c['end_period_ts']<=boundary) for m in payload(evidence['volume_receipt'])['markets']}
    markets=[m for m in payload(evidence['catalogue_receipt'])['markets'] if m['event_ticker']==d['event']]
    pick,_=choose(markets,books,evidence['source'],training,d['created'],volumes,cfg)
    assert pick==evidence['pick']
    assert ('eligible' if pick else 'no_edge_or_price')==d['reason']
new_attempts=rows('SELECT * FROM attempts WHERE created>? AND created<=?',(start,end))
for p in audit['trades']:
    assert p['qty']==cfg['contracts']==5
    assert p['friction']==cfg['friction'][p['scenario']]*p['qty']
poll_gaps=[];complete_gaps=[];last_sources={};new_calibrations={};poll_stats={}
for series,city in CITIES.items():
    url='https://external-api.kalshi.com/trade-api/v2/live_data/weather/'+city
    polls=rows('SELECT id,started,received,status,error FROM receipts WHERE url LIKE ? AND received<=? ORDER BY received',(url+'?%',end))
    new_polls=[x for x in polls if x['received']>start]
    gaps=[{'series':series,'from_receipt':a['id'],'to_receipt':b['id'],'seconds':b['received']-a['received']} for a,b in zip(polls,polls[1:]) if b['received']>start and b['received']-a['received']>45]
    poll_gaps.extend(gaps)
    poll_stats[series]={'new_polls':len(new_polls),'latest_poll_age_seconds':end-polls[-1]['received'],'max_new_gap_seconds':max((b['received']-a['received'] for a,b in zip(polls,polls[1:]) if b['received']>start),default=0)}
    firsts=rows('SELECT source,MIN(received) first_seen FROM sources WHERE series=? AND received<=? GROUP BY source ORDER BY first_seen',(series,end))
    for a,b in zip(firsts,firsts[1:]):
        if b['first_seen']>start and b['first_seen']-a['first_seen']>600:
            complete_gaps.append({'series':series,'from':a['first_seen'],'to':b['first_seen'],'seconds':b['first_seen']-a['first_seen']})
    last_sources[series]={'source':firsts[-1]['source'],'first_seen':firsts[-1]['first_seen'],'first_arrival_age_seconds':end-firsts[-1]['first_seen']}
    calibrations=rows('SELECT hash,MIN(received) first_seen,MIN(id) first_receipt FROM receipts WHERE url=? AND status=200 AND received<=? GROUP BY hash ORDER BY first_seen',(url+'/calibrations',end))
    new_calibrations[series]=[x for x in calibrations if x['first_seen']>start]
new_errors=rows('SELECT id,url,received,status,error FROM receipts WHERE received>? AND received<=? AND (error IS NOT NULL OR status!=200)',(start,end))
new_health=rows('SELECT * FROM health WHERE at>? AND at<=?',(start,end))
db.close()

# The user separately authorized a WTI repair after the previous weather review.
# Do not silently replace weather's original preservation baseline.
deploy_path=Path('/home/varma/kalshi_wti_fresh_retry_2026_10_13/deployment_receipt.json')
deploy=json.loads(deploy_path.read_text())
explained={};unexplained={}
for name,old_hash in audit['existing_bots']['hash_mismatches'].items():
    path=Path(name);actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if str(path.parent)=='/home/varma/kalshi_revalidation_2026_09_12/live_v1' and path.name in deploy['changed_files'] and actual==deploy['source_hashes'][path.name]:
        explained[name]={'weather_baseline':old_hash,'current':actual,'authorized_deployment':str(deploy_path),'deployed_et':deploy['verified_et']}
    else:unexplained[name]={'baseline':old_hash,'current':actual}
service_checks={}
for name in ('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service'):
    raw=subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
    current=dict(x.split('=',1) for x in raw.splitlines())
    service_checks[name]={'current':current,'matches_authorized_deployment':current==deploy['services_after'][name]}
quiet=(not new_attempts and not new_errors and not complete_gaps and not poll_gaps and not any(new_calibrations.values()) and not unexplained and all(x['matches_authorized_deployment'] for x in service_checks.values()) and all(x['first_arrival_age_seconds']<600 for x in last_sources.values()) and len(audit['qualified_signals'])==previous['last_qualified_signals'] and audit['portfolios']['early']['settled']==previous['last_early_settled'])
result={'captured_utc':audit['captured_utc'],'previous_review_utc':previous['last_checked_utc'],
    'new_decisions_verified':len(new_decisions),'new_decision_reasons':dict(collections.Counter(x['reason'] for x in new_decisions)),
    'new_attempts':new_attempts,'new_http_errors':new_errors,'new_health':new_health,
    'new_http_poll_gaps_over_45s':poll_gaps,'new_complete_source_gaps_over_10min':complete_gaps,
    'last_complete_sources':last_sources,'source_poll_stats':poll_stats,'new_calibration_versions':new_calibrations,
    'previous_pairing_artifact':previous['last_execution_audit'],'pairing_unchanged':not new_attempts,
    'canonical_matches':audit['feed']['canonical_matches'],'canonical_comparisons':audit['feed']['canonical_comparisons'],
    'forecast_revision_records':audit['feed']['forecast_revision_records'],'snapshot_retained':audit['snapshot_retained'],
    'preservation_baseline_explained_changes':explained,'unexplained_file_changes':unexplained,
    'service_checks':service_checks,'ntp':subprocess.check_output(['timedatectl','show','-p','NTPSynchronized'],text=True),
    'quiet_eligible':quiet,'changes':'Review artifacts only; observer code/config, ledger and all existing bots untouched. Original preservation baseline retained.'}
(R/'operations_audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
