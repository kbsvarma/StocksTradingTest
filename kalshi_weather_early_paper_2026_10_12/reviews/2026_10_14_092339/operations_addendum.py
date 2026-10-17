"""Read-only investigation of recovered throttling and source incompleteness."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zlib

R=Path(__file__).resolve().parent
P=R.parent.parent
sys.path.insert(0,str(P))
from engine import CITIES, reconstruct, ts
op=json.loads((R/'operations_audit.json').read_text())
end=dt.datetime.fromisoformat(op['captured_utc']).timestamp()
start=dt.datetime.fromisoformat(op['previous_review_utc']).timestamp()
db=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
db.execute('BEGIN')
def rows(q,args=()):return [dict(x) for x in db.execute(q,args)]
def payload(r):
    raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash']
    return json.loads(raw)
def utc(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
out={'captured_utc':op['captured_utc'],'rate_limits':[],'incomplete_source_observations':[]}
for error in op['new_http_errors']:
    next_request=rows('SELECT id,started,received,status,url FROM receipts WHERE started>? AND received<=? ORDER BY started LIMIT 1',(error['received'],end))[0]
    recovery=rows('SELECT id,started,received,status,url FROM receipts WHERE url=? AND received>? AND received<=? AND status=200 AND error IS NULL ORDER BY received LIMIT 1',(error['url'],error['received'],end))[0]
    assert error['status']==429 and next_request['started']-error['received']>=30
    nearby=rows('SELECT id,series,created,reason FROM decisions WHERE created BETWEEN ? AND ?',(error['received']-60,error['received']+180))
    attempts=rows('SELECT id,scenario,created,status FROM attempts WHERE created BETWEEN ? AND ?',(error['received']-60,error['received']+180))
    out['rate_limits'].append({'error':error,'first_request_after_cooldown':next_request,
        'cooldown_seconds':next_request['started']-error['received'],'same_url_recovery':recovery,
        'recovery_seconds':recovery['received']-error['received'],'nearby_decisions':nearby,'nearby_attempts':attempts})
    assert not nearby and not attempts
out['new_censored_source_versions']=rows('SELECT id,series,source,received,censored FROM sources WHERE received>? AND received<=? AND censored=1',(start,end))
for series,state in op['last_complete_sources'].items():
    if state['first_arrival_age_seconds']<300:continue
    base='https://external-api.kalshi.com/trade-api/v2/live_data/weather/'+CITIES[series]
    polls=rows('SELECT * FROM receipts WHERE url LIKE ? AND received>? AND received<=? ORDER BY received',(base+'?%',state['first_seen'],end))
    examples=[]
    for r in [polls[0],polls[len(polls)//2],polls[-1]]:
        cr=rows('SELECT * FROM receipts WHERE url=? AND received<=? AND status=200 ORDER BY received DESC LIMIT 1',(base+'/calibrations',r['received']))[0]
        configs=payload(cr)['calibrations']
        data=payload(r)
        for point in data['timeseries']:
            if point['t']/1000<=state['source']:continue
            value,reason=reconstruct(point,configs,r['received'])
            available=[x for x in configs if x['published_at_ms']/1000<=r['received'] and x['effective_at_ms']<=point['t']]
            conf=max(available,key=lambda x:x['effective_at_ms'])
            actual={s['station_id']:s for s in point.get('stations',[])}
            missing=[x['station_id'] for x in conf['stations'] if x['station_id'] not in actual or actual[x['station_id']].get('source')!='hf_asos' or actual[x['station_id']].get('temp_f') is None]
            examples.append({'receipt':r['id'],'received_utc':utc(r['received']),'status':r['status'],'calibration_receipt':cr['id'],
                'source_utc':utc(point['t']/1000),'provider_status':point.get('status'),'canonical_value':point.get('v'),
                'reconstruction_reason':reason,'missing_primary':missing,
                'fallback_details':[actual[k] for k in missing if k in actual]})
    catalogue=rows('SELECT * FROM receipts WHERE url=? AND status=200 AND received<=? ORDER BY received DESC LIMIT 1',
        ('https://external-api.kalshi.com/trade-api/v2/markets?series_ticker='+series+'&status=open&limit=1000',end))[0]
    events={m['event_ticker']:ts(m['close_time']) for m in payload(catalogue)['markets']}
    windows=[{'event':event,'opens_utc':utc(close-300),'closes_utc':utc(close-60)} for event,close in events.items() if close-60>=state['first_seen']]
    overlap=[x for event,close in events.items() if close-300<=end and close-60>=state['first_seen'] for x in [event]]
    assert not overlap
    out['incomplete_source_observations'].append({'series':series,'last_complete_source_utc':utc(state['source']),
        'last_complete_first_arrival_utc':utc(state['first_seen']),'seconds_without_a_new_complete_minute_at_capture':state['first_arrival_age_seconds'],
        'source_polls_during_observed_gap':len(polls),'http_errors':sum(x['status']!=200 or x['error'] is not None for x in polls),
        'examples':examples,'catalogue_receipt':catalogue['id'],'prospective_entry_windows':windows,'overlapping_entry_windows':overlap,
        'classification':'Incomplete upstream primary station data, not a stopped collector. Frozen model correctly excludes fallback METAR/missing roster. Ongoing gap outside the entry window at capture; no demonstrated missed qualified signal.'})
out['limitations']=['No hypothetical trades or PnL imputed for unobserved opportunities. Source station completeness can recover or worsen after this capture.',
    'Original first-arrival and publication times are polling bounds, not exclusive-information timestamps.']
out['disposition']='DONT_NOTIFY: results unchanged; recovered catalogue throttle and pending incomplete LA source outside entry windows require no user action. Preserve source-gap evidence for the next scheduled review.'
db.close()
(R/'operations_addendum.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
