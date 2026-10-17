"""Coherent temperature-distribution replay. All learning precedes each test week."""
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import json,gzip,math,time,hashlib,sys
import numpy as np
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_weather_rebuild_2026_10_12'))
from rebuild import read,save,ts,quote,sq,fee,metrics,CITIES,TZ
OLD=P.parent/'kalshi_broad_review_2026_10_10';EXT=P.parent/'kalshi_weather_validation_2026_10_10'
REBUILD=P.parent/'kalshi_weather_rebuild_2026_10_12'
START=ts('2026-09-12T04:00:00Z');END=ts('2026-10-12T04:00:00Z');SPLIT=ts('2026-10-03T04:00:00Z')

def portfolio(candidates,friction):
    cash=100.;active=[];out=[];seen=set();daily=defaultdict(float);blocked=Counter();minimum=100.;maxrisk=0.
    for r in sorted(candidates,key=lambda r:(r['entered'],r['asset'],r['event'])):
        for a in active:
            if a['settled']<=r['entered']:
                cash+=5*int(a['won']);daily[date(a['settled'])]+=a['net']
        active=[a for a in active if a['settled']>r['entered']]
        if r['event'] in seen:continue
        cost=5*r['entry']+r['fee']+5*friction;reserved=sum(a['cost'] for a in active)
        why=None
        if any(a['asset']==r['asset'] for a in active):why='city_pending'
        elif len(active)>=5:why='contracts'
        elif reserved+cost>25+1e-9:why='exposure'
        elif daily[date(r['entered'])]-reserved-cost < -20-1e-9:why='daily_risk'
        elif cash-cost<2:why='cash'
        if why:blocked[why]+=1;continue
        a=dict(r,cost=cost,friction=friction,net=5*int(r['won'])-cost)
        cash-=cost;active.append(a);out.append(a);seen.add(r['event']);minimum=min(minimum,cash);maxrisk=max(maxrisk,reserved+cost)
    return out,dict(blocked=dict(blocked),min_cash=minimum,max_open_cost=maxrisk)

def date(t):return datetime.fromtimestamp(t,ZoneInfo('America/New_York')).date().isoformat()
def describe(rows,lo=START,hi=END):
    a=metrics(rows);days=(hi-lo)/86400;ds=defaultdict(float)
    for r in rows:ds[date(r['t'])]+=r['net']
    values=[ds[date(t)] for t in range(lo,hi,86400)];rng=np.random.default_rng(20261012)
    a.update(calendar_days=days,trades_per_day=len(rows)/days,day_bootstrap95=np.quantile(rng.choice(values,(3000,len(values))).sum(axis=1),[.025,.975]).tolist(),
        mean_probability=float(np.mean([r['model_p'] for r in rows])) if rows else None,
        mean_breakeven=float(np.mean([r['cost']/5 for r in rows])) if rows else None,
        daily_entries=dict(Counter(date(r['t']) for r in rows)))
    return a

