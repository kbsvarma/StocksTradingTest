"""Point-in-time NBS forecast proxy, calibrated to prior exchange daily highs."""
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import sys,math,statistics,hashlib,time
from rebuild import P,OLD,EXT,read,save,ts,quote,sq,fee,metrics,day,summarize
sys.path.insert(0,str(P.parent/'kalshi_cross_market_edges_2026_10_11'))
from weather_bounds import STATIONS,source
sys.path.insert(0,str(EXT))
from evaluate import port
ARCH=P.parent/'kalshi_every_window_marketwide_2026_10_10'
QUOTES=P.parent/'kalshi_cross_market_edges_2026_10_11/weather_intraday'
START=ts('2026-09-12T04:00:00Z');END=ts('2026-10-10T04:00:00Z')

def stamp(s):return int(datetime.fromisoformat(s).replace(tzinfo=ZoneInfo('UTC')).timestamp())
def interval_yes(m,temp):
    temp=math.floor(temp+.5);kind=m['strike_type'];lo=m.get('floor_strike');hi=m.get('cap_strike')
    if kind=='greater':return temp>float(lo)
    if kind=='greater_or_equal':return temp>=float(lo)
    if kind=='less':return temp<float(hi)
    if kind=='less_or_equal':return temp<=float(hi)
    if kind=='between':return float(lo)<=temp<=float(hi)
    raise ValueError(kind)

