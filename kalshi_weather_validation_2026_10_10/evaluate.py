"""Causal chronological weather replay with predeclared robustness comparisons."""
from pathlib import Path
import sys,math,statistics,json,random,hashlib,bisect
from collections import defaultdict,Counter
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_broad_review_2026_10_10'
sys.path.insert(0,str(OLD))
from common import read,save,ts
from replay_candidates import quote,sq,fee,metrics,day
from weather_model import forecast
ANCHOR=ts('2026-08-15T04:00:00Z');END=ts('2026-10-10T04:00:00Z');SELECTION=ts('2026-10-03T04:00:00Z');WEEK=7*86400
CITIES=['KXTEMPMIAH','KXTEMPCHIHS','KXTEMPLAXHS']

def weekly(t):return ANCHOR+(t-ANCHOR)//WEEK*WEEK

def port(rows,stress=.01,extra_settle=0,city_lock=True):
    cash=100.;active=[];accepted=[];seen=set();realized=defaultdict(float);blocked=Counter();peak_reserved=0;min_cash=cash;audit=[]
    for r in sorted(rows,key=lambda x:(x['entered'],x['asset'],x['event'],x['entry'],x['ticker'])):
        for a in sorted(active,key=lambda a:a['released']):
            if a['released']<=r['entered']:
                cash+=5*int(a['won']);realized[day(a['released'])]+=a['net']
        active=[a for a in active if a['released']>r['entered']]
        if r['event'] in seen:continue
        reserve=5*max(.95,r['entry']+.02);risk=sum(a['reserve'] for a in active)+reserve;cost=5*r['entry']+r['fee']+5*stress
        reason=''
        if city_lock and any(a['asset']==r['asset'] for a in active):reason='city_pending'
        elif len(active)>=5:reason='contracts'
        elif risk>25+1e-8:reason='exposure'
        elif realized[day(r['entered'])]-risk < -20-1e-8:reason='daily_loss_including_open'
        elif cash-cost<2:reason='cash'
        if reason:blocked[reason]+=1;continue
        out=dict(r,net=5*(int(r['won'])-r['entry'])-r['fee']-5*stress,reserve=reserve,released=r['settled']+extra_settle)
        cash-=cost;active.append(out);accepted.append(out);seen.add(r['event']);peak_reserved=max(peak_reserved,risk);min_cash=min(min_cash,cash)
        assert len(active)<=5 and risk<=25+1e-8 and realized[day(r['entered'])]-risk>=-20-1e-8 and cash>=2-1e-8
    return accepted,{'blocked':dict(blocked),'min_free_cash':min_cash,'peak_reserved':peak_reserved}

def desc(rows,end=END):
    rows=[dict(r,settled=r.get('released',r['settled'])) for r in rows]
    m=metrics(rows)
    if not rows:return m
    n=len(rows);w=m['wins']/n;seq=sorted(rows,key=lambda r:(r['settled'],r['event']));streak=cur=0
    for r in seq:cur=0 if r['won'] else cur+1;streak=max(streak,cur)
    weeks={day(k):metrics([r for r in rows if weekly(r['t'])==k]) for k in sorted({weekly(r['t']) for r in rows})}
    dates=[day(t) for t in range(weekly(min(r['t'] for r in rows)),end,86400)]
    entry_daily=defaultdict(float)
    for r in rows:entry_daily[day(r['t'])]+=r['net']
    ds=[entry_daily.get(d,0) for d in dates];rng=random.Random(20261010);boot=sorted(sum(rng.choice(ds) for _ in ds) for __ in range(5000))
    m.update(weeks=weeks,calendar_days=len(dates),trades_per_day=n/len(dates),win_rate=w,mean_model_probability=statistics.mean(r['model_p'] for r in rows),mean_price=statistics.mean(r['entry'] for r in rows),break_even=w-m['net']/(5*n),worst_day=min(m['daily'].values()),without_best_day=m['net']-max(m['daily'].values()),loss_streak=streak,profit_factor=sum(max(0,r['net']) for r in rows)/max(1e-12,-sum(min(0,r['net']) for r in rows)),entry_daily=dict(entry_daily),day_bootstrap95=[boot[125],boot[4875]],brier=statistics.mean((r['model_p']-int(r['won']))**2 for r in rows),horizons={str(h):metrics([r for r in rows if r['horizon']==h]) for h in (30,15,5)},probability_bins={f'{lo:.1f}-{hi:.1f}':{'n':len(rr),'predicted':statistics.mean(r['model_p'] for r in rr),'observed':statistics.mean(int(r['won']) for r in rr)} for lo,hi in [(.6,.8),(.8,.9),(.9,1.00001)] if (rr:=[r for r in rows if lo<=r['model_p']<hi])})
    return m

