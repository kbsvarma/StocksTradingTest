from pathlib import Path
import pandas as pd,numpy as np
import pandas_market_calendars as mcal
schedule=mcal.get_calendar("NYSE").schedule("2019-06-01","2026-05-01");regular=set(schedule.index[schedule.market_close.dt.tz_convert("America/New_York").dt.hour==16].strftime("%Y-%m-%d"))
P=Path(__file__).resolve().parent;rows=[];coverage=[]
for sym,mult,tick in [('MES',5,1.25),('MGC',10,1),('M6E',12500,1.25)]:
 a=pd.read_parquet(P.parent/'session_edge_2026_09/data'/f'{sym}.parquet');a=a[a.volume>0];local=a.index.tz_convert('America/New_York');cl=a.close[(local.hour==15)&(local.minute==55)];trend=cl.rolling(20).mean()
 for day in pd.bdate_range('2019-07-01','2026-05-01'):
  ds=str(day.date())
  if ds not in regular:continue
  for window,clock,offset,end in [('post_close','16:05',1,'09:25'),('reopen','18:05',1,'09:25'),('europe','03:05',0,'09:25'),('daytime','09:35',0,'15:55')]:
   if window=='post_close' and (day.dayofweek==0 or str((day-pd.Timedelta(days=1)).date()) not in regular):continue
   start=pd.Timestamp(str((day-pd.Timedelta(days=offset)).date())+' '+clock,tz='America/New_York').tz_convert('UTC');stop=pd.Timestamp(ds+' '+end,tz='America/New_York').tz_convert('UTC')
   if start not in a.index:coverage.append(dict(symbol=sym,window=window,day=ds,entry=False));continue
   entry=a.at[start,'open'];j=a.index.searchsorted(stop);missing=j==len(a) or a.index[j]>stop+pd.Timedelta(minutes=5);exitprice=np.nan if missing else a.open.iloc[j];gross=-max(200,entry*mult*.05) if missing else (exitprice-entry)*mult
   ref=trend.index.searchsorted(start)-1;up=ref>=0 and np.isfinite(trend.iloc[ref]) and entry>trend.iloc[ref];low=a.loc[start:stop].low.min();mae=min(0,(low-entry)*mult,gross if not missing else 0)
   coverage.append(dict(symbol=sym,window=window,day=ds,entry=True,missing=missing))
   for gate in ['all','above20']:
    if gate=='above20' and not up:continue
    for case,k in [('base',1),('stress',2)]:rows.append(dict(symbol=sym,window=window,gate=gate,case=case,day=ds,entry_time=start,exit_time=None if missing else a.index[j],entry=entry,exit=exitprice,gross=gross,cost=k*(3+2*tick),net=gross-k*(3+2*tick),mae=mae,missing=missing))
 print(sym,flush=True)
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');pd.DataFrame(coverage).to_csv(P/'coverage.csv',index=False);out=[]
for key,g in x.groupby(['symbol','window','gate','case']):
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027')]:
  t=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=t.net;c=z.cumsum()
  out.append(dict(zip(['symbol','window','gate','case'],key))|dict(period=period,n=len(z),net=z.sum(),mean=z.mean(),worst=z.min(),mae=t.mae.min(),dd=(c-c.cummax().clip(lower=0)).min(),missing=t.missing.sum()))
pd.DataFrame(out).to_csv(P/'scores.csv',index=False)
