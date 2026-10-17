from pathlib import Path
import pandas as pd,numpy as np,json,itertools
from numba import njit
P=Path(__file__).resolve().parent;D=P.parent/'databento_fresh_2026_09';spec=json.loads((D/'contract_assumptions.json').read_text())
@njit(cache=True)
def run(op,cl,gap,move,roll,mult,tick,flags,style,integer,stress):
 q=np.zeros(2);net=0.;path=np.zeros(len(op));turn=0.;fees=0.;peak=0.;min_margin=1e12;min_equity=1e12
 for i in range(len(op)):
  net+=np.sum(q*gap[i]*mult)
  target=q.copy()
  if i==0 or (style>0 and flags[i]):
   mark=op[i] if i==0 else cl[i-1];target=max(0.,1000000+net)*.5/(np.maximum(np.abs(mark),np.abs(op[0])*.5)*mult)
   if integer:target=np.floor(target)
   for j in range(2):
    if not np.isfinite(op[i,j]):target[j]=q[j]
  changes=np.abs(target-q)+2*np.minimum(target,q)*roll[i]
  cost=np.sum(changes*(3.5*stress+tick*mult*stress));net-=cost;fees+=cost;turn+=changes.sum();q=target;net+=np.sum(q*move[i]*mult);path[i]=net;notional=np.sum(q*np.abs(cl[i])*mult);peak=max(peak,notional);min_equity=min(min_equity,1000000+net);min_margin=min(min_margin,1000000+net-.2*notional)
 cost=np.sum(q*(3.5*stress+tick*mult*stress));path[-1]-=cost;fees+=cost
 return path,fees,turn,peak,min_equity,min_margin
rows=[];audit=[];series={}
for f in sorted((D/'data').glob('daily_*.parquet')):
 sym=f.stem[6:]
 if sym not in spec:continue
 d=pd.read_parquet(f);d=d.loc['2019':'2026'];keep=d[['open','close','gap','move_after_open']].notna().all(axis=1);d=d[keep];audit.append(dict(symbol=sym,days=len(d),qualifies=len(d)>=1000))
 if len(d)>=1000:series[sym]=d
for a,b in itertools.combinations(series,2):
 common=series[a].index.intersection(series[b].index)
 if len(common)<1000:continue
 ix=series[a].index.union(series[b].index);ix=ix[(ix>=common[0])&(ix<=common[-1])]
 da,db=series[a].reindex(ix),series[b].reindex(ix)
 for dd in [da,db]:
  dd['close']=dd['close'].ffill();dd['gap']=dd['gap'].fillna(0);dd['move_after_open']=dd['move_after_open'].fillna(0);dd['roll']=dd['roll'].fillna(False)
 mult=np.array([spec[a][0],spec[b][0]],dtype=float);tick=np.array([spec[a][1],spec[b][1]],dtype=float)
 mats=[np.column_stack([da[k],db[k]]) for k in ['open','close','gap','move_after_open','roll']]
 for integer in [False,True]:
  for stress in [1.,2.]:
   for name,style in [('hold',0),('daily',1),('weekly',1)]:
    flags=np.ones(len(ix),dtype=bool) if name!='weekly' else np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]];p,fees,turn,peak,mineq,margin=run(*mats,mult,tick,flags,style,integer,stress);inc=pd.Series(np.diff(p,prepend=0),index=ix)
    for period,lo,hi in [('all','2019','2027'),('early','2019','2023'),('recent','2023','2027')]:
     x=inc[(inc.index>=lo)&(inc.index<hi)];eq=x.cumsum();rows.append(dict(a=a,b=b,integer=integer,cost=stress,design=name,period=period,net=x.sum(),dd=(eq-eq.cummax().clip(lower=0)).min(),drawdown_pct=float(((1000000+inc.cumsum()).loc[x.index]/(1000000+inc.cumsum()).cummax().clip(lower=1000000).loc[x.index]-1).min()),sharpe=x.mean()/x.std()*np.sqrt(252),fees_full=fees,peak_notional=peak,min_equity=mineq,min_margin20=margin,turnover_full=turn,days=len(x)))
 print(a,b,flush=True)
pd.DataFrame(rows).to_csv(P/'futures_crp_scores.csv',index=False);pd.DataFrame(audit).to_csv(P/'futures_crp_coverage.csv',index=False)
