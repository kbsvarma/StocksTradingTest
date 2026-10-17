from pathlib import Path
import pandas as pd,numpy as np,sys
BASE=Path(__file__).resolve().parent;MODE=sys.argv[1] if len(sys.argv)>1 else 'shared';P=BASE if MODE=='shared' else BASE/MODE;P.mkdir(exist_ok=True);q=pd.read_parquet(BASE/'panel.parquet');features=[c for c in q if c.startswith('f_')];
if MODE=='interactions':
 for c in list(features):q[c+'_mega']=q[c]*q.mega.astype(float);features.append(c+'_mega')
 q['f_mega']=q.mega.astype(float);features.append('f_mega')
q[features]=q[features].replace([np.inf,-np.inf],np.nan);q['month']=q.day.str[:7];predictions=[]
for month in sorted(q.month.unique()):
 if month<'2019':continue
 start=pd.Timestamp(month+'-01');lo=(start-pd.DateOffset(months=24)).strftime('%Y-%m-%d');train=q[(q.day<str(start.date()))&(q.day>=lo)].dropna(subset=features+['label']);test=q[q.month.eq(month)&q.mega].dropna(subset=features).copy()
 if MODE=='mega':train=train[train.mega]
 if train.day.nunique()<120 or test.empty:continue
 x=train[features].to_numpy();y=train.label.to_numpy();mu=x.mean(0);sd=x.std(0);sd[sd<1e-10]=1;x=np.clip((x-mu)/sd,-5,5);xt=np.clip((test[features].to_numpy()-mu)/sd,-5,5);x=np.c_[np.ones(len(x)),x];xt=np.c_[np.ones(len(xt)),xt];xx=x.T@x;xy=x.T@y
 for penalty in [10.,100.]:
  reg=np.eye(x.shape[1])*penalty;reg[0,0]=0;beta=np.linalg.solve(xx+reg,xy);z=test[['day','symbol','entry','exit','missing_exit','r']].copy();z['prediction']=xt@beta;z['prediction']-=z.groupby('day').prediction.transform('mean');z['penalty']=penalty;predictions.append(z)
 print(month,len(train),len(test),flush=True)
a=pd.concat(predictions);a.to_parquet(P/'predictions.parquet');rows=[];legs=[]
for (penalty,day),g in a.groupby(['penalty','day']):
 if len(g)<20:continue
 for cents in [.01,.03]:
  g=g.copy();g['cost_est']=2*cents/g.entry+.0001
  for cushion in [0,.0002]:
   long=g.loc[g.prediction.idxmax()];short=g.loc[g.prediction.idxmin()];trade=long.prediction>long.cost_est+cushion and -short.prediction>short.cost_est+cushion;gross=fee=notional=0.;missing=False
   if trade:
    for side,t in [(1,long),(-1,short)]:
     shares=np.floor(50000/t.entry)*side;missing=missing or t.missing_exit;value=shares*(t.exit-t.entry) if not t.missing_exit else -abs(shares)*t.entry*.1;cost=abs(shares)*(2*cents+.00005*(t.entry+(t.exit if not t.missing_exit else t.entry)));gross+=value;fee+=cost;notional+=abs(shares)*t.entry;legs.append(dict(day=day,penalty=penalty,cents=cents,cushion=cushion,symbol=t.symbol,side=side,shares=shares,prediction=t.prediction,gross=value,fees=cost,net=value-cost))
   rows.append(dict(day=day,penalty=penalty,cents=cents,cushion=cushion,active=trade,gross=gross,fees=fee,net=gross-fee,notional=notional,missing=missing))
x=pd.DataFrame(rows);x.to_parquet(P/'trades.parquet');pd.DataFrame(legs).to_parquet(P/'legs.parquet');out=[]
for key,g in x.groupby(['penalty','cents','cushion']):
 for period,lo,hi in [('development','2019','2023'),('validation','2023','2025'),('recent','2025','2027'),('all','2019','2027')]:
  t=g[(g.day>=lo)&(g.day<hi)].sort_values('day');z=t.net
  if not len(z):continue
  active=t[t.active];cum=z.cumsum();wins=z[z>0];loss=z[z<0];out.append(dict(zip(['penalty','cents','cushion'],key))|dict(period=period,days=len(z),trades=len(active),net=z.sum(),mean_active=active.net.mean(),mean_daily=z.mean(),win=(active.net>0).mean(),worst=z.min(),dd=(cum-cum.cummax().clip(lower=0)).min(),pf=wins.sum()/-loss.sum() if len(loss) else np.nan,**{'y'+year:v for year,v in z.groupby(t.day.str[:4]).sum().items()}))
pd.DataFrame(out).to_csv(P/'scores.csv',index=False);print('done',len(x),flush=True)
