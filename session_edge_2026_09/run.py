from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent
S={'MGC':('spx_spread_bot/backtests/gold_london/data/mgc_1m.csv',10,.1),'MES':('mes_orb_bot/data/mes_1min.csv',5,.25),'M6E':('spx_spread_bot/backtests/m6e_london/data/m6e_1m.csv',12500,.0001)}
WINDOWS={'tokyo':('Asia/Tokyo','09:05'),'london_open':('Europe/London','08:05'),'london_late':('Europe/London','16:05'),'ny_open':('America/New_York','09:35'),'ny_afternoon':('America/New_York','13:35'),'reopen':('America/New_York','18:05')}
rows=[];audit=[]
for sym,(file,mult,tick) in S.items():
 a=pd.read_csv(R/file,index_col=0);a.index=pd.to_datetime(a.index,utc=True);a=a.sort_index();a=a[~a.index.duplicated(keep='last')];a=a[(a.index>='2019-06-01')&(a.index<'2026-05-02')];a.to_parquet(P/'data'/f'{sym}.parquet')
 for window,(tz,clock) in WINDOWS.items():
  dates=pd.date_range('2019-07-01','2026-05-01',freq='D');dates=dates[dates.dayofweek.isin([6,0,1,2,3] if window=='reopen' else [0,1,2,3,4])];times=pd.DatetimeIndex([pd.Timestamp(str(d.date())+' '+clock,tz=tz).tz_convert('UTC') for d in dates]);entry=a.open.reindex(times);volume=a.volume.reindex(times);features={n:a.close.reindex(times-pd.Timedelta(minutes=2)).to_numpy()-a.close.reindex(times-pd.Timedelta(minutes=n+2)).to_numpy() for n in [5,30]};signs={'long':np.ones(len(times)),'short':-np.ones(len(times)),**{f'{label}{n}':np.sign(features[n])*v for n in [5,30] for label,v in [('follow',1),('fade',-1)]}}
  for hold in [30,60]:
   wanted=times+pd.Timedelta(minutes=hold);pos=a.index.get_indexer(wanted,method='bfill');validpos=pos>=0;safe=np.maximum(pos,0);delay=(a.index[safe]-wanted).total_seconds()/60;validexit=validpos&(delay<=5);exitprice=a.open.iloc[safe].to_numpy()
   for design,sign in signs.items():
    eligible=entry.notna().to_numpy()&(volume.to_numpy()>0)&np.isfinite(sign)&(sign!=0);gross=sign*(exitprice-entry.to_numpy())*mult;gross=np.where(validexit,gross,-200.);idx=np.where(eligible)[0]
    for case,k in [('base',1),('stress',2)]:
     fee=3*k;slip=2*tick*mult*k
     rows.extend(dict(symbol=sym,window=window,hold=hold,design=design,case=case,day=str(dates[j].date()),entry_time=times[j],entry_price=entry.iloc[j],exit_price=exitprice[j] if validexit[j] else np.nan,exit_delay=delay[j] if validexit[j] else np.nan,missing_exit=not validexit[j],gross=gross[j],fees=fee,slippage=slip,net=gross[j]-fee-slip) for j in idx)
  audit.append(dict(symbol=sym,window=window,scheduled=len(times),entry_available=int((entry.notna()&(volume>0)).sum())))
 print(sym,'complete',flush=True)
x=pd.DataFrame(rows);x.to_parquet(P/'results/trades.parquet');pd.DataFrame(audit).to_csv(P/'coverage.csv',index=False);scores=[]
for key,g in x.groupby(['symbol','window','hold','design','case']):
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2019','2027')]:
  q=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=q.net
  if len(z)==0:continue
  w=z[z>0];l=z[z<0];cum=z.cumsum();scores.append(dict(zip(['symbol','window','hold','design','case'],key))|dict(period=period,n=len(z),net=z.sum(),mean=z.mean(),win=(z>0).mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=w.sum()/-l.sum() if len(l) else np.nan,missing=q.missing_exit.sum(),**{'y'+year:p for year,p in z.groupby(q.day.str[:4]).sum().items()}))
pd.DataFrame(scores).to_csv(P/'results/scores.csv',index=False);print('done',len(x),flush=True)
