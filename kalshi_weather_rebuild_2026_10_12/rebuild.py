"""Read-only cached weather study; numpy only; frozen plan in PLAN.txt."""
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import sys, math, json, hashlib, time
import numpy as np

P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_broad_review_2026_10_10'
EXT=P.parent/'kalshi_weather_validation_2026_10_10'
sys.path.insert(0,str(OLD))
from common import read,save,ts
from replay_candidates import quote,sq,fee,metrics,day
sys.path.insert(0,str(EXT))
from evaluate import port
CITIES=['KXTEMPMIAH','KXTEMPCHIHS','KXTEMPLAXHS','KXTEMPNYCHS']
TZ=['America/New_York','America/Chicago','America/Los_Angeles','America/New_York']
ANCHOR=ts('2026-08-15T04:00:00Z');WEEK=604800
END=ts('2026-10-10T04:00:00Z')
def week(t):return ANCHOR+(t-ANCHOR)//WEEK*WEEK
def sigmoid(z):return 1/(1+np.exp(-np.clip(z,-40,40)))

def features(r,physical):
    mid=min(.99,max(.01,(r['bid']+r['ask'])/2));l=math.log(mid/(1-mid))
    hour=datetime.fromtimestamp(r['t'],ZoneInfo(TZ[CITIES.index(r['asset'])])).hour
    h=r['horizon']/30
    x=[1,l/3]+[float(r['asset']==c) for c in CITIES[1:]]+[h,math.sin(hour*math.pi/12),math.cos(hour*math.pi/12)]
    if physical:
        gap=r['temp']-r['strike'];g=math.tanh(gap/3)
        x += [g,math.tanh(gap),math.tanh(r['delta15']/2),math.tanh(r['delta60']/3),
              math.tanh(r['vol']),g*h,g*math.tanh(r['vol'])]
        x += [g*float(r['asset']==c) for c in CITIES[1:]]
    return x,l

def fit(rows,physical):
    xx=[features(r,physical) for r in rows]
    X=np.asarray([x[0] for x in xx]);offset=np.asarray([x[1] for x in xx])
    y=np.asarray([r['yes'] for r in rows],dtype=float)
    counts=Counter(r['event'] for r in rows);w=np.array([1/counts[r['event']] for r in rows])
    penalty=np.full(X.shape[1],10.);penalty[0]=.01
    b=np.zeros(X.shape[1])
    def objective(v):
        z=offset+X@v
        return np.sum(w*(np.logaddexp(0,z)-y*z))+.5*np.sum(penalty*v*v)
    for step in range(60):
        prob=sigmoid(offset+X@b)
        grad=X.T@(w*(prob-y))+penalty*b
        H=X.T@((w*prob*(1-prob))[:,None]*X)+np.diag(penalty)
        direction=np.linalg.solve(H,grad);scale=1.;before=objective(b)
        while scale>1e-8 and objective(b-scale*direction)>before+1e-10:scale*=.5
        b-=scale*direction
        if max(abs(grad))<1e-7:break
    assert np.max(np.abs(X.T@(w*(sigmoid(offset+X@b)-y))+penalty*b))<1e-5
    return b,{'iterations':step+1,'objective':float(objective(b)), 'events':len(counts),'rows':len(rows)}

