from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;rows=[]
for sym in ['SPY','IWM']:
 f=P/f'{sym.lower()}_hedge_trades.parquet'
 if not f.exists():continue
 u=pd.read_parquet(P.parent/f'trend_replication_2026_07/data/etf/{sym}_1m.parquet');et=u.index.tz_convert('America/New_York');u['day']=et.strftime('%Y-%m-%d');u['minute']=et.hour*60+et.minute;u['sq']=np.log(u.close/u.groupby("day").close.shift())**2
 first=u[u.minute.between(570,599)].groupby('day').sq.mean();late=u[u.minute.between(600,884)].groupby('day').sq.mean();forecast=.5*first+.5*late.rolling(20,min_periods=20).mean().shift();x=pd.read_parquet(f);x['forecast']=x.day.map(forecast);x['variance_ratio']=x.forecast/(x.iv_entry**2/(365*1440));x.to_parquet(P/f'{sym.lower()}_hedge_with_forecast.parquet')
 for threshold in [0.,1.,1.25,1.5]:
  selected=x if threshold==0 else x[x.variance_ratio>threshold]
  for (tenor,case,design),g in selected.groupby(['tenor','case','design']):
   for period,lo,hi in [('all','2021','2027'),('early','2021','2023'),('middle','2023','2025'),('recent','2025','2027')]:
    z=g[(g.day>=lo)&(g.day<hi)]
    for coverage in ['all_conservative','complete_exit']:
     zz=z if coverage=='all_conservative' else z[~z.missing_exit]
     if len(zz)==0:continue
     net=zz.sort_values('day').net;eq=net.cumsum();rows.append(dict(symbol=sym,threshold=threshold,tenor=tenor,case=case,design=design,period=period,coverage=coverage,n=len(zz),net=net.sum(),mean=net.mean(),worst=net.min(),dd=(eq-eq.cummax().clip(lower=0)).min(),wins=(net>0).mean(),missing_exit=zz.missing_exit.sum()))
pd.DataFrame(rows).to_csv(P/'volatility_gate_scores.csv',index=False);print('gate scores',len(rows))
