from pathlib import Path
import json
import pandas as pd,numpy as np
from numba import njit,prange,set_num_threads
set_num_threads(4)
P=Path(__file__).resolve().parent
@njit(cache=True,parallel=True)
def blocks(prices,aa,bb,start,end,block=20):
 n=end-start;nb=(n+block-1)//block;Q=np.zeros((len(aa),nb));observed=np.zeros(len(aa))
 for k in prange(len(aa)):
  a=prices[aa[k]];b=prices[bb[k]];hp=1.
  for t in range(start+1,end+1):
   ga=a[t]/a[t-1];gb=b[t]/b[t-1];crp=.5*(ga+gb)
   if t<end:crp-=.00025*abs(ga-gb)
   h=.5*(a[t]/a[start]+b[t]/b[start]);rel=np.log(crp)-np.log(h/hp);hp=h
   Q[k,(t-start-1)//block]+=rel;observed[k]+=rel
  mu=observed[k]/n
  for j in range(nb):Q[k,j]-=mu*min(block,n-j*block)
 return Q,observed*252/n
prices=np.load(P/'prices.npy');symbols=json.loads((P/'matrix_symbols.json').read_text());lookup={s:i for i,s in enumerate(symbols)};cal=pd.to_datetime(json.loads((P/'calendar.json').read_text()));start=int(np.flatnonzero(cal<='2021-12-31')[-1]);end=int(np.flatnonzero(cal<='2025-12-31')[-1]);n=end-start;nb=(n+19)//20
# Shared Gaussian multipliers preserve contemporaneous dependence between every pair.
# Nonoverlapping block multiplier approximation; not an exact or pristine-holdout p-value.
rng=np.random.default_rng(20260913);Z=rng.normal(size=(nb,499));maximum=np.full(499,-np.inf);student_maximum=np.full(499,-np.inf);screened_maximum=np.full(499,-np.inf);eligible=set(pd.read_csv(P/'tradability_stock_audit.csv').query('eligible').symbol);selected=pd.concat([pd.read_csv(P/'two_lists.csv'),pd.read_csv(P/'screened_two_lists.csv')],ignore_index=True);keys={(r.a,r.b) for _,r in selected.iterrows()};stats=[];count=0
for path in sorted((P/'all_pairs').glob('*.parquet')):
 f=pd.read_parquet(path,columns=['a','b','rankable']);f=f[f.rankable]
 if f.empty:continue
 aa=f.a.map(lookup).to_numpy();bb=f.b.map(lookup).to_numpy();Q,obs=blocks(prices,aa,bb,start,end)
 draws=Q@Z;maxima=draws.max(axis=0)*252/n;maximum=np.maximum(maximum,maxima)
 scale=np.sqrt((Q*Q).sum(axis=1));safe=np.maximum(scale,1e-12);tdraws=draws/safe[:,None];student_maximum=np.maximum(student_maximum,tdraws.max(axis=0))
 mask=f.a.isin(eligible).to_numpy()&f.b.isin(eligible).to_numpy()
 if mask.any():screened_maximum=np.maximum(screened_maximum,tdraws[mask].max(axis=0))
 count+=len(f)
 for i,(a,b) in enumerate(zip(f.a,f.b)):
  if (a,b) in keys:stats.append(dict(a=a,b=b,observed_annual_relative_log=float(obs[i]),observed_studentized_stat=float(obs[i]/(safe[i]*252/n))))
 if count%100000<len(f):print('family pairs',count,flush=True)
for r in stats:
 r['unstudentized_full_family_p']=(1+int(np.sum(maximum>=r['observed_annual_relative_log'])))/500
 r['studentized_full_family_p']=(1+int(np.sum(student_maximum>=r['observed_studentized_stat'])))/500
 r['studentized_screened_family_p']=(1+int(np.sum(screened_maximum>=r['observed_studentized_stat'])))/500
pd.DataFrame(stats).to_csv(P/'familywise_shortlist.csv',index=False);pd.DataFrame({'maximum_centered_annual_relative_log':maximum,'studentized_maximum_all':student_maximum,'studentized_maximum_screened':screened_maximum}).to_csv(P/'familywise_maxima.csv',index=False)
(P/'familywise_metadata.json').write_text(json.dumps(dict(pairs=count,draws=499,seed=20260913,block_sessions=20,method='nonoverlapping Gaussian block multiplier maximum over all discovery-rankable pairs',caution='Approximate historical selection adjustment; not a pristine holdout or proof of persistence.',unstudentized_quantile_95=float(np.quantile(maximum,.95)),studentized_full_quantile_95=float(np.quantile(student_maximum,.95)),studentized_screened_quantile_95=float(np.quantile(screened_maximum,.95))),indent=2));print('DONE',count,flush=True)
