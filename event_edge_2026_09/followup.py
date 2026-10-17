from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;R=P.parent
a=pd.read_csv(R/'spx_spread_bot/backtests/gold_london/data/mgc_1m.csv',index_col=0);a.index=pd.to_datetime(a.index,utc=True);b=pd.read_parquet(R/'gold_research_2026_07/data/mgc_1m_topup_2026.parquet');a=pd.concat([a,b[['open','high','low','close','volume']]]).sort_index();a=a[~a.index.duplicated(keep='first')];t=a.index.tz_convert('America/New_York');a['day']=t.strftime('%Y-%m-%d');a['minute']=t.hour*60+t.minute;a=a[(a.day>='2019')&(a.day<='2026-06-02')&a.minute.between(495,560)];op=a.pivot(index='day',columns='minute',values='open');cl=a.pivot(index='day',columns='minute',values='close');lo=a.pivot(index='day',columns='minute',values='low');hi=a.pivot(index='day',columns='minute',values='high');vol=a.pivot(index='day',columns='minute',values='volume');cal={name:set(pd.to_datetime(d,format='%m%d%Y').strftime('%Y-%m-%d') for d in json.loads((P/'sources'/file).read_text())) for name,file in [('CPI','cpi_dates.json'),('JOBS','jobs_dates.json')]};cal['CPI'].discard('2024-05-15');rows=[]
for event,dates in cal.items():
 for day in sorted(dates & set(op.index)):
  if not np.isfinite([cl.loc[day,509],op.loc[day,495],cl.loc[day,511]]).all():continue
  pre=cl.loc[day,509]-op.loc[day,495];imp=cl.loc[day,511]-cl.loc[day,509]
  if pre*imp<=0:continue
  side=-np.sign(imp)
  for entry in [513,514,515]:
   en=op.loc[day,entry]
   if not np.isfinite(en):continue
   for case,fee,slip in [('base',3.,2.),('stress',6.,4.)]:
    for stop in [None,30,60]:
     wanted=525;reason='time'
     if stop is not None:
      z=side*(cl.loc[day,entry:524]-en)*10-fee-slip;hits=z[z<=-stop].dropna()
      if len(hits):wanted=int(hits.index[0])+1;reason='stop'
     exits=op.loc[day,wanted:wanted+5].dropna();missing=exits.empty;exitminute=wanted if missing else int(exits.index[0]);ex=en-side*20 if missing else exits.iloc[0];gross=side*(ex-en)*10;net=gross-fee-slip;adverse=(lo if side>0 else hi).loc[day,entry:exitminute-1];mae=(side*(adverse-en)*10-fee-slip).min();rows.append(dict(event=event,day=day,entry=entry,stop=stop or 0,case=case,net=net,gross=gross,fees=fee,slippage=slip,entry_price=en,exit_price=ex,exit_minute=exitminute,side=side,pre=pre,impulse=imp,mae=mae,entry_bar_volume=vol.loc[day,entry],reason=reason,missing=missing))
x=pd.DataFrame(rows);x.to_parquet(P/'followup_trades.parquet');out=[]
for (entry,stop,case),g in x.groupby(['entry','stop','case']):
 for event,q in [('CPI',g[g.event=='CPI']),('JOBS',g[g.event=='JOBS']),('combined',g)]:
  for period,low,high in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2019','2027')]:
   v=q[(q.day>=low)&(q.day<high)].sort_values('day');z=v.net
   if len(z)==0:continue
   cum=z.cumsum();w=z[z>0];l=z[z<0];out.append(dict(entry=entry,stop=stop,case=case,event=event,period=period,trades=len(z),net=z.sum(),mean=z.mean(),win=(z>0).mean(),avg_win=w.mean(),avg_loss=l.mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),mae=v.mae.min(),pf=w.sum()/-l.sum() if len(l) else np.nan,**{'y'+y:p for y,p in z.groupby(v.day.str[:4]).sum().items()}))
pd.DataFrame(out).to_csv(P/'followup_scores.csv',index=False);print('followup complete',flush=True)
