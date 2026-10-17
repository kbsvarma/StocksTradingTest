from pathlib import Path
import json
import numpy as np
import pandas as pd
from scipy.stats import t
P=Path(__file__).resolve().parent; R=P.parent
u=pd.read_parquet(R/'opening_leader_2026_07/data/universe.parquet')
panel=pd.read_parquet(R/'volatile_direction_2026_07/data/pit_panel_2018_2026.parquet',columns=['raw_symbol','day','security_type'])
panel['day']=pd.to_datetime(panel.day)
classes={s:g.sort_values('day').set_index('day').security_type for s,g in panel.groupby('raw_symbol')}
def read(s):
 p=P/'data'/f'{dict(FB="META").get(s,s)}.parquet'
 if not p.exists(): return None
 b=pd.read_parquet(p);b.index=pd.to_datetime(b.index.date);return b[~b.index.duplicated()]
spy=read('SPY'); dates=spy.index
bs={s:read(s) for s in u.symbol.unique()}; arrays={}
for s,b in bs.items():
 if b is None:continue
 b=b.reindex(dates)
 div=b.Dividends.fillna(0); prev=b.Close.shift()
 on=(b.Open+div)/prev-1; cc=(b.Close+div)/prev-1
 splits=b['Stock Splits'].replace(0,1).fillna(1)
 nominal=b.Close*(splits.iloc[::-1].cumprod().iloc[::-1]/splits)
 arrays[s]=(b,on,cc,nominal)
selection=[];coverage=[];holdings={}
for month,ug in u.groupby('month'):
 cutoff=dates[dates<pd.Timestamp(month+'-01')][-1];window=dates[dates<=cutoff][-252:]
 eligible=[]
 for s in ug.symbol:
  cl=classes.get(s);cl=cl[cl.index<=cutoff] if cl is not None else pd.Series(dtype=str)
  if len(cl)==0 or cl.iloc[-1]!='C':continue
  reason='missing_history'; row={'month':month,'symbol':s}
  if s in arrays:
   b,on,cc,nominal=arrays[s]
   y=np.log1p(on.loc[window]);x=np.log1p(cc.loc[window]);valid=np.isfinite(y)&np.isfinite(x)
   if valid.sum()<240:reason='short_history'
   elif not np.isfinite(nominal.loc[cutoff]) or nominal.loc[cutoff]<5:reason='price_filter'
   else:
    x=x[valid].to_numpy();y=y[valid].to_numpy();n=len(x)
    xc=x-x.mean();sxx=xc@xc
    beta=xc@(y-y.mean())/sxx; alpha=y.mean()-beta*x.mean()
    residual=y-alpha-beta*x
    se=np.sqrt((residual@residual)/(n-2)*(1/n+x.mean()**2/sxx))
    sig=alpha>0 and alpha/se>t.ppf(.95,n-2)
    row.update(alpha=alpha,tstat=alpha/se,mean_overnight=y.mean(),n=n,obg=sig)
    eligible.append(row);reason='eligible'
  coverage.append(dict(month=month,symbol=s,reason=reason))
 eligible=sorted(eligible,key=lambda z:(-z['mean_overnight'],z['symbol']))
 top={r['symbol'] for r in eligible[:max(1,int(np.ceil(len(eligible)*.1))) ]}
 for r in eligible:r['top_decile']=r['symbol'] in top;selection.append(r)
 holdings[month]={'OBG':[r['symbol'] for r in eligible if r['obg']], 'TopDecile':[r['symbol'] for r in eligible if r['top_decile']], 'Universe':[r['symbol'] for r in eligible], 'SPYovernight':['SPY']}
