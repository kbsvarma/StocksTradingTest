"""Exploratory loss diagnosis using frozen candidates and independent controls."""
import json,gzip,sys,math
from collections import Counter,defaultdict
from decimal import Decimal as D
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
import portfolio as b
sys.path.insert(0,str(P.parent/'kalshi_capacity25_2026_10_07'))
import budget_replay as control
ET=ZoneInfo('America/New_York');CRYPTO=set(b.ASSETS[:4]);Z2='cushion_z2'
def save(n,o): (P/n).write_text(json.dumps(o,indent=2,allow_nan=False)+'\n')
def read(n):
 with gzip.open(n,'rt') as f:return json.load(f)
def t(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
CANDIDATES=['baseline','crypto_z225','crypto_z250','crypto_z300','crypto_max7min','crypto_max5min','original_max7min','copper_z175','copper_z200','wti_z200','wti_z250','crypto_correlated1','crypto_correlated2','no_wti']
def keep(x,name):
 if name=='no_wti':return x['asset']!='WTI'
 if x['lane']!=Z2:return True
 if name.startswith('crypto_z') and x['asset'] in CRYPTO:return x['z']>=int(name[-3:])/100-1e-12
 if name.startswith('crypto_max') and x['asset'] in CRYPTO:return x['rem']<=int(name[-4])
 if name=='original_max7min':return x['rem']<=7
 if name.startswith('copper_z') and x['asset']=='COPPER':return x['z']>=int(name[-3:])/100-1e-12
 if name.startswith('wti_z') and x['asset']=='WTI':return x['z']>=int(name[-3:])/100-1e-12
 return True
PARAMS=dict(budget=20,exposure=25,max_contracts=25,z3_contracts=25,z3_exposure=25)
periods={
 'matched10d':(t('2026-09-23T00:00:00Z'),t('2026-10-03T00:00:00Z'),False),
 'first5d':(t('2026-09-23T00:00:00Z'),t('2026-09-28T00:00:00Z'),False),
 'second5d':(t('2026-09-28T00:00:00Z'),t('2026-10-03T00:00:00Z'),False),
 'broader_available':(b.s.r.START,b.s.r.SPLIT,False),
 'later_noncrypto':(b.s.r.SPLIT,b.s.r.END,True)}
pools=read(P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz')
hist={};base_entries={}
for pname,(start,end,onlynew) in periods.items():
 hist[pname]={}
 for cost,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
  pool=[x for x in pools[cost] if start<=x['close']<end and (not onlynew or x['asset'] not in CRYPTO)]
  baseline,bb=control.simulate(pool,stress=stress,**PARAMS)
  ours,ob=b.simulate(pool,stress=stress,**PARAMS)
  assert baseline==ours and bb==ob, (pname,cost,'control mismatch')
  hist[pname][cost]={}
  for name in CANDIDATES:
   params=dict(PARAMS)
   if name.startswith('crypto_correlated'):params['correlation_cap']=int(name[-1])
   fills,blocks=b.simulate([x for x in pool if keep(x,name)],stress=stress,**params)
   bm={x['ticker']:x for x in baseline};fm={x['ticker']:x for x in fills}
   removed=[x for k,x in bm.items() if k not in fm];added=[x for k,x in fm.items() if k not in bm]
   hist[pname][cost][name]=dict(**b.summary(fills),blocks=blocks,removed=dict(trades=len(removed),wins=sum(x['won'] for x in removed),losses=sum(1-x['won'] for x in removed),net=round(sum(x['net'] for x in removed),4)),added=dict(trades=len(added),wins=sum(x['won'] for x in added),losses=sum(1-x['won'] for x in added),net=round(sum(x['net'] for x in added),4)),retimed=sum(k in bm and (bm[k]['entered']!=x['entered'] or bm[k]['lane']!=x['lane']) for k,x in fm.items()))
   if pname=='matched10d' and cost=='fees1c':base_entries[name]=fills
save('historical_results.json',hist)
with gzip.open(P/'historical_entries.json.gz','wt') as f:json.dump(base_entries,f)
assert hist['matched10d']['fees1c']['baseline']['trades']==490
assert abs(hist['matched10d']['fees1c']['baseline']['net']-58.9151)<1e-8
rows=read(P/'orders.json.gz');settled=[x for x in rows if x['status']=='settled' and D(str(x.get('filled',0)))>0]
for x in settled:
 x['lane']=x.get('lane',Z2);x['z']=x['signal']['z'];x['rem']=x['signal']['remaining_minutes'];x['close']=x.get('close_ts',x['signal'].get('close_ts'));x['won']=int(x['side']==x['result']);x['net']=float(x['pnl'])
 assert D(x['pnl'])==D(x['filled'])*x['won']-D(x['cost'])-D(x['fees'])
def metrics(rr):
 profit=sum((D(x['pnl']) for x in rr if D(x['pnl'])>0),D(0));loss=-sum((D(x['pnl']) for x in rr if D(x['pnl'])<0),D(0))
 cum=peak=dd=D(0)
 for x in sorted(rr,key=lambda x:(x['close'],x['ticker'])):
  cum+=D(x['pnl']);peak=max(peak,cum);dd=max(dd,peak-cum)
 qty=sum(D(x['filled']) for x in rr);cash=sum(D(x['cost'])+D(x['fees']) for x in rr)
 return dict(trades=len(rr),wins=sum(x['won'] for x in rr),losses=sum(1-x['won'] for x in rr),net=float(profit-loss),fees=float(sum((D(x['fees']) for x in rr),D(0))),gross_winners=float(profit),gross_losses=float(loss),average_win=float(profit/sum(x['won'] for x in rr)) if profit else 0,average_loss=float(loss/sum(1-x['won'] for x in rr)) if loss else 0,drawdown_by_close=float(dd),quantity=float(qty),all_in_cost_per_contract=float(cash/qty) if qty else None)
def group(rr,fn):
 d=defaultdict(list)
 for x in rr:d[str(fn(x))].append(x)
 return {k:metrics(v) for k,v in sorted(d.items())}
cohorts={'lifetime_mixed_sizes':settled,'today': [x for x in settled if x['settled_day']=='2026-10-08'],'since_z3_oct3': [x for x in settled if x['created_ts']>=t('2026-10-03T02:16:00-04:00')],'all_five_contract_fills':[x for x in settled if D(x['filled'])==5]}
actual={}
for cname,rr in cohorts.items():
 result=dict(total=metrics(rr),by_asset=group(rr,lambda x:x['asset']),by_lane=group(rr,lambda x:x['lane']),by_asset_lane=group(rr,lambda x:x['asset']+'/'+x['lane']),by_day=group(rr,lambda x:x['settled_day']))
 pruned={}
 for name in CANDIDATES:
  kept=[];same=Counter()
  for x in sorted(rr,key=lambda x:x['created_ts']):
   if not keep(x,name):continue
   key=(x['close'],x['side'])
   if name.startswith('crypto_correlated') and x['asset'] in CRYPTO:
    if same[key]>=int(name[-1]):continue
    same[key]+=1
   kept.append(x)
  removed=[x for x in rr if x not in kept]
  pruned[name]=dict(**metrics(kept),removed=metrics(removed),removed_tickers=[x['ticker'] for x in removed])
 result['pruning_not_full_replay']=pruned;actual[cname]=result
losses=[{k:x.get(k) for k in ('ticker','asset','lane','filled','entry','cost','fees','pnl','created_ts','close','settled_day','z','rem','side','result')} for x in settled if not x['won']]
actual['losses']=losses
actual['same_expiry_losses']=group([x for x in settled if not x['won']],lambda x:datetime.fromtimestamp(x['close'],ET).isoformat())
actual['crypto_original_buckets']=dict(z=group([x for x in settled if x['asset'] in CRYPTO and x['lane']==Z2],lambda x:'2-2.25' if x['z']<2.25 else '2.25-2.5' if x['z']<2.5 else '2.5-3' if x['z']<3 else '3+'),time=group([x for x in settled if x['asset'] in CRYPTO and x['lane']==Z2],lambda x:x['rem']))
save('actual_results.json',actual)
print('TODAY',json.dumps(actual['today']))
print('HISTORY',json.dumps({k:{p:{q:hist[p][q][k] for q in ('fees1c','fees2c','cap1c')} for p in ('matched10d','later_noncrypto')} for k in CANDIDATES}))
