from pathlib import Path
import pandas as pd,numpy as np,json
from numba import njit,prange,set_num_threads
set_num_threads(4)
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09'
D=pd.read_parquet(O/'all_screened_ranked_pairs.parquet');Q=pd.read_parquet(O/'price_matrix.parquet');mapping={s:i for i,s in enumerate(Q.columns)};aa=D.a.map(mapping).to_numpy();bb=D.b.map(mapping).to_numpy();px=Q.to_numpy().T.copy();starts=np.array(pd.Series(np.arange(len(Q)),index=Q.index).groupby(Q.index.to_period('M')).last(),dtype=np.int64)
@njit(parallel=True,cache=True)
def run(px,aa,bb,starts):
 n=px.shape[1];out=np.full((len(aa),21),np.nan);c=.001
 for k in prange(len(aa)):
  pa=px[aa[k]];pb=px[bb[k]];la=np.zeros(n);lb=np.zeros(n);lp=np.zeros(n);bad=np.zeros(n,np.int64);endcorr=np.zeros(n)
  for t in range(1,n):
   la[t]=la[t-1];lb[t]=lb[t-1];lp[t]=lp[t-1];bad[t]=bad[t-1]
   if np.isfinite(pa[t]+pb[t]+pa[t-1]+pb[t-1]) and min(pa[t],pb[t],pa[t-1],pb[t-1])>0:
    ga=pa[t]/pa[t-1];gb=pb[t]/pb[t-1];m=(ga+gb)/2;net=m-c*abs(ga-gb)/2;la[t]+=np.log(ga);lb[t]+=np.log(gb);lp[t]+=np.log(net);endcorr[t]=np.log(m/net)
   else:bad[t]+=1
  for hi in range(3):
   horizon=252*(hi+1);profits=np.empty(len(starts));advs=np.empty(len(starts));ct=0;wins=0;pos=0
   for s in starts:
    e=s+horizon
    if e>=n or bad[e]!=bad[s]:continue
    strategy=lp[e]-lp[s]+endcorr[e]+np.log((1-c)/(1+c));hold=np.log(.5*(np.exp(la[e]-la[s])+np.exp(lb[e]-lb[s])))+np.log((1-c)/(1+c));adv=(strategy-hold)/(hi+1);profit=np.expm1(strategy/(hi+1));profits[ct]=profit;advs[ct]=adv;ct+=1;wins+=adv>0;pos+=profit>0
   off=hi*7;out[k,off]=ct
   if ct:
    out[k,off+1]=wins/ct;out[k,off+2]=pos/ct;out[k,off+3]=np.median(profits[:ct]);out[k,off+4]=np.min(profits[:ct]);out[k,off+5]=np.median(advs[:ct]);out[k,off+6]=np.min(advs[:ct])
 return out
x=run(px,aa,bb,starts);cols=[f'rolling_{h}y_{m}' for h in [1,2,3] for m in ['windows','beat_hold_fraction','positive_fraction','median_cagr','worst_cagr','median_log_advantage','worst_log_advantage']];r=pd.DataFrame(x,columns=cols);r.insert(0,'b',D.b.to_numpy());r.insert(0,'a',D.a.to_numpy());r.to_parquet(P/'rolling_all_pairs.parquet',index=False);print('DONE',r.shape,flush=True)