pd.DataFrame(selection).to_csv(P/'selection.csv',index=False);pd.DataFrame(coverage).to_csv(P/'coverage.csv',index=False)
arrays['SPY']=(spy,(spy.Open+spy.Dividends)/spy.Close.shift()-1,(spy.Close+spy.Dividends)/spy.Close.shift()-1,spy.Close)
rows=[];trades=[]
for i,entry in enumerate(dates[:-1]):
 month=entry.strftime('%Y-%m')
 if month not in holdings:continue
 exit=dates[i+1]
 for strategy,names in holdings[month].items():
  gross=weight=0.;missing=0;entered=0
  for s in names:
   b,on,cc,nominal=arrays[s];w=1/len(names)
   if not np.isfinite(b.Close.loc[entry]) or b.Close.loc[entry]<=0:continue
   ret=on.loc[exit]
   if not np.isfinite(ret):ret=-1.;missing+=1
   gross+=w*ret;weight+=w;entered+=1
   trades.append(dict(entry=entry,exit=exit,strategy=strategy,symbol=s,weight=w,ret=ret,missing_exit=not np.isfinite(on.loc[exit])))
  rows.append(dict(entry=entry,exit=exit,strategy=strategy,gross=gross,weight=weight,names=len(names),entered=entered,missing_exit=missing))
 rows.append(dict(entry=entry,exit=exit,strategy='SPYbuyhold',gross=arrays['SPY'][2].loc[exit],weight=0,names=1,entered=0,missing_exit=0))
daily=pd.DataFrame(rows);daily.to_csv(P/'daily.csv',index=False);trades=pd.DataFrame(trades);trades.to_parquet(P/'trades.parquet',index=False)
def stats(g,cost):
 r=g.gross.to_numpy()-g.weight.to_numpy()*cost/10000;eq=np.cumprod(1+r);peak=np.maximum.accumulate(np.r_[1,eq])[1:]
 return dict(days=len(r),cagr=(eq[-1]**(252/len(r))-1)*100,total=(eq[-1]-1)*100,mean_bps=r.mean()*10000,vol=r.std(ddof=1)*np.sqrt(252)*100,sharpe=r.mean()/r.std(ddof=1)*np.sqrt(252),dd=(eq/peak-1).min()*100,win=(r>0).mean()*100,avg_names=g.names.mean(),round_trips=int(g.entered.sum()),missing_exits=int(g.missing_exit.sum()))
summ=[]
for strategy,g in daily.groupby('strategy'):
 for period,start in [('All','2018-01-01'),('2022+','2022-01-01'),('2025+','2025-01-01')]:
  h=g[g.entry>=pd.Timestamp(start)]
  for cost in ([0] if strategy=='SPYbuyhold' else [0,3,10,20]):summ.append(dict(strategy=strategy,period=period,cost_bps=cost,**stats(h,cost)))
pd.DataFrame(summ).to_csv(P/'summary.csv',index=False)
annual=[]
for (strategy,year),g in daily.groupby(['strategy',daily.entry.dt.year]):
 for cost in ([0] if strategy=='SPYbuyhold' else [0,3,10,20]):annual.append(dict(strategy=strategy,year=year,cost_bps=cost,**stats(g,cost)))
pd.DataFrame(annual).to_csv(P/'annual.csv',index=False)
# Stationary-calendar block bootstrap of mean differential, 20-session circular blocks.
pivot=daily.pivot(index='entry',columns='strategy',values='gross');rng=np.random.default_rng(20260912);unc=[]
for period,start in [('All','2018-01-01'),('2022+','2022-01-01'),('2025+','2025-01-01')]:
 q=pivot.loc[start:]
 for strategy in ['OBG','TopDecile']:
  a=(q[strategy]-q.Universe).to_numpy(); n=len(a);means=[]
  for j in range(5000):
   ix=((rng.integers(n,size=int(np.ceil(n/20)))[:,None]+np.arange(20))%n).ravel()[:n];means.append(a[ix].mean()*10000)
  unc.append(dict(period=period,strategy=strategy,mean_bps=a.mean()*10000,low=np.percentile(means,2.5),high=np.percentile(means,97.5)))
pd.DataFrame(unc).to_csv(P/'uncertainty.csv',index=False)
trades['contribution']=trades.weight*trades.ret
trades.groupby(['strategy','symbol']).agg(n=('ret','size'),mean_bps=('ret',lambda x:x.mean()*10000),sum_contribution=('contribution','sum')).to_csv(P/'attribution.csv')
print(pd.DataFrame(summ).query('cost_bps == 3 or strategy == "SPYbuyhold"').to_string(index=False))
print(pd.DataFrame(coverage).reason.value_counts());print(pd.DataFrame(unc).to_string(index=False))
print('LARGEST SINGLE STOCK RETURNS');print(trades.loc[trades.ret.abs().nlargest(12).index].to_string(index=False))
