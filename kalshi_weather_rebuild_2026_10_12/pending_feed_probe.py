"""Freeze raw pending-index reconstructions, then check later canonical values.

This is an information-availability probe, NOT a strategy or PnL backtest.
It does not treat source receipt timestamps as public availability timestamps.
"""
from pathlib import Path
import json,gzip,hashlib,time,requests
P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
CITIES={'KXTEMPMIAH':'miami','KXTEMPCHIHS':'chicago','KXTEMPLAXHS':'la-coastal','KXTEMPNYCHS':'nyc'}
predictions=[];receipts={}
for series,city in CITIES.items():
    url=BASE+'/live_data/weather/'+city+'?last_sec=3600&detailed=true'
    key=hashlib.sha256(url.encode()).hexdigest()
    with gzip.open(P/'extension_raw'/(key+'.json.gz'),'rt') as f:receipt=json.load(f)
    received=receipt['retrieved_ts'];detail=receipt['response'];cfgs=json.loads((P/'extension'/series/'calibrations.json').read_text())['calibrations']
    canonical=[a for a in detail['timeseries'] if a.get('v') is not None]
    receipts[city]={'received_ts':received,'latest_canonical_event_ms':max(a['t'] for a in canonical),
        'latest_canonical_age_seconds':received-max(a['t'] for a in canonical)/1000}
    for a in detail['timeseries']:
        if a.get('v') is not None or not a.get('stations'):continue
        cfg=max((c for c in cfgs if c['effective_at_ms']<=a['t'] and c['published_at_ms']<received*1000),key=lambda c:c['effective_at_ms'])
        config={s['station_id']:s for s in cfg['stations']};raw={s['station_id']:s for s in a['stations']}
        if set(config)!=set(raw) or any(s.get('source')!='hf_asos' or s.get('temp_f') is None for s in raw.values()):continue
        # All configured members present: offsets/reference cancel algebraically,
        # but explicitly retain the published formula and configuration version.
        adjusted={sid:(float(raw[sid]['temp_f'])-32)*5/9-c['offset_c']+cfg['city_reference_c'] for sid,c in config.items()}
        celsius=sum(config[sid]['weight']*value for sid,value in adjusted.items())/sum(c['weight'] for c in config.values())
        forecast=round(celsius*9/5+32,2)
        predictions.append({'city':city,'series':series,'event_ms':a['t'],'public_seen_ts':received,
            'received_before_nominal_deadline_seconds':a['t']/1000+300-received,
            'station_receipt_age_seconds':max(s['received_at_ms'] for s in raw.values())/1000-a['t']/1000,
            'canonical_point_was_absent':True,'raw_reconstruction_f':forecast,'config_version':cfg['config_version'],
            'caveat':'Assumes pending raw members survive QC/source substitution; not a canonical or future settlement value.'})
(P/'pending_predictions_frozen.json').write_text(json.dumps({'predictions':predictions,'snapshots':receipts},indent=2)+'\n')
verification=[]
for series,city in CITIES.items():
    rr=[r for r in predictions if r['city']==city]
    if not rr:continue
    sec=time.time()%60
    if sec<36:time.sleep(36-sec)
    elif sec>55:time.sleep(60-sec+36)
    args={'from':min(r['event_ms'] for r in rr),'to':max(r['event_ms'] for r in rr)+60000,'detailed':'true'}
    response=requests.get(BASE+'/live_data/weather/'+city,params=args,timeout=12)
    response.raise_for_status();obj=response.json();now=time.time()
    (P/f'pending_check_{city}.json').write_text(json.dumps({'url':response.url,'received_ts':now,'response':obj},indent=2))
    points={a['t']:a for a in obj['timeseries']}
    for r in rr:
        a=points.get(r['event_ms'],{});actual=a.get('v')
        verification.append(dict(r,canonical_later=actual,status_later=a.get('status'),matches=actual is not None and abs(actual-r['raw_reconstruction_f'])<=.011))
    time.sleep(1.5)
out={'checked_ts':time.time(),'points':verification,'all_matches':all(r['matches'] for r in verification),
    'count':len(verification),'snapshots':receipts,'economic_edge_tested':False,'orders_submitted':0}
(P/'pending_feed_result.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
