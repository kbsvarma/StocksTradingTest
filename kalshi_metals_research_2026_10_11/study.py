"""Frozen metal mechanisms, causal weekly probabilities and quote-proxy portfolios."""
import argparse,datetime as dt,gzip,hashlib,json,math,pickle
from pathlib import Path
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR
from collections import defaultdict,Counter
from functools import lru_cache
from zoneinfo import ZoneInfo
import numpy as np
from pull import P,ASSETS,START,END,ts
from build import quote
ET=ZoneInfo('America/New_York')
PERIODS={'development':(START,ts('2026-09-21T00:00:00Z')),'selection':(ts('2026-09-21T00:00:00Z'),ts('2026-10-03T00:00:00Z')),'confirmation':(ts('2026-10-03T00:00:00Z'),ts('2026-10-08T00:00:00Z')),'recent':(ts('2026-10-08T00:00:00Z'),END),'pooled':(START,END)}
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
@lru_cache(None)
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
@lru_cache(None)
def fee(px,qty=5,multiplier=1,precision='.0001'):
 p=D(str(px));cost=p*qty;raw=(D('.07')*D(str(multiplier))*qty*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING);unit=D(precision)
 return float(((cost+raw)/unit).to_integral_value(rounding=ROUND_CEILING)*unit-cost)
@lru_cache(None)
def cap(ask,side,absolute,ranges):
 spend=min(D(str(ask))+D('.01'),D(str(absolute)));yes=spend if side==1 else 1-spend
 for row in json.loads(ranges):
  lo,hi,step=map(D,(row['start'],row['end'],row['step']))
  if lo<=yes<=hi:
   px=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side==1 else ROUND_CEILING)*step
   return float(px if side==1 else 1-px)
 raise ValueError('missing tick range')

def configs():
 out=[]
 def add(name,**kw):out.append(dict(id=len(out),name=name,family='cushion',z=2,lo=3,hi=10,vol='z',filter='none',floor=.85,**kw))
 add('original_z2_z3')
 out.append(dict(id=len(out),name='retired_copper',family='cushion',z=1.5,lo=1,hi=10,vol='z',filter='none',floor=.8))
 for z in (1.5,2,2.5,3):
  for lo,hi in ((1,3),(3,5),(3,10),(5,10)):
   if (z,lo,hi)==(2,3,10):continue
   out.append(dict(id=len(out),name=f'z{z}_{lo}to{hi}',family='cushion',z=z,lo=lo,hi=hi,vol='z',filter='none',floor=.85))
 for v,f in (('zrob','none'),('zh','none'),('z','vol_stable'),('z','no_jump'),('z','persistent'),('z','us'),('z','no_release'),('zrob','persistent'),('zrob','us')):
  out.append(dict(id=len(out),name=f'{v}_{f}',family='cushion',z=2,lo=3,hi=10,vol=v,filter=f,floor=.85))
 for rem in (3,5,10):
  for model in ('quote','z','rich','empirical'):
   for edge in (.02,.04):out.append(dict(id=len(out),name=f'{model}_{rem}m_edge{edge}',family='value',rem=rem,model=model,edge=edge))
 return out
CONFIG=configs()

def feature_vector(m,f,kind):
 mid=(f['ybid']+f['yask'])/2;logit=math.log(max(.001,mid)/max(.001,1-mid));base=[logit]
 if kind in ('z','rich'):base.extend([np.clip(f['zy'],-6,6),math.log(f['rem'])])
 if kind=='rich':base.extend([np.clip(f['zrob']*f['side'],-6,6),np.clip(f['mom3'],-6,6),np.clip(f['volratio'],0,4),float(f['us']),math.sin(f['hour']/24*2*math.pi),math.cos(f['hour']/24*2*math.pi),float(m['asset']=='SILVER'),float(m['asset']=='COPPER')])
 return base

def fit(X,y):
 X=np.array(X,dtype=float);y=np.array(y,dtype=float);mu=X.mean(0);sd=X.std(0);sd[sd<1e-9]=1;X=np.column_stack([np.ones(len(X)),(X-mu)/sd]);beta=np.zeros(X.shape[1]);penalty=np.diag([.001]+[5.]*(len(beta)-1))
 for _ in range(40):
  eta=np.clip(X@beta,-30,30);pr=1/(1+np.exp(-eta));w=np.maximum(pr*(1-pr),1e-7);H=X.T@(X*w[:,None])+penalty;gradient=X.T@(pr-y)+penalty@beta;step=np.linalg.solve(H,gradient);beta-=step
  if max(abs(step))<1e-7:break
 return beta,mu,sd

