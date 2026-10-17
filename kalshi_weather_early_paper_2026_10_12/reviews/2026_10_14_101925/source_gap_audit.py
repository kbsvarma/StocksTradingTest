"""Read-only as-received audit of complete-station coverage gaps."""
from pathlib import Path
import collections
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import zlib
from zoneinfo import ZoneInfo

R=Path(__file__).resolve().parent
P=R.parent.parent
sys.path.insert(0,str(P))
from engine import CITIES,reconstruct,ts
op=json.loads((R/'operations_audit.json').read_text())
end=dt.datetime.fromisoformat(op['captured_utc']).timestamp()
cfg=json.loads((P/'frozen_config.json').read_text())
db=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True)
db.row_factory=sqlite3.Row
db.execute('BEGIN')
def rows(q,args=()):return [dict(x) for x in db.execute(q,args)]
def payload(r):
    raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(r['hash'],)).fetchone()[0])
    assert hashlib.sha256(raw).hexdigest()==r['hash']
    return json.loads(raw)
def stamp(t,tz=dt.timezone.utc):return dt.datetime.fromtimestamp(t,tz).isoformat()
out={'captured_utc':op['captured_utc'],'gaps':[]}
for gap in op['new_complete_source_gaps_over_10min']:
    series=gap['series'];base='https://external-api.kalshi.com/trade-api/v2/live_data/weather/'+CITIES[series]
    polls=rows('SELECT * FROM receipts WHERE url LIKE ? AND received>? AND received<=? ORDER BY received',(base+'?%',gap['from'],gap['to']))
    recovery=rows('SELECT * FROM sources WHERE series=? AND received=? ORDER BY source DESC LIMIT 1',(series,gap['to']))[0]
    previous=rows('SELECT * FROM sources WHERE series=? AND received=? ORDER BY source DESC LIMIT 1',(series,gap['from']))[0]
    changes=rows('SELECT * FROM receipts WHERE url=? AND received<=? AND status=200 ORDER BY received',(base+'/calibrations',gap['to']))
    def diagnostics(r):
        cr=max((x for x in changes if x['received']<=r['received']),key=lambda x:x['received'])
        configs=payload(cr)['calibrations'];points=[]
        for point in payload(r)['timeseries']:
            if point['t']/1000<=previous['source']:continue
            val,reason=reconstruct(point,configs,r['received'])
            available=[x for x in configs if x['published_at_ms']/1000<=r['received'] and x['effective_at_ms']<=point['t']]
            conf=max(available,key=lambda x:x['effective_at_ms'])
            actual={s['station_id']:s for s in point.get('stations',[])}
            missing=[x['station_id'] for x in conf['stations'] if x['station_id'] not in actual or actual[x['station_id']].get('source')!='hf_asos' or actual[x['station_id']].get('temp_f') is None]
            age=r['received']-point['t']/1000
            points.append({'source_utc':stamp(point['t']/1000),'pending':point.get('v') is None,
                'provider_status':point.get('status'),'canonical_value':point.get('v'),'age_seconds':age,
                'reconstruction_reason':reason,'forecast':val['forecast'] if val else None,'missing_primary':missing,
                'fallbacks':[actual[k] for k in missing if k in actual],
                'would_pass_fresh_pending_source_checks':val is not None and point.get('v') is None and 0<=age<=cfg['max_source_age']})
        return {'receipt':r['id'],'received_utc':stamp(r['received']),'http_status':r['status'],
            'calibration_receipt':cr['id'],'points':points}
    market_url='https://external-api.kalshi.com/trade-api/v2/markets?series_ticker='+series+'&status=open&limit=1000'
    catalogues=rows('SELECT * FROM receipts WHERE url=? AND received BETWEEN ? AND ? AND status=200 ORDER BY received',(market_url,gap['from']-120,gap['to']))
    events={}
    for cat in catalogues:
        for m in payload(cat)['markets']:
            close=ts(m['close_time'])
            if close-cfg['max_remaining']<gap['to'] and close-cfg['min_remaining']>gap['from']:
                events.setdefault(m['event_ticker'],{'close':close,'catalogue_receipt':cat['id']})
    windows=[]
    for event,record in events.items():
        opens=record['close']-cfg['max_remaining'];closes=record['close']-cfg['min_remaining']
        # Include the immediately prior source poll: a decision may use a receipt up to10s old.
        relevant=[x for x in polls if opens-10<=x['received']<=closes]
        details=[diagnostics(x) for x in relevant]
        qualified_sources=[{'receipt':x['receipt'],'point':point} for x in details for point in x['points'] if point['would_pass_fresh_pending_source_checks']]
        decisions=rows('SELECT id,created,reason FROM decisions WHERE event=?',(event,))
        attempts=rows('SELECT id,scenario,created,status,reason FROM attempts WHERE event=?',(event,))
        cycles=rows('SELECT id,created,pre_receipt,post_receipt,data FROM cycles WHERE created BETWEEN ? AND ? ORDER BY created',(opens,closes))
        city_cycles=[{k:v for k,v in x.items() if k!='data'} for x in cycles if series in json.loads(x['data'])['markets']]
        assert relevant and not qualified_sources and not decisions and not attempts
        windows.append({'event':event,'catalogue_receipt':record['catalogue_receipt'],
            'opens_utc':stamp(opens),'closes_utc':stamp(closes),
            'opens_et':stamp(opens,ZoneInfo('America/New_York')),'closes_et':stamp(closes,ZoneInfo('America/New_York')),
            'source_polls_checked':len(details),'source_receipts':details,'valid_fresh_pending_sources':qualified_sources,
            'decision_records':decisions,'attempt_records':attempts,'paired_collection_cycles':city_cycles,
            'interpretation':'Window could not be evaluated because no complete, fresh pending primary source was received. Qualification, fillability and hypothetical PnL are unassessable, not assumed.'})
    recovery_receipt=rows('SELECT * FROM receipts WHERE id=?',(recovery['receipt'],))[0]
    detail=diagnostics(recovery_receipt)
    assert any(x['would_pass_fresh_pending_source_checks'] for x in detail['points'])
    out['gaps'].append({**gap,'minutes':gap['seconds']/60,
        'from_utc':stamp(gap['from']),'to_utc':stamp(gap['to']),
        'previous_complete_source_utc':stamp(previous['source']),'next_complete_source_utc':stamp(recovery['source']),
        'source_poll_count_including_recovery':len(polls),'http_errors':sum(x['status']!=200 or x['error'] is not None for x in polls),
        'maximum_http_poll_gap_seconds':max(b['received']-a['received'] for a,b in zip(polls,polls[1:])),
        'entry_windows':windows,'recovery':detail,
        'latest_complete_at_review':op['last_complete_sources'][series],
        'recommendation':'Continue frozen collection. Upstream data recovered automatically; no collector repair indicated. Do not replace missing primary readings with METAR or train on forward outcomes.'})
out['notification_decision']='NOTIFY: LA primary-source completeness gap lasted55minutes and prevented evaluating one entire entry window; source has recovered. No known qualified trade or hypothetical PnL claimed.'
db.close()
(R/'source_gap_audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'captured_utc':out['captured_utc'],'gaps':[{k:v for k,v in x.items() if k not in ['entry_windows','recovery']} for x in out['gaps']],
    'windows':[{k:v for k,v in w.items() if k not in ['source_receipts','paired_collection_cycles']} for g in out['gaps'] for w in g['entry_windows']],
    'decision':out['notification_decision']},indent=2))
