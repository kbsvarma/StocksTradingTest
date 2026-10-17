from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;local=pd.read_parquet(P/'expanded_eligible_prices.parquet');vendor=pd.read_parquet(P/'vendor_hybrid_adjusted.parquet');pool=pd.concat([pd.read_csv(P/'verification_pool_extended.csv'),pd.read_csv(P/'verification_pool_expanded.csv'),pd.read_csv(P/'verification_pool_long.csv')]).drop_duplicates(['a','b']);rows=[]
def terminal(z,c=.001):
 g=z.to_numpy()[1:]/z.to_numpy()[:-1];n=g.mean(axis=1)-c*np.abs(g[:,0]-g[:,1])/2;n[-1]=g[-1].mean()*(1-c);return np.prod(n)/(1+c)
for _,r in pool.iterrows():
 sy=[r.a,r.b]
 if not set(sy)<=set(vendor.columns):continue
 for label,lo,hi in [('discovery','2021-12-31','2025-12-31'),('confirmation','2025-12-31','2026-09-11')]:
  l=local[sy].loc[lo:hi];v=vendor[sy].reindex(l.index);missing=v.isna().sum();h=v.combine_first(l);years=(l.index[-1]-l.index[0]).days/365.25
  if l.isna().any().any():continue
  lt=terminal(l);vt=terminal(h);rows.append(dict(a=r.a,b=r.b,period=label,sessions=len(l),vendor_missing_a=int(missing.iloc[0]),vendor_missing_b=int(missing.iloc[1]),local_cagr=lt**(1/years)-1,vendor_hybrid_cagr=vt**(1/years)-1,terminal_relative_difference=vt/lt-1,fully_independent_nominal=bool(missing.sum()==0),shared_local_adjustment_factors=True))
pd.DataFrame(rows).to_csv(P/'pair_price_verification.csv',index=False);print('DONE',len(rows),flush=True)
