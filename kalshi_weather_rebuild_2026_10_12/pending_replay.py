"""Frozen pending-station late-hour replay. Eventual QC codes never enter input selection."""
from pathlib import Path
from collections import defaultdict,Counter
import math,time,hashlib
from rebuild import P,OLD,EXT,CITIES,read,save,ts,quote,sq,fee,metrics,day,summarize,week,port
START=ts('2026-10-03T04:00:00Z');END=ts('2026-10-12T04:00:00Z');SPLIT=ts('2026-10-10T04:00:00Z')

def prepare():
    complete=read(P/'pending_pull_complete.json')
    assert complete['end']==END
    assert all(len(list((P/'pending_history'/city).glob('*.json.gz')))==complete['days_per_city'] for city in CITIES)
    prepared=[];coverage={}
    for city in CITIES:
        d=EXT/'data'/city
        if not d.exists():d=OLD/'replays'/city
        assert float(read(d/'series.json')['fee_multiplier'])==1
        newer=P/'extension'/city
        ms=list({m['ticker']:m for m in read(d/'markets.json')+read(newer/'markets.json')}.values())
        configs=read(newer/'calibrations.json')['calibrations'];raw={};cov=Counter()
        for f in (P/'pending_history'/city).glob('*.json.gz'):
            for a in read(f)['response']['timeseries']:
                t=int(a['t'])//1000
                if t in raw:assert raw[t]==a,(city,t,'historical station revision')
                raw[t]=a
        reconstructed={}
        for t,a in raw.items():
            stations=a.get('stations',[]);cov['minute_records']+=1
            cov['receipt_basis_'+str(a.get('receipt_basis','not_provided'))]+=1
            matches=[c for c in configs if c['effective_at_ms']<=t*1000]
            if not matches:cov['no_configuration']+=1;continue
            cfg=max(matches,key=lambda c:c['effective_at_ms'])
            roster={s['station_id']:s for s in cfg['stations']};r={s['station_id']:s for s in stations}
            if set(r)!=set(roster):cov['incomplete_roster']+=1;continue
            if any(s.get('source')!='hf_asos' or s.get('received_at_ms') is None or s.get('temp_f') is None for s in r.values()):
                cov['missing_primary_temperature_or_receipt']+=1;continue
            if any(not math.isfinite(float(s['temp_f'])) or not -40<=float(s['temp_f'])<=122 for s in r.values()):
                cov['hard_range']+=1;continue
            celsius=sum(c['weight']*((float(r[sid]['temp_f'])-32)*5/9-c['offset_c']+cfg['city_reference_c']) for sid,c in roster.items())/sum(c['weight'] for c in roster.values())
            predicted=round(celsius*9/5+32,2)
            received=max(s['received_at_ms'] for s in r.values())/1000
            reconstructed[t]={'source_last':t,'forecast':predicted,'latest_station_receipt':received,
                'config_published':cfg['published_at_ms']/1000,'config_version':cfg['config_version']}
            cov['reconstructable']+=1
            if a.get('v') is not None:
                cov['later_canonical_comparable']+=1
                cov['later_matches' if abs(predicted-a['v'])<=.011 else 'later_differs']+=1
        events=defaultdict(list)
        for m in ms:
            if START-604800<=ts(m['close_time'])<=END:events[m['event_ticker']].append(m)
        for event,items in events.items():
            if any(m.get('result') not in ('yes','no') or m.get('expiration_value') is None or not m.get('settlement_ts') for m in items):cov['nonbinary']+=1;continue
            cl=ts(items[0]['close_time']);final=float(items[0]['expiration_value'])
            cp=d/(event+'_candles.json.gz')
            if not cp.exists():cp=newer/(event+'_candles.json.gz')
            if not cp.exists():cov['no_candle_file']+=1;continue
            cm={k:{c['end_period_ts']:c for c in v} for k,v in read(cp).items()}
            for h in [5,4,3,2,1]:
                t=cl-h*60
                for margin in [60,120]:
                    available=[reconstructed[s] for s in range(t-300,t+1,60) if s in reconstructed and reconstructed[s]['latest_station_receipt']+margin<=t and reconstructed[s]['config_published']<=t]
                    if not available:cov[f'margin{margin}_unavailable_horizon']+=1;continue
                    source=max(available,key=lambda s:s['source_last'])
                    prepared.append(dict(asset=city,event=event,t=t,close=cl,settled=max(ts(m['settlement_ts']) for m in items),
                        final=final,error=final-source['forecast'],horizon=h,margin=margin,**source,items=items,cm=cm))
        coverage[city]=dict(cov)
    save(P/'pending_coverage.json',coverage)
    save(P/'pending_prepared.json.gz',[{k:v for k,v in r.items() if k not in ('items','cm')} for r in prepared])
    return prepared

