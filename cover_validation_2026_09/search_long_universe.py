from pathlib import Path
import sys,json,time
import numpy as np,pandas as pd
from numba import njit,prange
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09';sys.path.insert(0,str(O));from engine import compute,metrics
Q=pd.read_parquet(P/'long_eligible_prices.parquet').sort_index(axis=1);names=np.array(Q.columns);px=Q.to_numpy().T.copy();dates=Q.index;days=dates.to_numpy().astype('datetime64[D]').astype('int64');pos=lambda s:np.flatnonzero(dates<=pd.Timestamp(s))[-1];d0=pos('2021-12-31');mid=pos('2023-12-31');end=pos('2025-12-31');aa,bb=np.triu_indices(len(names),1);out=P/'long_pairs';out.mkdir(exist_ok=True)
labels=['days','years','cagr_0bps','cagr_5bps','cagr_10bps','hold_cagr_5bps','a_cagr_5bps','b_cagr_5bps','maxdd_5bps','hold_maxdd_5bps','annual_turnover','relative_log_5bps','relative_log_10bps','strategy_log_5bps','hold_log_5bps','correlation','diversification_log_bonus'];cols=['start_index','end_index']+[p+'_'+x for p in ['full','discovery','half1','half2','confirmation'] for x in labels]
@njit(parallel=True,cache=True)
def prem(px,aa,bb,days,end):
 out=np.full((len(aa),18),np.nan)
 for k in prange(len(aa)):
  a=px[aa[k]];b=px[bb[k]];s=end
  while s>0 and np.isfinite(a[s-1]+b[s-1]) and a[s-1]>0 and b[s-1]>0:s-=1
  out[k,0]=s
  if s<end:out[k,1:]=metrics(a,b,s,end,days)
 return out
start=time.time()
for k in range(0,len(aa),20000):
 a=aa[k:k+20000];b=bb[k:k+20000];v=compute(px,a,b,days,d0,mid,end,len(dates)-1);d=pd.DataFrame(v,columns=cols);pre=prem(px,a,b,days,end);d=pd.concat([d,pd.DataFrame(pre,columns=['pre_start_index']+['pre_'+x for x in labels])],axis=1);d.insert(0,'b',names[b]);d.insert(0,'a',names[a]);d['robust_score_10bps']=np.minimum.reduce([np.log1p(d.half1_cagr_10bps),np.log1p(d.half2_cagr_10bps),d.half1_relative_log_10bps,d.half2_relative_log_10bps]);d['lower_risk_score']=d.robust_score_10bps/(-d.pre_maxdd_5bps);d.to_parquet(out/f'part_{k:08d}.parquet',index=False)
 if k%100000==0:print(k,'/',len(aa),round(time.time()-start,1),flush=True)
(P/'LONG_SEARCH_COUNTS.json').write_text(json.dumps({'stocks':len(names),'pairs':len(aa),'seconds':time.time()-start},indent=2));print('DONE',len(aa),flush=True)
