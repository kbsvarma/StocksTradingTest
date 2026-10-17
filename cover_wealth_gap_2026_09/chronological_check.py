from pathlib import Path
import pandas as pd,numpy as np
from numba import njit,prange,set_num_threads
set_num_threads(4)
P=Path(__file__).resolve().parent
x=pd.read_parquet(P/'common22_all_pairs.parquet');x=x[x.weight_a.eq(.5)&x.cost_bps.eq(10)&x.history_proxy&x.no_ai].copy();x=x[~x.a.isin(['GME','AMC','MSTR','COIN'])&~x.b.isin(['GME','AMC','MSTR','COIN'])]
q=pd.read_parquet(P.parent/'cover_validation_2026_09/long_eligible_prices.parquet');names=sorted(set(x.a)|set(x.b));q=q[names];mapping={s:i for i,s in enumerate(names)};aa=x.a.map(mapping).to_numpy();bb=x.b.map(mapping).to_numpy()
@njit(parallel=True,cache=True)
def calc(px,aa,bb):
 out=np.empty((len(aa),6));c=.001
 for k in prange(len(aa)):
  a,b=aa[k],bb[k];v=1/(1+c);peak=1.;dd=0.
  for t in range(1,px.shape[1]):
   ga=px[a,t]/px[a,t-1];gb=px[b,t]/px[b,t-1];g=(ga+gb)/2
   if t<px.shape[1]-1:g-=c*abs(ga-gb)/2
   else:g*=1-c
   v*=g;peak=max(v,peak);dd=min(dd,v/peak-1)
  sa=px[a,-1]/px[a,0]*(1-c)/(1+c);sb=px[b,-1]/px[b,0]*(1-c)/(1+c)
  out[k]=np.array([v,sa,sb,v/max(sa,sb),v/((sa+sb)/2),dd])
 return out
for label,lo,hi in [('selection','2004-09-13','2014-09-12'),('subsequent','2014-09-15','2026-09-11')]:
 z=calc(q.loc[lo:hi].to_numpy().T.copy(),aa,bb)
 for j,col in enumerate(['rebalance','stock_a','stock_b','over_best','over_hold','maxdd']):x[label+'_'+col]=z[:,j]
 print(label,len(x),flush=True)
x[['a','b']+[c for c in x if c.startswith(('selection_','subsequent_'))]].to_parquet(P/'chronological_all.parquet',index=False)
z=x[x.selection_rebalance.gt(1)].sort_values('selection_over_best',ascending=False).head(10)
z.to_csv(P/'chronological_top10.csv',index=False)
print(z[['a','b','selection_over_best','subsequent_over_best','subsequent_over_hold','subsequent_maxdd']].to_string(index=False),flush=True)