def main():
    prepared=prepare();results={};ledgers={};calibration={};saved_intents={}
    for margin in [60,120]:
        for blend in [.5,0.]:
            if margin==120 and blend==0:continue
            name=f'margin{margin}s/market_weight{blend}';intents=[]
            for city in CITIES:
                for w in [START,SPLIT]:
                    errors={h:[] for h in [5,4,3,2,1]}
                    for a in prepared:
                        if a['asset']==city and a['margin']==margin and w-604800<=a['close']<w and a['settled']<w:
                            errors[a['horizon']].append(a)
                    for h,rows in errors.items():
                        calibration[f'{name}/{city}/{w}/{h}']={'events':[a['event'] for a in rows],'n':len(rows),'last_label':max((a['settled'] for a in rows),default=None)}
                    for r in prepared:
                        if r['asset']!=city or r['margin']!=margin or not w<=r['t']<min(w+604800,END):continue
                        train=errors[r['horizon']]
                        if len(train)<60:continue
                        choices=[]
                        for m in r['items']:
                            cs=r['cm'].get(m['ticker'],{});q=quote(cs.get(r['t']))
                            if q is None or q[1]-q[0]>.02000001:continue
                            if sum(float(c.get('volume_fp') or 0) for t,c in cs.items() if r['t']-600<t<=r['t'])<5:continue
                            py=(1+sum(r['forecast']+a['error']>float(m['floor_strike']) for a in train))/(len(train)+2)
                            py=py*(1-blend)+sum(q)/2*blend
                            for side,p in [('yes',py),('no',1-py)]:
                                _,ask=sq(q,side);f=fee(ask,1);edge=p-ask-f/5-.01
                                if not .50<=ask<.95 or edge<.03:continue
                                x={k:v for k,v in r.items() if k not in ('items','cm')}
                                x.update(ticker=m['ticker'],side=side,ask=ask,model_p=p,model_edge=edge,cap=min(ask+.01,.95),
                                    calibration_end=w,calibration_n=len(train),won=m['result']==side,fee_multiplier=1)
                                choices.append(x)
                        if not choices:continue
                        pick=max(choices,key=lambda a:(a['model_edge'],-a['ask'],a['ticker']))
                        cs=r['cm'][pick['ticker']]
                        pick['future']={label:quote(cs.get(r['t']+60),phase) for label,phase in [('base','open'),('delay60','close')]}
                        intents.append(pick)
            saved_intents[name]=intents
            for mode,stress in [('base',.01),('base',.02),('delay60',.01),('delay60',.02)]:
                candidates=[];skips=Counter()
                for r in intents:
                    entered=r['t']+(10 if mode=='base' else 60)
                    if entered>=r['close']:skips['closed_before_delayed_entry']+=1;continue
                    q=r['future'][mode]
                    if q is None:skips['missing_quote']+=1;continue
                    entry=max(r['ask'],sq(q,r['side'])[1])
                    if entry>r['cap']+1e-8:skips['cap']+=1;continue
                    x={k:v for k,v in r.items() if k!='future'}
                    x.update(entry=entry,entered=entered,fee=fee(entry,1),stress=stress)
                    assert x['latest_station_receipt']+margin<=x['t'] and x['config_published']<=x['t']<entered<x['close']<x['settled']
                    candidates.append(x)
                acc,risk=port(candidates,stress);key=f'{name}/{mode}/cost{int(stress*100)}c'
                ledgers[key]=acc
                results[key]={'all':summarize(acc,START,END),'first7':summarize([r for r in acc if r['t']<SPLIT],START,SPLIT),
                    'last2':summarize([r for r in acc if r['t']>=SPLIT],SPLIT,END),
                    'cities':{c:metrics([r for r in acc if r['asset']==c]) for c in CITIES},'risk':risk,'intents':len(intents),
                    'signal_events':len({r['event'] for r in intents}),'execution_skips':dict(skips)}
                print(key,{k:v for k,v in results[key]['all'].items() if k!='daily'},flush=True)
    save(P/'pending_results.json',results);save(P/'pending_ledgers.json.gz',ledgers);save(P/'pending_intents.json.gz',saved_intents)
    save(P/'pending_calibration.json',calibration)
    save(P/'pending_receipt.json',{'completed_ts':time.time(),'plan_sha256':hashlib.sha256((P/'PENDING_PLAN.txt').read_bytes()).hexdigest(),
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'mode':'research_only'})
if __name__=='__main__':main()
