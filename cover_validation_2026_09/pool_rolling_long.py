from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;pool=pd.concat([pd.read_csv(P/'verification_pool_extended.csv'),pd.read_csv(P/'verification_pool_expanded.csv'),pd.read_csv(P/'verification_pool_long.csv')]).drop_duplicates(['a','b']);frames={}
for f in (P/'long_daily').glob('*.parquet'):
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);frames[f.stem]=d['Adj Close']
q=pd.DataFrame(frames).sort_index();rows=[]
for _,pair in pool.iterrows():
 a,b=pair.a,pair.b
 if a not in q or b not in q:continue
 z=q[[a,b]];starts=pd.Series(np.arange(len(z)),index=z.index).groupby(z.index.to_period('M')).last().to_numpy()
 for years in [1,2,3,5,10]:
  profits=[];advs=[];both=[]
  for st in starts:
   en=st+252*years
   if en>=len(z):continue
   v=z.iloc[st:en+1].to_numpy()
   if not np.isfinite(v).all() or (v<=0).any():continue
   g=v[1:]/v[:-1];c=.001;net=g.mean(axis=1)-c*abs(g[:,0]-g[:,1])/2;net[-1]=g[-1].mean()*(1-c);s=np.log(net).sum()-np.log1p(c);ind=v[-1]/v[0]*(1-c)/(1+c);h=np.log(ind.mean());actual=(z.index[en]-z.index[st]).days/365.25;profits.append(np.expm1(s/actual));advs.append((s-h)/actual);both.append(s>np.log(ind.max()))
  if profits:rows.append(dict(a=a,b=b,horizon_years=years,windows=len(profits),beat_hold_fraction=float((np.array(advs)>0).mean()),beat_both_fraction=float(np.mean(both)),positive_fraction=float((np.array(profits)>0).mean()),median_cagr=float(np.median(profits)),worst_cagr=float(np.min(profits)),median_log_advantage=float(np.median(advs)),worst_log_advantage=float(np.min(advs))))
pd.DataFrame(rows).to_csv(P/'pool_long_rolling.csv',index=False);print('DONE',len(rows),flush=True)
