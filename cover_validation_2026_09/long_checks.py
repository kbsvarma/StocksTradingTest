from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
pool=pd.concat([pd.read_csv(P/'verification_pool_extended.csv'),pd.read_csv(P/'verification_pool_expanded.csv'),pd.read_csv(P/'verification_pool_long.csv')]).drop_duplicates(['a','b'])
frames={}
for f in (P/'long_daily').glob('*.parquet'):
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);frames[f.stem]=d['Adj Close']
q=pd.DataFrame(frames).sort_index();rows=[]
for _,pair in pool.iterrows():
 a,b=pair.a,pair.b
 if a not in q or b not in q:continue
 z=q[[a,b]];good=z.notna().all(axis=1);z=z.loc[good.idxmax():];z=z.loc[:good[good].index[-1]]
 if z.isna().any().any():continue
 for label,lo,hi in [('longest','2000-01-01','2026-09-12'),('pre2026','2000-01-01','2025-12-31'),('22year','2004-09-11','2026-09-12'),('gfc','2007-01-01','2009-12-31'),('discovery','2021-12-31','2025-12-31'),('confirmation','2025-12-31','2026-09-12')]:
  x=z.loc[lo:hi]
  if len(x)<100:continue
  if label in ['22year','gfc'] and x.index[0]>pd.Timestamp(lo)+pd.Timedelta(days=7):continue
  g=x.to_numpy()[1:]/x.to_numpy()[:-1];years=(x.index[-1]-x.index[0]).days/365.25
  for bp in [0,5,10,25,50]:
   c=bp/10000;net=g.mean(axis=1)-c*np.abs(g[:,0]-g[:,1])/2;net[-1]=g[-1].mean()*(1-c);nav=np.r_[1/(1+c),np.cumprod(net)/(1+c)];individual=x.to_numpy()/x.iloc[0].to_numpy()/(1+c);individual[-1]*=1-c;hold=individual.mean(axis=1);draw=nav/np.maximum.accumulate(np.r_[1.,nav])[1:]-1
   bonus=np.log(g.mean(axis=1))-.5*np.log(g).sum(axis=1);top=np.sort(bonus)[-10:].sum();adv=np.log(nav[-1]/hold[-1])/years
   below=draw<-.000001;run=0;longest=0
   for v in below:run=run+1 if v else 0;longest=max(longest,run)
   rows.append(dict(a=a,b=b,period=label,cost_bps=bp,start=str(x.index[0].date()),end=str(x.index[-1].date()),years=years,cagr=nav[-1]**(1/years)-1,hold_cagr=hold[-1]**(1/years)-1,best_stock_cagr=individual[-1].max()**(1/years)-1,terminal_1000=1000*nav[-1],maxdd=draw.min(),hold_maxdd=(hold/np.maximum.accumulate(np.r_[1.,hold])[1:]-1).min(),annual_log_advantage=adv,beats_both=nav[-1]>individual[-1].max(),underwater_sessions=longest,top10_bonus_share=top/bonus.sum() if bonus.sum() else 0,annual_log_advantage_after_removing_top10_bonus=adv-top/years))
pd.DataFrame(rows).to_csv(P/'long_period_checks.csv',index=False);print('DONE',len(rows),len(pool),flush=True)
