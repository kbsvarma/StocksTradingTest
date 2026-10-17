"""First observed public arrival, excluding the first snapshot's censored history."""
from pathlib import Path
import json,gzip,statistics
P=Path(__file__).resolve().parent
previous={};rows=[];counts={};canonical={};first_values={};changed=[];snapshots=[];complete=[];complete_seen=set();startup=set();recon={}
series={'miami':'KXTEMPMIAH','chicago':'KXTEMPCHIHS','la-coastal':'KXTEMPLAXHS','nyc':'KXTEMPNYCHS'}
configs={c:json.loads((P.parent/'kalshi_weather_rebuild_2026_10_12/extension'/s/'calibrations.json').read_text())['calibrations'] for c,s in series.items()}
for f in sorted((P/'probe').glob('*.json.gz')):
    with gzip.open(f,'rt') as stream:a=json.load(stream)
    city=a['body']['city'];old=previous.get(city);keys={r['t'] for r in a['body']['timeseries']}
    counts[city]=counts.get(city,0)+1
    snapshots.append({'city':city,'received':a['received'],'duration':a['received']-a['started'],
        'canonical_age':a['received']-max(r['t']/1000 for r in a['body']['timeseries'] if r.get('v') is not None)})
    for r in a['body']['timeseries']:
        k=(city,r['t']);stations=r.get('stations',[])
        if r.get('v') is not None:canonical[k]=r['v']
        raw={s['station_id']:[s.get('temp_f'),s.get('received_at_ms'),s.get('obs_time_ms')] for s in stations}
        if k not in first_values:first_values[k]=raw
        elif first_values[k]!=raw:
            prior=first_values[k]
            changed.append({'city':city,'event':r['t'],'received':a['received'],
                'added_station_ids':sorted(raw.keys()-prior.keys()),'removed_station_ids':sorted(prior.keys()-raw.keys()),
                'changed_existing_temperatures':[s for s in raw.keys()&prior.keys() if raw[s][0]!=prior[s][0]]})
            first_values[k]=raw
        if old is None:startup.add(k)
        cfg=max((c for c in configs[city] if c['effective_at_ms']<=r['t'] and c['published_at_ms']/1000<=a['received']),key=lambda c:c['effective_at_ms'])
        roster={s['station_id']:s for s in cfg['stations']};sr={s['station_id']:s for s in stations}
        if set(sr)==set(roster) and all(s.get('source')=='hf_asos' and s.get('temp_f') is not None and s.get('received_at_ms') is not None for s in stations):
            predicted=round(sum(c['weight']*((sr[sid]['temp_f']-32)*5/9-c['offset_c']+cfg['city_reference_c']) for sid,c in roster.items())/sum(c['weight'] for c in roster.values())*9/5+32,2)
            if k not in recon and r.get('v') is None:recon[k]=predicted
            if k not in startup and k not in complete_seen:
                receipt=max(s['received_at_ms'] for s in stations)/1000
                complete.append({'city':city,'event':r['t']/1000,'first_seen':a['received'],'upper':a['received']-receipt,
                    'source_age':a['received']-r['t']/1000,'predicted':predicted,'was_pending':r.get('v') is None})
                complete_seen.add(k)
        if not old or r['t'] in old[1]:continue
        if not stations or any(s.get('received_at_ms') is None for s in stations):continue
        latest=max(s['received_at_ms'] for s in stations)/1000
        rows.append(dict(city=city,event=r['t']/1000,first_seen=a['received'],vendor_receipt=latest,
            upper=a['received']-latest,lower=max(0,old[0]-latest),source_age=a['received']-r['t']/1000,
            pending=r.get('v') is None,station_count=len(stations)))
    previous[city]=(a['received'],keys)
def stats(v):
    v=sorted(v)
    return {'n':len(v),'min':min(v),'median':statistics.median(v),'p95':v[int(.95*(len(v)-1))],'max':max(v)} if v else {'n':0}
out={'snapshots_by_city':counts,'first_arrivals':rows,'snapshots':snapshots,'vendor_to_public_upper_seconds':stats([r['upper'] for r in rows]),
    'vendor_to_public_lower_seconds':stats([r['lower'] for r in rows]),'point_event_age_seconds':stats([r['source_age'] for r in rows]),
    'canonical_age_seconds':stats([r['canonical_age'] for r in snapshots]),'raw_station_changes':changed,
    'complete_primary_roster_first_arrivals':complete,'complete_primary_vendor_delay_upper_seconds':stats([r['upper'] for r in complete]),
    'first_reconstruction_vs_later_canonical':[{'city':k[0],'event':k[1]/1000,'predicted':v,'canonical':canonical[k],'matches':abs(v-canonical[k])<=.011} for k,v in recon.items() if k in canonical],
    'limitations':['Six once-per-minute rounds, not continuous first-arrival capture. Startup history excluded.',
        'Vendor receipt clock is distinct from event time; data source age is not network latency.',
        'No contemporaneous books or executions were sampled. No economic edge demonstrated.',
        'No extrapolation to historical public availability, sustained reliability or all market conditions.']}
(P/'feed_arrival_audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ('first_arrivals','snapshots')},indent=2))