def main():
    index=[g for g in read(ARCH/'daily_index.json') if g['asset'] in STATIONS]
    obs=source();models={};cov=Counter();prepared=[]
    for asset,(station,zone) in STATIONS.items():
        runs=defaultdict(list)
        for row in read(P/'forecasts'/('K'+station+'.json.gz')):
            if row.get('tmp') is not None:runs[stamp(row['runtime'])].append((stamp(row['ftime']),float(row['tmp'])))
        models[asset]=runs
    for g in sorted(index,key=lambda g:(g['date'],g['asset'])):
        asset=g['asset'];zone=ZoneInfo(STATIONS[asset][1]);date=datetime.fromisoformat(g['date']).replace(tzinfo=zone)
        finals={float(m['expiration_value']) for m in g['markets'] if m.get('expiration_value') not in ('',None)}
        if len(finals)!=1 or any(m.get('result') not in ('yes','no') for m in g['markets']):cov['nonbinary_or_final_missing']+=1;continue
        final=next(iter(finals));settled=max(ts(m['settlement_ts']) for m in g['markets'])
        for m in g['markets']:assert interval_yes(m,final)==(m['result']=='yes'),(m['ticker'],'settlement boundary')
        cp=QUOTES/(g['event']+'.json.gz')
        cm={k:{c['end_period_ts']:c for c in v} for k,v in read(cp).items()} if cp.exists() else {}
        for hour in [9,12,15]:
            t=int(date.replace(hour=hour).timestamp());upper=int((date+timedelta(days=1)).timestamp())
            for lag in [3,6]:
                available=[runtime for runtime in models[asset] if runtime<=t-lag*3600]
                if not available:cov[f'lag{lag}_no_run']+=1;continue
                runtime=max(available)
                future=[v for ft,v in models[asset][runtime] if t<=ft<upper]
                if len(future)<2:cov[f'lag{lag}_few_forecast_points']+=1;continue
                observed=[v for ot,v in obs[asset].get(g['date'],[]) if ot<=t-1800]
                if not observed:cov[f'lag{lag}_missing_observations']+=1;continue
                forecast=max(future+observed)
                prepared.append(dict(asset=asset,event=g['event'],date=g['date'],t=t,hour=hour,lag=lag,runtime=runtime,
                    forecast=forecast,error=final-forecast,final=final,settled=settled,markets=g['markets'],mult=g['mult'],cm=cm))
    save(P/'forecast_prepared.json.gz',[{k:v for k,v in r.items() if k not in ('markets','cm')} for r in prepared])
    results={};ledgers={};intents_out={};calibration={}
    for lag in [3,6]:
        for blend in [.5,0.]:
            if lag==6 and blend==0:continue
            name=f'forecast_lag{lag}h/market_weight{blend}'
            intents=[]
            for r in prepared:
                if r['lag']!=lag or not START<=r['t']<END:continue
                train=[a for a in prepared if a['asset']==r['asset'] and a['lag']==lag and a['hour']==r['hour'] and r['t']-28*86400<=a['t']<r['t'] and a['settled']<r['t']]
                if len(train)<14:cov[name+'_warmup']+=1;continue
                calibration[f'{name}/{r["event"]}/{r["hour"]}']={'events':[a['event'] for a in train],'last_label':max(a['settled'] for a in train),'n':len(train)}
                choices=[]
                for m in r['markets']:
                    cs=r['cm'].get(m['ticker'],{});q=quote(cs.get(r['t']))
                    if q is None or q[1]-q[0]>.05000001:continue
                    if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if r['t']-600<tt<=r['t'])<5:continue
                    py=(1+sum(interval_yes(m,r['forecast']+a['error']) for a in train))/(len(train)+2)
                    py=py*(1-blend)+sum(q)/2*blend
                    for side,p in [('yes',py),('no',1-py)]:
                        bid,ask=sq(q,side);edge=p-ask-fee(ask,r['mult'])/5-.01
                        if not .10<=ask<.95 or edge<.03:continue
                        choices.append(dict(asset=r['asset'],event=r['event'],ticker=m['ticker'],t=r['t'],close=ts(m['close_time']),
                            settled=ts(m['settlement_ts']),ask=ask,side=side,model_p=p,model_edge=edge,forecast=r['forecast'],
                            runtime=r['runtime'],lag=lag,horizon=r['hour'],cap=min(ask+.01,.95),mult=r['mult'],
                            calibration_n=len(train),calibration_end=max(a['settled'] for a in train),won=m['result']==side))
                if not choices:continue
                pick=max(choices,key=lambda a:(a['model_edge'],-a['ask'],a['ticker']))
                cs=r['cm'][pick['ticker']]
                pick['future']={label:quote(cs.get(r['t']+60),phase) for label,phase in [('base','open'),('delay60','close')]}
                intents.append(pick)
            intents_out[name]=intents
            for mode,stress in [('base',.01),('base',.02),('delay60',.01),('delay60',.02)]:
                candidates=[];skips=Counter()
                for r in intents:
                    q=r['future'][mode]
                    if q is None:skips['missing_quote']+=1;continue
                    entry=max(r['ask'],sq(q,r['side'])[1])
                    if entry>r['cap']+1e-8:skips['cap']+=1;continue
                    x={k:v for k,v in r.items() if k!='future'}
                    x.update(entry=entry,entered=r['t']+(10 if mode=='base' else 60),fee=fee(entry,r['mult']),stress=stress)
                    assert x['runtime']<=x['t']-lag*3600 and x['calibration_end']<x['t']<x['entered']<x['close']<x['settled']
                    candidates.append(x)
                acc,risk=port(candidates,stress)
                key=f'{name}/{mode}/cost{int(stress*100)}c';ledgers[key]=acc
                first={}
                for r in sorted(candidates,key=lambda r:r['t']):first.setdefault(r['event'],r)
                uncon=[dict(r,net=5*(int(r['won'])-r['entry'])-r['fee']-5*stress) for r in first.values()]
                results[key]={'all':summarize(acc,START,END),'cities':{c:metrics([r for r in acc if r['asset']==c]) for c in STATIONS},
                    'risk':risk,'intents':len(intents),'signal_events':len({r['event'] for r in intents}),
                    'execution_skips':dict(skips),'unconstrained_first_per_event':metrics(uncon),
                    'weeks':{day(w):metrics([r for r in acc if w<=r['t']<w+604800]) for w in range(START,END,604800)}}
                print(key,{k:v for k,v in results[key]['all'].items() if k!='daily'},flush=True)
    save(P/'forecast_results.json',results);save(P/'forecast_ledgers.json.gz',ledgers)
    save(P/'forecast_intents.json.gz',intents_out);save(P/'forecast_coverage.json',dict(cov));save(P/'forecast_calibration.json',calibration)
    save(P/'forecast_receipt.json',{'completed_ts':time.time(),'plan_sha256':hashlib.sha256((P/'FORECAST_PLAN.txt').read_bytes()).hexdigest(),
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'mode':'research_only'})
if __name__=='__main__':main()