def predict(model,X):
 beta,mu,sd=model;X=np.column_stack([np.ones(len(X)),(np.array(X)-mu)/sd]);return 1/(1+np.exp(-np.clip(X@beta,-30,30)))

def build_predictions(markets):
 byrem={rem:[(m,f) for m in markets for f in m['features'] if f['rem']==rem] for rem in (3,5,10)}
 records={};models=[]
 for rem,rows in byrem.items():
  weeks=sorted({int((f['t']-345600)//604800)*604800+345600 for m,f in rows}) # Monday UTC
  for begin in weeks:
   train=[(m,f) for m,f in rows if m['settled']<begin];test=[(m,f) for m,f in rows if begin<=f['t']<begin+604800]
   counts=Counter(m['asset'] for m,f in train)
   if any(counts[a]<250 for a in ASSETS) or not test:continue
   for kind in ('quote','z','rich'):
    model=fit([feature_vector(m,f,kind) for m,f in train],[m['result'] for m,f in train]);pred=predict(model,[feature_vector(m,f,kind) for m,f in test])
    for (m,f),p in zip(test,pred):
     mid=(f['ybid']+f['yask'])/2;records[(m['ticker'],f['t'],kind)]=float(.8*p+.2*mid)
    models.append(dict(week=begin,rem=rem,model=kind,train=len(train),last_settled=max(m['settled'] for m,f in train),test=len(test),beta=model[0].tolist(),mean=model[1].tolist(),scale=model[2].tolist()))
   # Empirical residual distribution only same asset and horizon, no Gaussian tail assumption.
   residuals={a:np.sort([f['residual'] for m,f in train if m['asset']==a]) for a in ASSETS}
   for m,f in test:
    rr=residuals[m['asset']];success=len(rr)-np.searchsorted(rr,f['threshold'],side='left');p=(success+1)/(len(rr)+2);mid=(f['ybid']+f['yask'])/2
    records[(m['ticker'],f['t'],'empirical')]=float(.8*p+.2*mid)
   print('models',rem,dt.datetime.fromtimestamp(begin,dt.timezone.utc).date(),len(train),len(test),flush=True)
 (P/'predictions.pickle').write_bytes(pickle.dumps(records));save('model_audit.json',models)


def filter_pass(f,c):
 name=c['filter']
 return {'none':True,'vol_stable':f['volratio']<=1.5,'no_jump':f['jump']<=4,'persistent':f['zprev']>=c['z'],'us':f['us'],'no_release':not f['release']}[name]


def opportunities(markets,c,pred,mode='base'):
 out=[];counts=Counter()
 for m in markets:
  for f in m['features']:
   if c['family']=='cushion':
    if not c['lo']<=f['rem']<=c['hi'] or f['ask']-f['bid']>.02000001 or not filter_pass(f,c):continue
    options=[]
    for lane,z,fl,ceil,absolute in (('original',c['z'],c['floor'],.95,.939),('premium',c['z']+1,.93,.98,.975)):
     if fl-1e-8<=f['ask']<ceil-1e-8 and f[c['vol']]>=z:options.append((lane,f['side'],f['ask'],fl,absolute,None))
   else:
    if f['rem']!=c['rem']:continue
    p=pred.get((m['ticker'],f['t'],c['model']))
    if p is None:continue
    yes=(1,f['yask'],p);no=(-1,1-f['ybid'],1-p);chosen=max((yes,no),key=lambda r:r[2]-r[1]-fee(r[1],5,m['multiplier'])/5-.01)
    side,ask,pwin=chosen;edge=pwin-ask-fee(ask,5,m['multiplier'])/5-.01
    if not .5-1e-8<=ask<.95-1e-8 or f['yask']-f['ybid']>.02000001 or edge<c['edge']:continue
    options=[('value',side,ask,.5,.939,pwin)]
   for lane,side,ask,floor,absolute,pwin in options:
    counts['qualified_minutes']+=1
    limit=cap(ask,side,absolute,json.dumps(m['ranges'],sort_keys=True));pair=quote(m['candles'].get(f['t']+60),'c' if mode=='delay60' else 'o')
    if pair is None:counts['missing_execution_quote']+=1;continue
    bookask=pair[1] if side==1 else 1-pair[0]
    if not floor-1e-8<=bookask<=limit+1e-8:counts['outside_cap_floor']+=1;continue
    cost=limit if mode=='cap' else min(limit,max(ask,bookask))
    if mode=='legacy':cost=bookask
    if cost>limit+1e-8:counts['conservative_quote_above_cap']+=1;continue
    won=int(m['result']==int(side==1));paid=fee(cost,5,m['multiplier'],'.01' if mode=='wholecent' else '.0001')
    out.append(dict(ticker=m['ticker'],asset=m['asset'],config=c['id'],lane=lane,t=f['t'],entered=f['t']+(60 if mode=='delay60' else 3),close=m['close'],settled=m['settled'],side=side,won=won,quantity=5,entry=cost,cap=limit,ask=ask,bookask=bookask,floor=floor,fee=paid,z=f['z'],rem=f['rem'],net_before_friction=5*(won-cost)-paid,pwin=pwin,reserve=5*(1 if lane in ('premium','value') else .95),hour=f['hour'],zrob=f['zrob'],zh=f['zh'],volratio=f['volratio'],jump=f['jump'],flat=f['flat_minutes'],zero_fraction=f['zero_fraction']))
 return out,dict(counts)


def portfolio(rows,stress=.01,enforce=True):
 cash=100.;active=[];closed=[];seen=set();daily=defaultdict(float);blocked=Counter();minimum_cash=cash;peak_risk=0
 def settle(t):
  nonlocal cash,active
  remaining=[]
  for r in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
   if r['settled']<=t:cash+=5*r.get('payout',r['won']);daily[day(r['settled'])]+=r['net'];closed.append(r)
   else:remaining.append(r)
  active=remaining
 for r in sorted(rows,key=lambda r:(r['entered'],0 if r['lane']=='original' else 1,ASSETS.index(r['asset']))):
  settle(r['entered'])
  if r['ticker'] in seen:continue
  reserve=max(r['reserve'],5*r['entry']+r['fee']+stress*5)
  risk=sum(a['reserve'] for a in active)+reserve;reason=None
  if enforce:
   if any(a['asset']==r['asset'] for a in active):reason='asset_owned'
   elif len(active)*5+5>25 or risk>25+1e-8:reason='exposure'
   elif daily[day(r['entered'])]-risk < -20-1e-8:reason='daily_including_open'
   elif cash<max(5,reserve):reason='cash'
  if reason:blocked[reason]+=1;continue
  net=r['net_before_friction']-stress*5;cash-=5*r['entry']+r['fee']+stress*5;minimum_cash=min(minimum_cash,cash);peak_risk=max(peak_risk,risk)
  active.append(dict(r,net=net,friction=stress*5,reserve=reserve));seen.add(r['ticker'])
 settle(float('inf'))
 return sorted(closed,key=lambda r:(r['settled'],r['ticker'])),dict(blocked=blocked,minimum_cash=minimum_cash,peak_reserved_risk=peak_risk,final_cash=cash)


def metrics(rows):
 by=defaultdict(float);daily=defaultdict(float);weeks=defaultdict(float)
 for r in rows:by[r['settled']]+=r['net'];daily[day(r['settled'])]+=r['net'];weeks[dt.date.fromisoformat(day(r['settled'])).isocalendar()[:2]]+=r['net']
 eq=peak=dd=0
 for t in sorted(by):eq+=by[t];peak=max(peak,eq);dd=max(dd,peak-eq)
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(1-r['won'] for r in rows),net=round(eq,6),drawdown=round(dd,6),mean_cost=sum(r['entry'] for r in rows)/len(rows) if rows else None,breakeven=sum(r['entry']+(r['fee']+r['friction'])/5 for r in rows)/len(rows) if rows else None,positive_days=sum(v>0 for v in daily.values()),active_days=len(daily),positive_weeks=sum(v>0 for v in weeks.values()),active_weeks=len(weeks),daily=dict(sorted(daily.items())),without_best_day=eq-max(daily.values(),default=0),without_best3=eq-sum(sorted((r['net'] for r in rows),reverse=True)[:3]))


def evaluate(pool,asset,period,stress=.01,enforce=True):
 lo,hi=PERIODS[period];rows=[r for r in pool if (asset=='ALL' or r['asset']==asset) and lo<=r['close']<hi];ledger,diag=portfolio(rows,stress,enforce);m=metrics(ledger);m['trades_per_calendar_day']=len(ledger)/((hi-lo)/86400);m.update(diag);return m,ledger


def select(markets,pred):
 result={a:[] for a in ASSETS}
 for c in CONFIG:
  pools={mode:opportunities(markets,c,pred,mode)[0] for mode in ('base','cap')}
  for a in ASSETS:
   x=dict(id=c['id'],name=c['name'])
   for period in ('development','selection'):
    x[period]={n:evaluate(pools[mode],a,period,stress)[0] for n,mode,stress in (('base','base',.01),('cost2','base',.02),('cap','cap',.01))}
   x['pass']=x['development']['base']['trades']>=25 and x['selection']['base']['trades']>=15 and all(x[p][m]['net']>0 for p in ('development','selection') for m in ('cost2','cap'))
   scores=[(x[p][m]['net']-.1*x[p][m]['drawdown'])/((PERIODS[p][1]-PERIODS[p][0])/86400) for p in ('development','selection') for m in ('cost2','cap')];x['score']=min(scores)
   result[a].append(x)
  if c['id']%10==0:print('selection completed rule',c['id'],flush=True)
 chosen={}
 for a,table in result.items():
  good=sorted((x for x in table if x['pass']),key=lambda x:(-x['score'],x['id']))
  chosen[a]=dict(selected=good[0]['id'] if good else None,survivors=[x['id'] for x in good],best_unqualified=max(table,key=lambda x:x['score'])['id'])
 save('selection.json',result);save('choices_frozen.json',dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(),choices=chosen,configurations=CONFIG,protocol_sha=hashlib.sha256((P/'PLAN.txt').read_bytes()).hexdigest(),selection_source_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),later_results_not_calculated=True))
 print('FROZEN',json.dumps(chosen),flush=True)