def load_city(city,lag):
    d=EXT/'data'/city
    if not d.exists():d=OLD/'replays'/city
    ms=read(d/'markets.json');meta=read(d/'series.json');bars={};conflicts=set()
    if (d/'bars.json.gz').exists():bars={int(t):tuple(v) for t,v in read(d/'bars.json.gz').items()}
    else:
        for p in d.glob('*_source.json.gz'):
            for a in read(p).get('live_data',{}).get('details',{}).get('timeseries',[]):
                if not isinstance(a.get('v'),dict) or a['v'].get('temp_f') is None:continue
                t=int(a['t'])//1000;v=(float(a['v']['temp_f']),a['v'].get('status'))
                if t in bars and bars[t]!=v:conflicts.add(t)
                bars[t]=v
        for t in conflicts:bars.pop(t,None)
    newer=P/'extension'/city
    if (newer/'markets.json').exists():
        ms=list({m['ticker']:m for m in ms+read(newer/'markets.json')}.values())
        for t,v in read(newer/'bars.json.gz').items():
            t=int(t);v=tuple(v)
            if t in bars and bars[t]!=v:raise ValueError('Archive source revision')
            bars[t]=v
    grouped=defaultdict(list)
    for m in ms:grouped[m['event_ticker']].append(m)
    out=[];coverage=Counter(events=len(grouped),source_minutes=len(bars),conflicts=len(conflicts))
    for event,items in sorted(grouped.items()):
        if any(m.get('result') not in ('yes','no') or m.get('expiration_value') is None or not m.get('settlement_ts') for m in items):
            coverage['nonbinary_unsettled']+=1;continue
        cl=ts(items[0]['close_time']);final=float(items[0]['expiration_value'])
        cp=d/(event+'_candles.json.gz')
        if not cp.exists():cp=newer/(event+'_candles.json.gz')
        if not cp.exists():coverage['missing_candles']+=1;continue
        cm=read(cp);csmap={k:{c['end_period_ts']:c for c in v} for k,v in cm.items()}
        latest=max((t for t in range(cl-3600,cl+1,60) if t in bars),default=None)
        if latest is not None:
            assert abs(bars[latest][0]-final)<=.011,(city,event,'final mismatch')
            coverage['terminal_match']+=1
        for m in items:
            assert m['strike_type']=='greater' and (final>float(m['floor_strike']))==(m['result']=='yes')
        for h in [30,15,5]:
            t=cl-h*60;last=t-lag*60;keys=list(range(last-3600,last+1,60))
            if not all(k in bars for k in keys) or bars[last][1]!='normal':coverage['missing_source_horizon']+=1;continue
            vals=[bars[k][0] for k in keys]
            for m in items:
                if not ts(m['open_time'])<=t<cl:continue
                cs=csmap.get(m['ticker'],{});q=quote(cs.get(t))
                if q is None or q[1]-q[0]>.02000001 or not .01<sum(q)/2<.99:continue
                volume=sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)
                row=dict(asset=city,event=event,ticker=m['ticker'],t=t,close=cl,settled=ts(m['settlement_ts']),
                    strike=float(m['floor_strike']),yes=int(m['result']=='yes'),final=final,
                    bid=q[0],ask=q[1],volume=volume,horizon=h,temp=vals[-1],delta15=vals[-1]-vals[-16],
                    delta60=vals[-1]-vals[0],vol=float(np.std(np.diff(vals))),source_last=last,
                    fee_multiplier=meta['fee_multiplier'],future={})
                for name,delay,phase in [('base',60,'open'),('delay60',60,'close'),('delay120',120,'close')]:
                    row['future'][name]=quote(cs.get(t+delay),phase)
                out.append(row)
    coverage['rows']=len(out)
    return out,dict(coverage)

def summarize(rows,lo,hi):
    result=metrics(rows);days=(hi-lo)/86400
    ds=defaultdict(float)
    for r in rows:ds[day(r['t'])]+=r['net']
    values=np.array([ds[day(t)] for t in range(lo,hi,86400)])
    rng=np.random.default_rng(20261012)
    boot=np.sum(rng.choice(values,size=(3000,len(values))),axis=1) if len(values) else [0]
    result.update(calendar_days=days,trades_per_day=len(rows)/days if days else 0,
        day_bootstrap95=np.quantile(boot,[.025,.975]).tolist(),
        mean_model_p=float(np.mean([r['model_p'] for r in rows])) if rows else None,
        mean_breakeven=float(np.mean([r['entry']+r['fee']/5+r['stress'] for r in rows])) if rows else None,
        brier=float(np.mean([(r['model_p']-int(r['won']))**2 for r in rows])) if rows else None)
    return result

