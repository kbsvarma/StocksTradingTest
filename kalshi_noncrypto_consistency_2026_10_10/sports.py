from study import *
from pathlib import Path
START=ts('2026-08-15T04:00:00Z');END=ts('2026-10-10T04:00:00Z');SPLIT=ts('2026-09-12T04:00:00Z')
PROFILES=('pregame70','pregame55','momentum','held95','held97')
def seed(m,s,t,side,cs,mile,mult):
 q=quote(cs.get(t),side)
 if q is None:return None
 b,a=q
 if not 0<a<.99 or a-b>.02000001:return None
 if cs[t].get('_volume10',0)<5:return None
 cl=ts(m['close_time']);end=ts(mile['end_date']) if mile.get('end_date') else cl
 return dict(kind='sports',asset=s,event=m['event_ticker'],ticker=m['ticker'],side=side,t=t,ask=a,bid=b,mid=(a+b)/2,close=cl,game_end=end,cap=min(.99,a+.01),_cs=cs,_market=m,_mult=mult)
def event_signals(ms,cmap,s,mile,mult):
 csmap={m['ticker']:{int(c['end_period_ts']):c for c in cmap.get(m['ticker'],[])} for m in ms};start=ts(mile['start_date']);end=ts(mile['end_date']) if mile.get('end_date') else min(ts(m['close_time']) for m in ms)
 
 for cs in csmap.values():
  for t,c in cs.items():c['_volume10']=sum(float(cc.get('volume_fp') or 0) for tt,cc in cs.items() if t-600<tt<=t)
 output=defaultdict(list);pre=((start-1800)//60)*60;times=sorted({t for cs in csmap.values() for t in cs if start<=t<min(end,start+6*3600)})
 for profile in PROFILES+('calibration_sample',):
  for t in ([pre] if profile.startswith('pregame') or profile in ('momentum','calibration_sample') else times):
   choices=[]
   for m in ms:
    cs=csmap[m['ticker']]
    for side in ('yes','no'):
     x=seed(m,s,t,side,cs,mile,mult)
     if not x:continue
     a=x['ask'];mid=x['mid'];good=False
     if profile=='pregame70':good=.70<=a<.90 and mid>.5
     elif profile=='pregame55':good=.55<=a<.80 and mid>.5
     elif profile in ('momentum','calibration_sample'):
      good=.55<=a<(.90 if profile=='momentum' else .98) and mid>.5
      if profile=='momentum':
       old=quote(cs.get(t-1800),side);good=good and old is not None and mid-sum(old)/2>=.03-1e-8
     else:
      lo,hi,hold=(.95,.98,5) if profile=='held95' else (.97,.99,10);good=True
      for back in range(hold):
       q=quote(cs.get(t-back*60),side)
       if q is None or not lo<=q[1]<hi or q[1]-q[0]>.02000001:good=False;break
     if good:choices.append(x)
   if choices:
    x=min(choices,key=lambda x:(x['ask'],x['ticker'],x['side']));x['profile']=profile;output[profile].append(x)
 return output

def model(samples):
 out=[];cal=[]
 for boundary in range(START,END,7*86400):
  train=[x for x in samples if boundary-28*86400<=x['t']<boundary and x['_market'].get('settlement_ts') and ts(x['_market']['settlement_ts'])<boundary and x['_market'].get('result') in ('yes','no')]
  counts=Counter(x['asset'] for x in train);eligible={s for s,n in counts.items() if n>=20};train=[x for x in train if x['asset'] in eligible]
  if len(train)<100 or len(eligible)<2:cal.append({'boundary':boundary,'status':'insufficient','n':len(train),'leagues':dict(counts)});continue
  bands=(.5,.65,.80,.90,.99);pars=[]
  for lo,hi in zip(bands,bands[1:]):
   ss=[x for x in train if lo<=x['mid']<hi];p=(sum(payout(x['_market'],x['side']) for x in ss)+20*statistics.mean(x['mid'] for x in ss))/(len(ss)+20) if ss else None
   pars.append({'lo':lo,'hi':hi,'n':len(ss),'p':p})
  cal.append({'boundary':boundary,'status':'ready','n':len(train),'leagues':dict(counts),'parameters':pars,'latest_training_settlement':max(ts(x['_market']['settlement_ts']) for x in train)})
  for x in samples:
   if not boundary<=x['t']<boundary+7*86400 or x['asset'] not in eligible:continue
   par=next((r for r in pars if r['lo']<=x['mid']<r['hi']),None)
   if not par or par['n']<30:continue
   edge=par['p']-x['ask']-fee(x['ask'],x['_mult'])/5-.01
   if edge>=.03:out.append(dict(x,profile='pooled_calibration',probability=par['p'],edge=edge,calibration_boundary=boundary))
 save(P/'sports_calibration.json',cal);return out

def main():
 signals=defaultdict(list);coverage={}
 for d in sorted((P/'sports').iterdir()):
  if not (d/'receipt.json').exists():continue
  ms=read(d/'markets.json');mult=read(d/'series.json')['fee_multiplier'];events=defaultdict(list);ct=Counter()
  for m in ms:events[m['event_ticker']].append(m)
  for et,items in sorted(events.items()):
   ct['events']+=1;mp=d/(et+'_milestone.json');cp=d/(et+'_candles.json.gz')
   if not mp.exists() or not cp.exists():ct['missing_files']+=1;continue
   mile=read(mp);start=ts(mile['start_date'])
   if not START<=start<END:ct['start_outside']+=1;continue
   if not mile.get('end_date'):ct['missing_game_end']+=1
   if any(m.get('result') not in ('yes','no') for m in items):ct['scalar_or_unresolved_events']+=1
   xs=event_signals(items,read(cp),d.name,mile,mult)
   for prof,rr in xs.items():signals[prof]+=rr;ct[prof+'_candidate_minutes']+=len(rr)
  coverage[d.name]=dict(ct);print('BUILT',d.name,dict(ct),flush=True)
 signals['pooled_calibration']=model(signals['calibration_sample']);results={};ledgers={};rejects={}
 for prof in PROFILES+('pooled_calibration',):
  for label,stress,mode in SCENARIOS:
   rows=[y for x in signals[prof] if (y:=execute(x,mode)) is not None];rejects[prof+'/'+label]={'signal_minutes':len(signals[prof]),'execution_eligible_minutes':len(rows)}
   record(results,ledgers,prof+'/'+label,rows,stress,False,SPLIT)
   for asset in coverage:record(results,ledgers,asset+'/'+prof+'/'+label,[x for x in rows if x['asset']==asset],stress,False,SPLIT)
   print('RESULT',prof,label,{k:v for k,v in results[prof+'/'+label].items() if k in ('trades','wins','losses','net','drawdown','weekly')},flush=True)
 save(P/'sports_results.json',results);save(P/'sports_ledgers.json.gz',ledgers);save(P/'sports_coverage.json',coverage);save(P/'sports_execution.json',rejects)
if __name__=='__main__':main()