def confirm(markets,pred):
 frozen=json.loads((P/'choices_frozen.json').read_text());ids=sorted({0,1}|{x[k] for x in frozen['choices'].values() for k in ('selected','best_unqualified') if x[k] is not None})
 results={};ledgers={}
 for cid in ids:
  c=CONFIG[cid];result={};logs={}
  for mode,stress,name,enforce in (('base',.01,'base',True),('base',.02,'cost2',True),('cap',.01,'cap',True),('delay60',.01,'delay60',True),('wholecent',.01,'wholecent',True),('base',.01,'unconstrained',False),('legacy',.01,'legacy_proxy',True)):
   pool,counts=opportunities(markets,c,pred,mode);result[name]={};logs[name]={}
   for a in (*ASSETS,'ALL'):
    result[name][a]={};logs[name][a]={}
    for period in PERIODS:
     m,ledger=evaluate(pool,a,period,stress,enforce);result[name][a][period]=m;logs[name][a][period]=ledger
   result[name]['input_counts']=counts
  results[str(cid)]=result;ledgers[str(cid)]=logs
 save('final_results.json',results)
 with gzip.open(P/'final_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 for a,choice in frozen['choices'].items():
  cid=choice['selected'];print('FINAL',a,CONFIG[cid]['name'] if cid is not None else 'NO PASS',json.dumps({p:{k:results[str(cid)]['base'][a][p][k] for k in ('trades','wins','losses','net','drawdown')} for p in PERIODS}) if cid is not None else '',flush=True)

if __name__=='__main__':
 action=argparse.ArgumentParser();action.add_argument('action',choices=['models','select','confirm']);args=action.parse_args();markets=pickle.loads((P/'features.pickle').read_bytes())
 if args.action=='models':build_predictions(markets)
 else:
  pred=pickle.loads((P/'predictions.pickle').read_bytes())
  if args.action=='select':select(markets,pred)
  else:confirm(markets,pred)