def load():
    data=[];event_cache={};coverage={}
    for city in CITIES:
        d=EXT/'data'/city
        if not d.exists():d=OLD/'replays'/city
        new=REBUILD/'extension'/city
        ms=list({m['ticker']:m for m in read(d/'markets.json')+read(new/'markets.json')}.values())
        bars={}
        if (d/'bars.json.gz').exists():bars={int(k):tuple(v) for k,v in read(d/'bars.json.gz').items()}
        else:
            for f in d.glob('*_source.json.gz'):
                for b in read(f).get('live_data',{}).get('details',{}).get('timeseries',[]):
                    if isinstance(b.get('v'),dict) and b['v'].get('temp_f') is not None:
                        bars[int(b['t'])//1000]=(float(b['v']['temp_f']),b['v'].get('status'))
        for k,v in read(new/'bars.json.gz').items():
            k=int(k);v=tuple(v)
            if k in bars:assert bars[k]==v
            bars[k]=v
        grouped=defaultdict(list);cov=Counter()
        for m in ms:grouped[m['event_ticker']].append(m)
        for event,items in grouped.items():
            close=ts(items[0]['close_time'])
            if close>END or any(m.get('result') not in ('yes','no') or m.get('expiration_value') is None for m in items):continue
            final=float(items[0]['expiration_value']);settled=max(ts(m['settlement_ts']) for m in items)
            for m in items:assert (final>float(m['floor_strike']))==(m['result']=='yes') and m['strike_type']=='greater'
            cp=d/(event+'_candles.json.gz')
            if not cp.exists():cp=new/(event+'_candles.json.gz')
            cm={k:{c['end_period_ts']:c for c in v} for k,v in read(cp).items()} if cp.exists() else {}
            event_cache[event]={'items':items,'cm':cm,'asset':city,'close':close,'final':final,'settled':settled}
            cov['events']+=1
            for h in (30,15,5):
                t=close-h*60;last=t-600;keys=range(last-3600,last+1,60)
                if not all(k in bars for k in keys) or bars[last][1]!='normal':cov['missing_features']+=1;continue
                vals=[bars[k][0] for k in keys]
                data.append(dict(asset=city,event=event,t=t,close=close,settled=settled,horizon=h,final=final,temp=vals[-1],
                    delta15=vals[-1]-vals[-16],delta60=vals[-1]-vals[0],vol=float(np.std(np.diff(vals))),source_last=last))
                cov['forecast_rows']+=1
        coverage[city]=dict(cov)
    save(P/'coverage.json',coverage);save(P/'physical_features.json.gz',data)
    return data,event_cache

def features(r):
    hour=datetime.fromtimestamp(r['t'],ZoneInfo(TZ[CITIES.index(r['asset'])])).hour
    horizon=(r['horizon']+10)/60
    si=math.sin(hour*math.pi/12)*horizon;co=math.cos(hour*math.pi/12)*horizon
    x=[1]+[float(r['asset']==c) for c in CITIES[1:]]
    x += [horizon,r['delta15']*(r['horizon']+10)/15,r['delta60']*horizon,si,co,r['vol']*math.sqrt(r['horizon']+10)]
    x += [v*float(r['asset']==c) for c in CITIES[1:] for v in (si,co)]
    return x

def fit(rows):
    X=np.array([features(r) for r in rows]);y=np.array([r['final']-r['temp'] for r in rows])
    penalty=np.full(X.shape[1],10.);penalty[:4]=.1
    beta=np.linalg.solve(X.T@X+np.diag(penalty),X.T@y)
    assert np.max(abs((X.T@X+np.diag(penalty))@beta-X.T@y))<1e-6
    return beta

def prediction(r,kind,beta=None):
    if kind=='conditional':return r['temp']+float(np.dot(features(r),beta))
    if kind=='slope':return r['temp']+.5*r['delta15']*(r['horizon']+10)/15
    return r['temp']

def run():
    data,events=load();intents=defaultdict(list);fits={};forecasts=[]
    for w in range(START,END,604800):
        tr=[r for r in data if w-14*86400<=r['close']<w and r['settled']<w]
        first=[r for r in tr if r['close']<w-604800 and r['settled']<w-604800]
        calibrate=[r for r in tr if w-604800<=r['close']<w]
        cities=[c for c in CITIES if len({r['event'] for r in first if r['asset']==c})>=60 and len({r['event'] for r in calibrate if r['asset']==c})>=60]
        first=[r for r in first if r['asset'] in cities];tr=[r for r in tr if r['asset'] in cities];calibrate=[r for r in calibrate if r['asset'] in cities]
        if not tr:continue
        early=fit(first);beta=fit(tr)
        fits[str(w)]={'training_events':sorted({r['event'] for r in tr}),'training_last_settled':max(r['settled'] for r in tr),
            'calibration_events':sorted({r['event'] for r in calibrate}),'early_training_last_settled':max(r['settled'] for r in first),
            'early_beta':early.tolist(),'final_beta':beta.tolist(),'cities':cities}
        for kind in ('conditional','slope','persistence'):
            errors=defaultdict(list)
            for r in calibrate:errors[(r['asset'],r['horizon'])].append(r['final']-prediction(r,kind,early))
            for r in data:
                if r['asset'] not in cities or not w<=r['t']<min(w+604800,END):continue
                errs=errors[(r['asset'],r['horizon'])]
                if len(errs)<60:continue
                forecast=prediction(r,kind,beta);e=events[r['event']]
                forecasts.append(dict(r,kind=kind,forecast=forecast,calibration_end=w,n=len(errs)))
                choices=defaultdict(list)
                for m in e['items']:
                    cs=e['cm'].get(m['ticker'],{});q=quote(cs.get(r['t']))
                    if q is None or q[1]-q[0]>.02000001:continue
                    if sum(float(c.get('volume_fp') or 0) for t,c in cs.items() if r['t']-600<t<=r['t'])<5:continue
                    py=(1+sum(forecast+error>float(m['floor_strike']) for error in errs))/(len(errs)+2)
                    for blend in (.5,0.):
                        prob=(1-blend)*py+blend*sum(q)/2
                        for side,sp in [('yes',prob),('no',1-prob)]:
                            ask=sq(q,side)[1];edge=sp-ask-fee(ask,1)/5-.01
                            if not .4<=ask<.95 or edge<.03:continue
                            x=dict(r,kind=kind,blend=blend,forecast=forecast,model_p=sp,model_edge=edge,ticker=m['ticker'],side=side,
                                signal_ask=ask,cap=min(.95,ask+.01),won=m['result']==side,calibration_end=w,calibration_n=len(errs),
                                future={label:quote(cs.get(r['t']+60),phase) for label,phase in [('base','open'),('delay60','close')]})
                            choices[blend].append(x)
                for blend,ch in choices.items():
                    if ch:intents[f'{kind}/blend{blend}'].append(max(ch,key=lambda r:(r['model_edge'],-r['signal_ask'],r['ticker'])))
    results={};ledgers={}
    for name,rr in intents.items():
        for mode,friction in [('base',.01),('base',.02),('delay60',.01),('delay60',.02),('cap',.01)]:
            candidates=[];skips=Counter()
            for r in rr:
                q=r['future']['base' if mode=='cap' else mode]
                if q is None:skips['missing_quote']+=1;continue
                entry=max(r['signal_ask'],sq(q,r['side'])[1])
                if entry>r['cap']+1e-8:skips['cap']+=1;continue
                if mode=='cap':entry=r['cap']
                entered=r['t']+(60 if mode=='delay60' else 10)
                assert r['calibration_end']<=r['t'] and r['source_last']<=r['t']-600 and entered<r['close']<r['settled']
                candidates.append(dict({k:v for k,v in r.items() if k!='future'},entered=entered,entry=entry,fee=fee(entry,1)))
            ledger,risk=portfolio(candidates,friction);key=f'{name}/{mode}/cost{int(100*friction)}c'
            ledgers[key]=ledger;results[key]={'all':describe(ledger),'earlier':describe([r for r in ledger if r['t']<SPLIT],START,SPLIT),
                'later':describe([r for r in ledger if r['t']>=SPLIT],SPLIT,END),'cities':{c:metrics([r for r in ledger if r['asset']==c]) for c in CITIES},
                'intents':len(rr),'signal_events':len(set(r['event'] for r in rr)),'execution_skips':dict(skips),'risk':risk}
            print(key,len(ledger),results[key]['all']['net'],flush=True)
    save(P/'conditional_results.json',results);save(P/'conditional_ledgers.json.gz',ledgers);save(P/'conditional_intents.json.gz',dict(intents));save(P/'conditional_fits.json',fits);save(P/'forecasts.json.gz',forecasts)
    save(P/'run_receipt.json',{'completed':time.time(),'script_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'plan_hash':hashlib.sha256((P/'PLAN.txt').read_bytes()).hexdigest()})

if __name__=='__main__':run()