def main():
    global END
    if '--extension' in sys.argv:END=ts('2026-10-12T04:00:00Z')
    all_results={};ledgers={};fit_audit={};all_signals={};coverage={};calibration={}
    for lag in [10,6,15]:
        data=[]
        for city in CITIES:
            rr,cov=load_city(city,lag);data+=rr;coverage[f'{city}/lag{lag}']=cov
        save(P/f'dataset_lag{lag}.json.gz',data)
        for physical in [False,True]:
            model='weather_market' if physical else 'market_only'
            if lag!=10 and not physical:continue
            predictions=[];eligible_windows={}
            for w in range(ANCHOR+WEEK,END,WEEK):
                train=[r for r in data if w-14*86400<=r['close']<w and r['settled']<w]
                city_events={c:len({r['event'] for r in train if r['asset']==c}) for c in CITIES}
                eligible=[c for c in CITIES if city_events[c]>=60]
                if len({r['event'] for r in train})<200:continue
                test=[r for r in data if w<=r['t']<min(w+WEEK,END) and r['asset'] in eligible]
                if not test:continue
                b,info=fit(train,physical);key=f'{model}/lag{lag}/{day(w)}'
                fit_audit[key]={**info,'coefficients':b.tolist(),'city_events':city_events,'last_label':max(r['settled'] for r in train),
                    'training_events':sorted({r['event'] for r in train})}
                assert max(r['settled'] for r in train)<w
                for c in eligible:eligible_windows.setdefault(c,w)
                for r in test:
                    x,offset=features(r,physical);p=float(sigmoid(offset+np.dot(x,b)))
                    predictions.append(dict(r,predicted=p,calibration_end=w))
            save(P/f'predictions_{model}_lag{lag}.json.gz',predictions)
            if not predictions:continue
            lo=min(eligible_windows.values());groups=defaultdict(list)
            for r in predictions:groups[(r['event'],r['t'])].append(r)
            intents=[]
            for (event,t),rows in sorted(groups.items()):
                options=[]
                for r in rows:
                    if r['volume']<5:continue
                    for side in ['yes','no']:
                        bid,ask=sq((r['bid'],r['ask']),side);p=r['predicted'] if side=='yes' else 1-r['predicted']
                        edge=p-ask-fee(ask,r['fee_multiplier'])/5-.01
                        if not .40<=ask<.95 or edge<.03:continue
                        options.append(dict(r,side=side,signal_ask=ask,model_p=p,model_edge=edge,cap=min(ask+.01,.95),won=bool(r['yes'])==(side=='yes')))
                if options:intents.append(max(options,key=lambda r:(r['model_edge'],-r['signal_ask'],r['ticker'])))
            all_signals[f'{model}/lag{lag}']=intents
            for mode,stress,round_cent in [('base',.01,False),('base',.02,False),('base',.03,False),('delay60',.01,False),('delay60',.02,False),('delay120',.01,False),('cap',.01,False),('base',.01,True)]:
                candidates=[];skips=Counter()
                for r in intents:
                    future=r['future']['base' if mode=='cap' else mode]
                    if future is None:skips['missing_quote']+=1;continue
                    actual=sq(future,r['side'])[1];entry=max(r['signal_ask'],actual)
                    if entry>r['cap']+1e-8:skips['above_cap']+=1;continue
                    if mode=='cap':entry=r['cap']
                    f=fee(entry,r['fee_multiplier'])
                    if round_cent:f=math.ceil((f-1e-10)*100)/100
                    entered=r['t']+({'base':10,'cap':10,'delay60':60,'delay120':120}[mode])
                    assert r['source_last']<r['t']<entered<r['close']<r['settled']
                    candidates.append({k:v for k,v in dict(r,entry=entry,entered=entered,fee=f,stress=stress,ask=r['signal_ask']).items() if k!='future'})
                acc,risk=port(candidates,stress)
                key=f'{model}/lag{lag}/{mode}/cost{int(stress*100)}c/cent{int(round_cent)}'
                ledgers[key]=acc
                common_start=ts('2026-09-19T04:00:00Z')
                result={'all':summarize(acc,lo,END),'risk':risk,'signals':len(intents),'signal_events':len({r['event'] for r in intents}),
                    'execution_candidates':len(candidates),'execution_skips':dict(skips),
                    'cities':{c:summarize([r for r in acc if r['asset']==c],eligible_windows[c],END) for c in eligible_windows},
                    'weeks':{day(w):metrics([r for r in acc if week(r['t'])==w]) for w in sorted({week(r['t']) for r in predictions})},
                    'common_3city_period':summarize([r for r in acc if r['t']>=common_start and r['asset']!=CITIES[3]],common_start,END)}
                if '--extension' in sys.argv:result['new_oct10_11']=summarize([r for r in acc if r['t']>=ts('2026-10-10T04:00:00Z')],ts('2026-10-10T04:00:00Z'),END)
                all_results[key]=result
                print(key,json.dumps({k:v for k,v in result['all'].items() if k not in ('daily',)}),flush=True)
            for city in CITIES:
                rr=[r for r in predictions if r['asset']==city]
                if not rr:continue
                calibration[f'{model}/lag{lag}/{city}']={'rows':len(rr),'events':len({r['event'] for r in rr}),
                    'model_brier':float(np.mean([(r['predicted']-r['yes'])**2 for r in rr])),
                    'market_brier':float(np.mean([((r['bid']+r['ask'])/2-r['yes'])**2 for r in rr]))}
    save(P/'results.json',all_results);save(P/'ledgers.json.gz',ledgers);save(P/'signals.json.gz',all_signals)
    save(P/'fit_audit.json',fit_audit);save(P/'coverage.json',coverage);save(P/'calibration.json',calibration)
    save(P/'run_receipt.json',{'completed_ts':time.time(),'end':END,'plan_sha256':hashlib.sha256((P/'PLAN.txt').read_bytes()).hexdigest(),
        'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'numpy':np.__version__})

if __name__=='__main__':main()
