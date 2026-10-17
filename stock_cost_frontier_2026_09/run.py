from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent
a=pd.read_parquet(R/'databento_fresh_2026_09/data/stock_morning_panel.parquet');a=a[a.mega];rows=[];legs=[];GROSS=100000.
for day,q in a.groupby('day'):
 if len(q)<20:continue
 q=q.copy();n=len(q);rank=q.r.rank(method='first');k=max(1,n//10);center=q.r-q.r.mean();weights={'decile':((rank>n-k).astype(float)-(rank<=k).astype(float))/(2*k),'continuous':center/center.abs().sum(),'long_control':pd.Series(1/n,index=q.index)}
 for design,w in weights.items():
  shares=np.trunc(w*GROSS/q.entry);grossleg=shares*(q.exit-q.entry).fillna(0)-(shares.abs()*q.entry*.10).where(q.missing_exit,0);gross=grossleg.sum();notional=(shares.abs()*q.entry).sum();turnover_dollars=(shares.abs()*(q.entry+q.exit.fillna(q.entry))).sum();share_sides=shares.abs().sum()*2
  for label,cents in [('one_cent',.01),('three_cents',.03),('five_cents',.05),('five_bps',None)]:
   fee=turnover_dollars*(.0005 if cents is None else .00005)+(0 if cents is None else cents*share_sides);rows.append(dict(day=day,design=design,cost=label,gross=gross,fees=fee,net=gross-fee,gross_notional=notional,share_sides=share_sides,turnover_dollars=turnover_dollars,missing_stocks=int((q.missing_exit&(shares!=0)).sum())))
  if design=='decile':
   legs.extend(dict(day=day,symbol=q.loc[i,'symbol'],shares=shares.loc[i],gross=grossleg.loc[i],entry=q.loc[i,'entry'],exit=q.loc[i,'exit']) for i in q.index[shares!=0])
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');pd.DataFrame(legs).to_parquet(P/'legs.parquet');scores=[]
for (design,cost),g in x.groupby(['design','cost']):
 for period,lo,hi in [('development','2018','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2018','2027')]:
  q=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=q.net
  if not len(z):continue
  cum=z.cumsum();w=z[z>0];l=z[z<0];scores.append(dict(design=design,cost=cost,period=period,n=len(z),net=z.sum(),mean=z.mean(),gross_mean=q.gross.mean(),fees_mean=q.fees.mean(),win=(z>0).mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=w.sum()/-l.sum() if len(l) else np.nan,breakeven_cents_per_side=(q.gross.sum()-.00005*q.turnover_dollars.sum())/q.share_sides.sum()*100,**{'y'+y:p for y,p in z.groupby(q.day.str[:4]).sum().items()}))
pd.DataFrame(scores).to_csv(P/'scores.csv',index=False);print('done',len(x),flush=True)
