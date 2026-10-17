from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent
u=pd.read_parquet(R/'trend_replication_2026_07/data/etf/IWM_1m.parquet');et=u.index.tz_convert('America/New_York');u['day']=et.strftime('%Y-%m-%d');u['minute']=et.hour*60+et.minute;under={m:u[u.minute==m].set_index('day')[c] for m,c in [(570,'open'),(574,'close'),(584,'close')]};rows=[];audit=[]
for i,f in enumerate(sorted((R/'spx_spread_bot/backtests/multi_ic_2026/data/iwm_wide').glob('*.parquet'))):
 day=str(pd.Timestamp(f.stem[-8:]).date())
 if day not in under[570].index:continue
 q=pd.read_parquet(f);et=q.index.tz_convert('America/New_York');q['minute']=et.hour*60+et.minute;p=q.symbol.str.extract(r'^IWM\s+(\d{6})([CP])(\d{8})$');q['expiry']=pd.to_datetime(p[0],format='%y%m%d',errors='coerce');q['right']=p[1];q['strike']=pd.to_numeric(p[2],errors='coerce')/1000;q=q[(q.expiry==pd.Timestamp(day))&q.minute.between(575,705)].copy();q['bid']=q.bid_px_00.where(q.bid_sz_00>=1,0).fillna(0);q['ask']=q.ask_px_00.where(q.ask_sz_00>=1);q=q[q.ask.ge(q.bid)&q.ask.gt(0)];q=q.drop_duplicates(['minute','symbol'],keep='last')
 for entry in [575,585]:
  if day not in under[entry-1].index:continue
  spot=float(under[entry-1].loc[day]);up=spot>=under[570].loc[day];e=q[q.minute==entry];ca=e[e.right=='C'].set_index('strike');pu=e[e.right=='P'].set_index('strike');common=ca.index.intersection(pu.index)
  if not len(common):continue
  k=min(common,key=lambda z:abs(z-spot))
  if abs(k-spot)>.51:continue
  for width in [3,5]:
   if k+width not in ca.index or k-width not in pu.index:audit.append(dict(day=day,entry=entry,width=width,status='missing_wing'));continue
   legs=[ca.loc[k,'symbol'],pu.loc[k,'symbol'],ca.loc[k+width,'symbol'],pu.loc[k-width,'symbol']];designs={'butterfly':([-1,-1,1,1]),'call_credit':([-1,0,1,0]),'put_credit':([0,-1,0,1]),'opening_follow':([0,-1,0,1] if up else [-1,0,1,0]),'opening_fade':([-1,0,1,0] if up else [0,-1,0,1])}
   for design,weights in designs.items():
    w=np.array(weights);active=w!=0;symbols=np.array(legs)[active];w=w[active];en=e.set_index('symbol').loc[symbols]
    if not (en.bid.to_numpy()[w<0]>0).all():continue
    credit=float((en.bid.to_numpy()[w<0].sum()-en.ask.to_numpy()[w>0].sum())*100)
    if not 0<credit<width*100:continue
    future=q[q.symbol.isin(symbols)&(q.minute>entry)];bid=future.pivot(index='minute',columns='symbol',values='bid').reindex(columns=symbols);ask=future.pivot(index='minute',columns='symbol',values='ask').reindex(columns=symbols);valid=bid.notna().all(axis=1)&ask.notna().all(axis=1);bid=bid[valid];ask=ask[valid];mins=bid.index.to_numpy();debit=(ask.to_numpy()[:,w<0].sum(axis=1)-bid.to_numpy()[:,w>0].sum(axis=1))*100;n=len(w)
    for case in ['base','stress']:
     fee=n*2*(.65 if case=='base' else 1.30);slip=n*2*(0 if case=='base' else 1.);mark=credit-debit-fee-slip
     for deadline in [630,690]:
      for target in [10,15,None]:
       reason='time';trigger=None;ix=None
       if target is not None:
        hit=np.where((mins<deadline)&((mark>=target)|(mark<=-50)))[0]
        if len(hit):
         j=hit[0];trigger=int(mins[j]);reason='target' if mark[j]>=target else 'stop'
         if j+1<len(mins) and mins[j+1]<=deadline+15:ix=j+1
       if ix is None:
        eligible=np.where((mins>=deadline)&(mins<=deadline+15))[0]
        if len(eligible):ix=eligible[0]
       missing=ix is None;pay=width*100 if missing else debit[ix];net=credit-pay-fee-slip;policy='time_only' if target is None else f'tp{target}_sl50';rows.append(dict(day=day,entry=entry,width=width,design=design,case=case,deadline=deadline,policy=policy,net=net,gross=credit-pay,fees=fee,slippage=slip,credit=credit,max_terminal_loss=width*100-credit+fee+slip,reason=reason,trigger=trigger,exit_minute=None if missing else int(mins[ix]),missing=missing,legs='|'.join(symbols),weights='|'.join(map(str,w)),strike=k,spot=spot))
   audit.append(dict(day=day,entry=entry,width=width,status='evaluated'))
 if i%200==0:print(i,len(rows),flush=True)
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');pd.DataFrame(audit).to_csv(P/'coverage.csv',index=False);scores=[]
for key,g in x.groupby(['entry','width','design','case','deadline','policy']):
 for period,lo,hi in [('early','2019','2024'),('development','2024','2025'),('validation','2025','2026'),('recent','2026','2027'),('all','2019','2027')]:
  q=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=q.net
  if len(z)==0:continue
  cum=z.cumsum();w=z[z>0];l=z[z<0];scores.append(dict(zip(['entry','width','design','case','deadline','policy'],key))|dict(period=period,n=len(z),net=z.sum(),mean=z.mean(),win=(z>0).mean(),avg_win=w.mean(),avg_loss=l.mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=w.sum()/-l.sum() if len(l) else np.nan,missing=q.missing.sum(),median_credit=q.credit.median(),**{'y'+year:v for year,v in z.groupby(q.day.str[:4]).sum().items()}))
pd.DataFrame(scores).to_csv(P/'scores.csv',index=False);print('done',len(x),flush=True)
