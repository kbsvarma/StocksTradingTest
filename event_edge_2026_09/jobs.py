from pathlib import Path
import pandas as pd,numpy as np,re,json
P=Path(__file__).resolve().parent;R=P.parent
P=P/'gold_jobs';P.mkdir(exist_ok=True)
dates=sorted(pd.to_datetime(d,format='%m%d%Y').strftime('%Y-%m-%d') for d in json.loads((P.parent/'sources/jobs_dates.json').read_text()));all_events=set(dates);events=all_events-{'2024-05-15'};end='2026-06-02';pd.DataFrame({'day':dates}).to_csv(P/'jobs_calendar.csv',index=False)
a=pd.read_csv(R/'spx_spread_bot/backtests/gold_london/data/mgc_1m.csv',index_col=0);a.index=pd.to_datetime(a.index,utc=True);b=pd.read_parquet(R/'gold_research_2026_07/data/mgc_1m_topup_2026.parquet');a=pd.concat([a,b[['open','high','low','close','volume']]]).sort_index();a=a[~a.index.duplicated(keep='first')];a['instrument_id']=0;assert a.index.is_unique;t=a.index.tz_convert('America/New_York');a['day']=t.strftime('%Y-%m-%d');a['minute']=t.hour*60+t.minute;a=a[(a.day>='2019')&(a.day<=end)];opens=a.pivot(index='day',columns='minute',values='open');closes=a.pivot(index='day',columns='minute',values='close');ids=a.pivot(index='day',columns='minute',values='instrument_id');features=pd.DataFrame(index=opens.index);features['pre']=closes[509]-opens[495]
zn=pd.concat([pd.read_parquet(f) for f in sorted((R/'trend_replication_2026_07/data').glob('ZN_1m_*.parquet'))]).sort_index();zt=zn.index.tz_convert('America/New_York');zn['day']=zt.strftime('%Y-%m-%d');zn['minute']=zt.hour*60+zt.minute;zn=zn[zn.minute.isin([509,511,514])];zc=zn.pivot(index='day',columns='minute',values='close')
for n in [2,5]:
 features[f'rate{n}']=zc[509+n]-zc[509]
 features[f'imp{n}']=closes[509+n]-closes[509];features[f'high{n}']=features[f'imp{n}'].abs()>features[f'imp{n}'].abs().dropna().rolling(20).median().shift().reindex(features.index)
rows=[];audit=[]
for day,f in features.iterrows():
 weekday=pd.Timestamp(day).dayofweek
 group='event' if day in events else ('control' if weekday in [1,3] and day not in all_events else None)
 if group is None:continue
 for n in [2,5]:
  impulse=f[f'imp{n}'];entry=510+n+1
  if not np.isfinite(impulse) or impulse==0 or not np.isfinite(opens.loc[day,entry]):continue
  filters={'all':True,'large_impulse':bool(f[f'high{n}']),'pre_aligned':f.pre*impulse>0,'pre_opposed':f.pre*impulse<0,'rates_confirm':f[f'rate{n}']*impulse>0,'rates_disagree':f[f'rate{n}']*impulse<0}
  if n==5:filters['failed_impulse']=f.imp2*f.imp5>=0 and abs(f.imp5)<=.5*abs(f.imp2) and f.imp2!=0
  for filt,yes in filters.items():
   if not yes:continue
   raw=f.imp2 if filt=='failed_impulse' else impulse
   for side,name in [(1,'follow'),(-1,'fade')]:
    direction=side*np.sign(raw);en=opens.loc[day,entry];path=closes.loc[day];contract=ids.loc[day,entry]
    for case,fee,slip in [('base',3.,2.),('stress',6.,4.)]:
     for hold in [15,30,60]:
      endminute=510+hold;ok=opens.loc[day].loc[endminute:endminute+5].dropna();missing=ok.empty;ex=ok.iloc[0] if not missing else en-direction*20.;exitminute=int(ok.index[0]) if not missing else endminute;roll=(ids.loc[day,entry:exitminute].dropna()!=contract).any()
      if roll:raise ValueError((day,'intraday roll'))
      net=direction*(ex-en)*10-fee-slip
      rows.append(dict(day=day,group=group,n=n,filter=filt,direction=name,case=case,policy=f'time{hold}',entry=entry,exit_minute=exitminute,entry_price=en,exit_price=ex,net=net,gross=net+fee+slip,fees=fee,slippage=slip,missing_exit=missing))
     # Quick exits use completed-minute close then following minute open.
     for target in [15,30]:
      for stop in [30,60]:
       marks=direction*(path.loc[entry:539]-en)*10-fee-slip;hit=marks[(marks>=target)|(marks<=-stop)].dropna();reason='time';wanted=540
       if len(hit):wanted=int(hit.index[0])+1;reason='target' if hit.iloc[0]>=target else 'stop'
       ok=opens.loc[day].loc[wanted:wanted+5].dropna();missing=ok.empty;ex=ok.iloc[0] if not missing else en-direction*20.;exitminute=int(ok.index[0]) if not missing else wanted
       if (ids.loc[day,entry:exitminute].dropna()!=contract).any():raise ValueError((day,'intraday roll'))
       net=direction*(ex-en)*10-fee-slip;rows.append(dict(day=day,group=group,n=n,filter=filt,direction=name,case=case,policy=f'tp{target}_sl{stop}',entry=entry,exit_minute=exitminute,entry_price=en,exit_price=ex,net=net,gross=net+fee+slip,fees=fee,slippage=slip,missing_exit=missing,reason=reason))
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');out=[];keys=['group','n','filter','direction','case','policy']
for key,g in x.groupby(keys):
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2019','2027')]:
  q=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=q.net
  if len(z)==0:continue
  cum=z.cumsum();wins=z[z>0];loss=z[z<0];years=z.groupby(q.day.str[:4]).sum();out.append(dict(zip(keys,key))|dict(period=period,trades=len(z),net=z.sum(),mean=z.mean(),win=(z>0).mean(),avg_win=wins.mean(),avg_loss=loss.mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=wins.sum()/-loss.sum() if len(loss) else np.nan,missing=q.missing_exit.sum(),**{'y'+y:p for y,p in years.items()}))
pd.DataFrame(out).to_csv(P/'scores.csv',index=False);print('done',len(x),'event dates',x[x.group=='event'].day.nunique(),'control dates',x[x.group=='control'].day.nunique(),flush=True)
