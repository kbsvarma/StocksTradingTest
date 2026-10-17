"""Fixed two-model weather study. Public cached data only; no trading interface."""
from common import *
from replay_candidates import quote,sq,fee,metrics,portfolio,day
from collections import defaultdict,Counter
import math,statistics
CITIES=('KXTEMPNYCHS','KXTEMPMIAH','KXTEMPLAXHS','KXTEMPCHIHS')
SPLIT=START+7*86400

def forecast(bars,decision,close,model,lag):
 last=decision-lag*60;keys=range(last-3600,last+1,60)
 if not all(t in bars for t in keys):return None
 if bars[last][1]!='normal':return None
 values=[bars[t][0] for t in keys]
 base=values[-1]
 if model=='trend':
  ys=values[-30:];slope=sum((i-14.5)*(v-statistics.mean(ys)) for i,v in enumerate(ys))/sum((i-14.5)**2 for i in range(30))
  base+=slope*(close-last)/60
 return base

def main():
 results={};ledgers={};quality={};allrows=defaultdict(list);calibration={};integrity=[]
 for city in CITIES:
  d=P/'replays'/city
  if not (d/'receipt.json').exists() or not list(d.glob('*_source.json.gz')):continue
  ms=read(d/'markets.json');meta=read(d/'series.json');events=defaultdict(list)
  for m in ms:events[m['event_ticker']].append(m)
  bars={};bad=set();q=Counter()
  for path in sorted(d.glob('*_source.json.gz')):
   raw=read(path)
   for p in raw['live_data']['details']['timeseries']:
    t=int(p['t'])//1000;v=p['v'];status=v.get('status');value=v.get('temp_f')
    if value is None or t%60 or status not in ('normal','degraded'):continue
    pair=(float(value),status)
    if t in bars and bars[t]!=pair:bad.add(t)
    bars[t]=pair
  q.update(source_points=len(bars),conflicting_points=len(bad),events=len(events),source_charts=len(list(d.glob('*_source.json.gz'))))
  for t in bad:bars.pop(t,None)
  event_data=[]
  for event,items in sorted(events.items(),key=lambda x:x[1][0]['close_time']):
   m0=items[0];cl=ts(m0['close_time']);final=float(m0['expiration_value']);checks=[t for t in bars if cl-3600<=t<=cl]
   last=max(checks) if checks else None
   delta=abs(bars[last][0]-final) if last is not None else None
   integrity.append(dict(city=city,event=event,final=final,source_last=last,source_value=bars[last][0] if last is not None else None,delta=delta))
   q['settlement_checked']+=last is not None;q['settlement_mismatch']+=delta is not None and delta>.011
   cp=d/(event+'_candles.json.gz');cmap=read(cp) if cp.exists() else {};cs={k:{c['end_period_ts']:c for c in v} for k,v in cmap.items()}
   for m in items:
    assert m['strike_type']=='greater' and float(m['notional_value_dollars'])==1
    if (final>float(m['floor_strike']))!=(m['result']=='yes'):raise ValueError('settlement result inconsistency')
   event_data.append((event,items,cl,final,cs))
  for lag in (10,6):
   for model in ('persistence','trend'):
    errs={h:[] for h in (30,15,5)}
    for event,items,cl,final,cs in event_data:
     # Calibration labels must have been settled before the evaluation split.
     if not START<=cl<SPLIT or max(ts(m['settlement_ts']) for m in items)>=SPLIT:continue
     for h in errs:
      pred=forecast(bars,cl-h*60,cl,model,lag)
      if pred is not None:errs[h].append(final-pred)
    ck=f'{city}/{model}/lag{lag}';calibration[ck]={h:{'n':len(e),'mean_error':statistics.mean(e) if e else None,'mae':statistics.mean(abs(x) for x in e) if e else None} for h,e in errs.items()}
    for mode,stress,label in [('opening',.01,'fees1c'),('opening',.02,'fees2c'),('delay60',.01,'delay1c')]:
     rows=[];cnt=Counter()
     for event,items,cl,final,csmap in event_data:
      if not SPLIT<=cl<END:continue
      cnt['test_events']+=1
      for h in (30,15,5):
       t=cl-h*60;pred=forecast(bars,t,cl,model,lag)
       if pred is None:cnt['source_window_missing']+=1;continue
       if len(errs[h])<60:cnt['insufficient_calibration']+=1;continue
       choices=[]
       for m in items:
        cs=csmap.get(m['ticker'],{});pair=quote(cs.get(t))
        if pair is None:continue
        if sum(float(c.get('volume_fp') or 0) for tt,c in cs.items() if t-600<tt<=t)<5:continue
        py=(sum(pred+e>float(m['floor_strike']) for e in errs[h])+1)/(len(errs[h])+2)
        for side,p in [('yes',py),('no',1-py)]:
         bid,ask=sq(pair,side)
         if not .60<=ask<.95 or ask-bid>.02000001:continue
         edge=p-ask-fee(ask,meta['fee_multiplier'])/5-.01
         if edge<.02:continue
         nxt=quote(cs.get(t+60),'close' if mode=='delay60' else 'open')
         if nxt is None:continue
         cap=min(ask+.01,.95);entry=max(ask,sq(nxt,side)[1])
         if entry>cap+1e-8:continue
         x=dict(category='weather',profile=model,series=city,asset=city,event=event,ticker=m['ticker'],side=side,t=t,entered=t+60 if mode=='delay60' else t+3,settled=ts(m['settlement_ts']),close=cl,entry=entry,ask=ask,cap=cap,fee=fee(entry,meta['fee_multiplier']),won=m['result']==side,model_p=p,model_edge=edge,forecast=pred,lag=lag,horizon=h,calibration_n=len(errs[h]),source_last=t-lag*60)
         assert x['entered']<cl and x['source_last']<=t-360 and x['settled']>cl
         choices.append(x)
       if choices:rows.append(max(choices,key=lambda x:(x['model_edge'],-x['entry'],x['ticker'])))
     key=ck+'/'+label;acc,blocked=portfolio(rows,stress);unc,_=portfolio(rows,stress,False)
     results[key]={'portfolio':metrics(acc),'unconstrained':metrics(unc),'blocked':blocked,'coverage':dict(cnt)};ledgers[key]=acc;allrows[(model,lag,label)]+=rows
     print(key,results[key]['portfolio'],flush=True)
  quality[city]=dict(q)
 for (model,lag,label),rows in allrows.items():
  stress=.02 if label=='fees2c' else .01;key=f'ALL/{model}/lag{lag}/{label}';acc,blocked=portfolio(rows,stress);unc,_=portfolio(rows,stress,False)
  results[key]={'portfolio':metrics(acc),'unconstrained':metrics(unc),'blocked':blocked};ledgers[key]=acc
 save(P/'weather_model_candidates.json.gz',{'/'.join(map(str,k)):v for k,v in allrows.items()})
 save(P/'weather_model_results.json',results);save(P/'weather_model_ledgers.json.gz',ledgers);save(P/'weather_model_calibration.json',calibration);save(P/'weather_model_quality.json',quality);save(P/'weather_model_integrity.json',integrity)
if __name__=='__main__':main()
