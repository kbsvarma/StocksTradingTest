from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;rng=np.random.default_rng(6092026);rows=[]
sets={'EIA_follow5_time60':(P/'trades.parquet',dict(group='event',n=5,filter='all',direction='follow',case='base',policy='time60')),'CPI_aligned_fade2_time15':(P/'gold/trades.parquet',dict(group='event',n=2,filter='pre_aligned',direction='fade',case='base',policy='time15')),'JOBS_fade2_tp30_sl30':(P/'gold_jobs/trades.parquet',dict(group='event',n=2,filter='all',direction='fade',case='base',policy='tp30_sl30'))}
for name,(f,conditions) in sets.items():
 a=pd.read_parquet(f);q=a.copy()
 for k,v in conditions.items():q=q[q[k]==v]
 q=q.sort_values('day');q.to_csv(P/(name+'_ledger.csv'),index=False)
 for period,lo,hi in [('pre_recent','2019','2025'),('recent','2025','2027'),('all','2019','2027')]:
  x=q[(q.day>=lo)&(q.day<hi)].copy();x['month']=x.day.str[:7];g=x.groupby('month').net.agg(['sum','count']);xx=g.to_numpy();means=[]
  for _ in range(10000):
   z=xx[rng.integers(0,len(xx),len(xx))].sum(axis=0);means.append(z[0]/z[1])
  low,high=np.quantile(means,[.025,.975]);rows.append(dict(design=name,period=period,n=len(x),mean=x.net.mean(),ci_low=low,ci_high=high,net=x.net.sum(),largest_win=x.net.max(),net_without_best=x.net.sum()-x.net.max()))
pd.DataFrame(rows).to_csv(P/'uncertainty.csv',index=False)
# Verify independent follow-up replay equals selected original path.
a=pd.read_parquet(P/'jobs_candidate_trades.parquet').query("entry==513 and case=='base' and model=='minute_close'").set_index('day');b=pd.read_csv(P/'JOBS_fade2_tp30_sl30_ledger.csv').set_index('day');assert a.index.equals(b.index);assert (a.net-b.net).abs().max()<1e-8
for f in [P/'trades.parquet',P/'gold/trades.parquet',P/'gold_jobs/trades.parquet',P/'jobs_candidate_trades.parquet',P/'followup_trades.parquet']:
 q=pd.read_parquet(f);assert (q.net-q.gross+q.fees+q.slippage).abs().max()<1e-8
print(pd.DataFrame(rows).round(2).to_string(index=False));print('Independent selected-rule replay and cashflow reconciliation passed.')
