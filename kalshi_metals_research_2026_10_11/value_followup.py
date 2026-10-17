"""Execution-selection stress on the frozen Copper probability candidate (rule40)."""
import json,gzip,pickle,math
from collections import defaultdict,Counter
import numpy as np
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());pred=pickle.loads((P/'predictions.pickle').read_bytes());c=s.CONFIG[40]
namespace={};exec(compile((P/'book_overlay.py').read_text().split('frozen=json.loads')[0],str(P/'book_overlay.py'),'exec'),namespace)
base,_=s.opportunities(markets,c,pred);base=[r for r in base if r['asset']=='COPPER'];known={(r['ticker'],r['t']):r for r in base};allsignals=[];qual=[]
for m in markets:
 if m['asset']!='COPPER':continue
 for f in m['features']:
  if f['rem']!=5:continue
  p=pred.get((m['ticker'],f['t'],'empirical'))
  if p is None:continue
  side,ask,pwin=max(((1,f['yask'],p),(-1,1-f['ybid'],1-p)),key=lambda x:x[2]-x[1]-s.fee(x[1],5,m['multiplier'])/5-.01)
  edge=pwin-ask-s.fee(ask,5,m['multiplier'])/5-.01
  if not .5-1e-8<=ask<.95-1e-8 or f['yask']-f['ybid']>.02000001 or edge<.02:continue
  cap=s.cap(ask,side,.939,json.dumps(m['ranges'],sort_keys=True));won=int(m['result']==int(side==1));paid=s.fee(cap,5,m['multiplier'])
  row=dict(ticker=m['ticker'],asset=m['asset'],config=40,lane='value',t=f['t'],entered=f['t']+3,close=m['close'],settled=m['settled'],side=side,won=won,quantity=5,entry=cap,cap=cap,ask=ask,bookask=None,floor=.5,fee=paid,z=f['z'],rem=5,net_before_friction=5*(won-cap)-paid,pwin=pwin,reserve=5)
  allsignals.append(row);qual.append(dict(row,estimated_edge=edge))
  book=namespace['recorded_quote'](m['ticker'],f['t'],side,.5,cap)
  if book:
   price,when,source=book;price=min(cap,max(ask,price));paid=s.fee(price,5,m['multiplier']);known[(m['ticker'],f['t'])]=dict(row,entered=when,entry=price,bookask=book[0],fee=paid,net_before_friction=5*(won-price)-paid,source=source)
result={};ledgers={}
for mode,pool in (('quote',base),('recorded_overlay',list(known.values())),('all_signals_at_cap',allsignals)):
 result[mode]={};ledgers[mode]={}
 for scenario,cost in (('base',.01),('cost2',.02),('cost3',.03)):
  result[mode][scenario]={};ledgers[mode][scenario]={}
  for period in s.PERIODS:
   m,l=s.evaluate(pool,'COPPER',period,cost);assert abs(m['final_cash']-100-m['net'])<1e-5;result[mode][scenario][period]=m;ledgers[mode][scenario][period]=l
s.save('value_execution_stress.json',result)
with gzip.open(P/'value_execution_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
with gzip.open(P/'value_qualifiers.json.gz','wt') as f:json.dump(qual,f)

# Fixed original grid neighbor: 4c surplus /3min or10min, no later-selected replacement.
neighbors={}
for cid in (32,33,40,41,48,49):
 cc=s.CONFIG[cid];pool,_=s.opportunities(markets,cc,pred);neighbors[str(cid)]=dict(rule=cc,results={per:s.evaluate(pool,'COPPER',per,.02)[0] for per in s.PERIODS})
s.save('value_neighbors.json',neighbors)
rr=ledgers['recorded_overlay']['base']['pooled'];rng=np.random.default_rng(4011);unc={}
for period in ('pooled','confirmation','recent'):
 rr=ledgers['recorded_overlay']['base'][period];lo,hi=s.PERIODS[period];daily=defaultdict(float)
 for r in rr:daily[s.day(r['settled'])]+=r['net']
 dates=sorted({s.day(t) for t in range(lo,hi,3600)}|set(daily));values=np.array([daily[d] for d in dates]);ci=np.quantile(rng.choice(values,(10000,len(values)),replace=True).sum(1),[.025,.975]);unc[period]=dict(day_bootstrap95=ci.tolist(),one_more_loss=sum(r['net'] for r in rr)-5,positive_days=int(sum(x>0 for x in values)),calendar_days=len(values))
# Audit label chronology for the empirical model explicitly (not included in logistic coefficients list).
training=[]
for m in markets:
 if m['asset']!='COPPER':continue
 for f in m['features']:
  if f['rem']!=5 or (m['ticker'],f['t'],'empirical') not in pred:continue
  week=int((f['t']-345600)//604800)*604800+345600;past=[(mm,ff) for mm in markets if mm['asset']=='COPPER' and mm['settled']<week for ff in mm['features'] if ff['rem']==5]
  if not any(x['week']==week for x in training):training.append(dict(week=week,train=len(past),latest_settled=max(mm['settled'] for mm,ff in past)))
  if m['ticker'] in {r['ticker'] for r in rr[:3]}:
   arr=np.array([ff['residual'] for mm,ff in past]);q=(sum(arr>=f['threshold'])+1)/(len(arr)+2);expected=.8*q+.2*(f['ybid']+f['yask'])/2;assert abs(expected-pred[(m['ticker'],f['t'],'empirical')])<1e-12
assert all(x['latest_settled']<x['week'] for x in training)
s.save('value_uncertainty.json',unc);s.save('value_training_audit.json',training)
print({m:{p:tuple(result[m]['base'][p][k] for k in ('trades','wins','losses','net','drawdown')) for p in ('pooled','confirmation','recent')} for m in result},flush=True)