def main():
    pools=defaultdict(list);coverage={};calibration={};issues=[];input_checks=Counter()
    for city in CITIES:
        d=P/'data'/city;assert (d/'receipt.json').exists()
        ms=read(d/'markets.json');meta=read(d/'series.json');bars={int(t):tuple(v) for t,v in read(d/'bars.json.gz').items()};byevent=defaultdict(list)
        for m in ms:byevent[m['event_ticker']].append(m)
        cov=Counter(events=len(byevent),markets=len(ms));data=[]
        for event,items in sorted(byevent.items(),key=lambda x:x[1][0]['close_time']):
            cl=ts(items[0]['close_time']);cp=d/(event+'_candles.json.gz')
            if any(m.get('result') not in ('yes','no') or not m.get('expiration_value') or not m.get('settlement_ts') for m in items):cov['nonbinary_or_unsettled']+=1;continue
            final=float(items[0]['expiration_value']);available=[t for t in range(cl-3600,cl+1,60) if t in bars]
            if available:
                delta=abs(bars[max(available)][0]-final);cov['source_terminal_checked']+=1
                if delta>.011:issues.append({'type':'terminal_mismatch','city':city,'event':event,'delta':delta});continue
            else:cov['terminal_source_missing']+=1
            for m in items:
                assert m['strike_type']=='greater' and float(m['notional_value_dollars'])==1
                assert (final>float(m['floor_strike']))==(m['result']=='yes');input_checks['outcome']+=1
            cm=read(cp) if cp.exists() else {};csmap={k:{c['end_period_ts']:c for c in v} for k,v in cm.items()}
            for k,vs in cm.items():assert len(csmap[k])==len(vs);input_checks['unique_candles']+=1
            if not cm:cov['candle_file_missing']+=1
            data.append((event,items,cl,final,csmap))
        earliest=read(d/'receipt.json')['start'];coverage[city]=dict(cov)
        for lookback,lag in [(7,10),(14,10),(7,6),(7,15)]:
            variant=f'w{lookback}_lag{lag}'
            for w in range(ANCHOR+WEEK,END,WEEK):
                if w-lookback*86400<earliest:continue
                errs={h:[] for h in (30,15,5)};ids={h:[] for h in errs}
                for event,items,cl,final,cs in data:
                    if not w-lookback*86400<=cl<w or max(ts(m['settlement_ts']) for m in items)>=w:continue
                    for h in errs:
                        pred=forecast(bars,cl-h*60,cl,'persistence',lag)
                        if pred is not None:errs[h].append(final-pred);ids[h].append(event)
                minimum=60 if lookback==7 else 120
                calibration[f'{city}/{variant}/{day(w)}']={str(h):{'n':len(es),'eligible':len(es)>=minimum,'mean':statistics.mean(es) if es else None,'mae':statistics.mean(abs(e) for e in es) if es else None,'events':ids[h]} for h,es in errs.items()}
                for h in errs:errs[h].sort()
                for event,items,cl,final,csmap in data:
                    if not w<=cl<=w+WEEK:continue
                    for h in (30,15,5):
                        t=cl-h*60
                        if not w<=t<w+WEEK:continue
                        pred=forecast(bars,t,cl,'persistence',lag)
                        if pred is None or len(errs[h])<minimum:continue
                        choices=[]
                        for m in items:
                            if not ts(m['open_time'])<=t<cl:continue
                            cs=csmap.get(m['ticker'],{});q=quote(cs.get(t))
                            if q is None or sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
                            # Match original addition-based strict comparison, including float behavior.
                            py=(sum(pred+e>float(m['floor_strike']) for e in errs[h])+1)/(len(errs[h])+2)
                            for side,p in [('yes',py),('no',1-py)]:
                                bid,ask=sq(q,side)
                                if not .60<=ask<.95 or ask-bid>.02000001:continue
                                edge=p-ask-fee(ask,meta['fee_multiplier'])/5-.01
                                if edge<.02:continue
                                choices.append(dict(category='weather',asset=city,series=city,event=event,ticker=m['ticker'],side=side,t=t,close=cl,settled=ts(m['settlement_ts']),ask=ask,cap=min(ask+.01,.95),model_p=p,model_edge=edge,forecast=pred,source_last=t-lag*60,horizon=h,calibration_n=len(errs[h]),calibration_end=w,variant=variant,won=m['result']==side,final=final,strike=float(m['floor_strike'])))
                        if not choices:continue
                        pick=max(choices,key=lambda x:(x['model_edge'],-x['ask'],x['ticker']))
                        for delay,phase,label in [(60,'open','base'),(60,'close','delay60'),(120,'close','delay120')]:
                            q=quote(csmap[pick['ticker']].get(t+delay),phase)
                            if q is None:continue
                            ep=max(pick['ask'],sq(q,pick['side'])[1])
                            if ep>pick['cap']+1e-8:continue
                            r=dict(pick,entry=ep,fee=fee(ep,meta['fee_multiplier']),entered=t+3 if label=='base' else t+delay)
                            assert r['calibration_end']<=t and r['source_last']<=t-lag*60 and r['entered']<cl<r['settled']
                            pools[f'{variant}/{label}'].append(r)
                            if label=='base':pools[f'{variant}/cap'].append(dict(r,entry=r['cap'],fee=fee(r['cap'],meta['fee_multiplier'])))
        print('PREPARED',city,dict(cov),flush=True)
    save(P/'coverage.json',{'cities':coverage,'issues':issues,'checks':dict(input_checks)});save(P/'calibration.json',calibration)
    if issues:raise ValueError('Terminal source mismatch; inspect coverage before claiming a replay')
    results={};ledgers={}
    scenarios=[]
    for key in pools:
        scenarios.append((key,.01,0,True))
        if key.startswith('w7_lag10/'):
            scenarios.extend([(key,cost,0,True) for cost in (.02,.03,.05)])
        if key=='w7_lag10/base':
            scenarios.extend([(key,.01,delay,True) for delay in (900,3600)])
            scenarios.append((key,.01,0,False))
    for key,cost,delay,lock in scenarios:
        name=f'{key}/cost{int(cost*100)}c/release{delay}/lock{int(lock)}';rows=pools[key];acc,state=port(rows,cost,delay,lock)
        results[name]={'ALL':desc(acc),'cities':{c:desc([r for r in acc if r['asset']==c]) for c in CITIES},'standalone_cities':{c:desc(port([r for r in rows if r['asset']==c],cost,delay,lock)[0]) for c in CITIES},'state':state,'earlier_weeks':desc([r for r in acc if r['t']<SELECTION],SELECTION),'selection_week':desc([r for r in acc if r['t']>=SELECTION])}
        ledgers[name]=acc
        print('RESULT',name,results[name]['ALL']['trades'],results[name]['ALL']['net'],flush=True)
    save(P/'candidates.json.gz',dict(pools));save(P/'results.json',results);save(P/'ledgers.json.gz',ledgers)

if __name__=='__main__':main()
