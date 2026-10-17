from pathlib import Path
import sys,json
import pandas as pd,numpy as np
from numba import njit,prange
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09';sys.path.insert(0,str(O))
from engine import metrics
D=pd.read_parquet(O/'all_screened_ranked_pairs.parquet');Q=pd.read_parquet(O/'price_matrix.parquet').loc[:'2025-12-31'];symbols=list(Q.columns);mp={x:i for i,x in enumerate(symbols)};a=D.a.map(mp).to_numpy();b=D.b.map(mp).to_numpy();px=Q.to_numpy().T.copy();days=(Q.index-Q.index[0]).days.to_numpy().astype(float)
@njit(parallel=True)
def run(px,aa,bb,days):
 out=np.full((len(aa),18),np.nan)
 for k in prange(len(aa)):
  x=px[aa[k]];y=px[bb[k]];start=len(x)-1
  while start>0 and np.isfinite(x[start-1]) and np.isfinite(y[start-1]) and x[start-1]>0 and y[start-1]>0:start-=1
  out[k,0]=start
  if len(x)-start>1:out[k,1:]=metrics(x,y,start,len(x)-1,days)
 return out
v=run(px,a,b,days);cols=['start_index','days','years','cagr_0bps','cagr_5bps','cagr_10bps','hold_cagr_5bps','a_cagr_5bps','b_cagr_5bps','maxdd_5bps','hold_maxdd_5bps','annual_turnover','relative_log_5bps','relative_log_10bps','strategy_log_5bps','hold_log_5bps','correlation','diversification_log_bonus'];R=pd.DataFrame(v,columns=['pre_'+x for x in cols]);R.insert(0,'b',D.b.to_numpy());R.insert(0,'a',D.a.to_numpy());R.to_parquet(P/'pre2026_all_pairs.parquet',index=False);print('DONE',len(R),flush=True)
