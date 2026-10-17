from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;R=P.parent
a=pd.read_csv(R/'spx_spread_bot/backtests/gold_london/data/mgc_1m.csv',index_col=0);a.index=pd.to_datetime(a.index,utc=True);b=pd.read_parquet(R/'gold_research_2026_07/data/mgc_1m_topup_2026.parquet');a=pd.concat([a,b[['open','high','low','close','volume']]]).sort_index();a=a[~a.index.duplicated(keep='first')];t=a.index.tz_convert('America/New_York');a['day']=t.strftime('%Y-%m-%d');a['minute']=t.hour*60+t.minute;dates=set(pd.to_datetime(d,format='%m%d%Y').strftime('%Y-%m-%d') for d in json.loads((P/'sources/jobs_dates.json').read_text()));a=a[a.day.isin(dates)&(a.day<='2026-06-02')&a.minute.between(509,545)];rows=[]
for day,q in a.groupby('day'):
 q=q.set_index('minute')
 if not {509,511}.issubset(q.index):continue
 imp=q.loc[511,'close']-q.loc[509,'close']
 if imp==0:continue
 side=-np.sign(imp)
 for enminute in [513,514,515]:
  if enminute not in q.index:continue
  en=q.loc[enminute,'open']
  for case,fee,slip in [('base',3.,2.),('stress',6.,4.)]:
   for model in ['minute_close','intrabar_bracket']:
    ex=None;reason='time';ambiguous=False;wanted=540
    for m,bar in q.loc[enminute:539].iterrows():
     if model=='minute_close':
      pnl=side*(bar.close-en)*10-fee-slip
      if pnl>=30 or pnl<=-30:wanted=m+1;reason='target' if pnl>=30 else 'stop';break
     else:
      adverse=bar.low if side>0 else bar.high;favorable=bar.high if side>0 else bar.low;st=side*(adverse-en)*10-fee-slip<=-30;tp=side*(favorable-en)*10-fee-slip>=30
      if st or tp:
       ambiguous=st and tp;reason='stop' if st else 'target';level=en+side*((-30 if st else 30)+fee+slip)/10;ex=(min(bar.open,level) if side>0 else max(bar.open,level)) if st else level;wanted=m;break
    if ex is None:
     after=q.loc[wanted:wanted+5];missing=after.empty;ex=en-side*20 if missing else after.iloc[0].open;exitminute=wanted if missing else after.index[0]
    else:missing=False;exitminute=wanted
    gross=side*(ex-en)*10;rows.append(dict(day=day,entry=enminute,case=case,model=model,side=side,entry_price=en,exit_price=ex,exit_minute=exitminute,net=gross-fee-slip,gross=gross,fees=fee,slippage=slip,reason=reason,ambiguous=ambiguous,missing=missing))
x=pd.DataFrame(rows);x.to_parquet(P/'jobs_candidate_trades.parquet');out=[]
for (entry,case,model),g in x.groupby(['entry','case','model']):
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2019','2027')]:
  q=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=q.net
  if len(z)==0:continue
  cum=z.cumsum();w=z[z>0];l=z[z<0];out.append(dict(entry=entry,case=case,model=model,period=period,trades=len(z),net=z.sum(),mean=z.mean(),win=(z>0).mean(),avg_win=w.mean(),avg_loss=l.mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=w.sum()/-l.sum() if len(l) else np.nan,ambiguous=q.ambiguous.sum(),**{'y'+y:p for y,p in z.groupby(q.day.str[:4]).sum().items()}))
pd.DataFrame(out).to_csv(P/'jobs_candidate_scores.csv',index=False);print('candidate complete',flush=True)
