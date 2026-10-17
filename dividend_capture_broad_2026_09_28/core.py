from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
U=json.loads((P/'universe.json').read_text());CAT={s:c for c,ss in U.items() for s in ss};SYMS=list(CAT)
DATES=pd.read_csv(P/'data/SPY.csv',index_col=0,parse_dates=True).index
N=len(DATES);END=N-1;DAY=DATES.to_numpy().astype('datetime64[D]').astype(int)
ISS=pd.read_csv(P/'issuer_distributions.csv');OV={s:g for s,g in ISS.groupby('ticker')}
DATA={};audit=[];actions=[]
for s in SYMS:
 d=pd.read_csv(P/'data'/f'{s}.csv',index_col=0,parse_dates=True)
 assert d.index.isin(DATES).all() and not d.index.duplicated().any()
 cash=d.Dividends.copy()+d.get('Capital Gains',0)
 pays={};decs={}
 if s in OV:
  for r in OV[s].itertuples():
   ex=pd.Timestamp(r.ex_date);k=DATES.get_loc(ex)
   assert ex in d.index
   audit.append(dict(ticker=s,ex_date=r.ex_date,provider=float(cash.loc[ex]),issuer=r.amount,difference=float(cash.loc[ex])-r.amount))
   cash.loc[ex]=r.amount
   pays[k]=int(DATES.searchsorted(pd.Timestamp(r.payment)));decs[k]=int(DATES.searchsorted(pd.Timestamp(r.declaration)))
 start=DATES.get_loc(d.index[0]);a=d.reindex(DATES);c=a.Close.to_numpy();v=cash.reindex(DATES).fillna(0).to_numpy()
 # Point-in-time cash-distribution yield proxies; exclude current-day dividend.
 z=pd.Series(v,index=DATES);ttm=z.rolling('365D',closed='left').sum().to_numpy()/c
 past126=z.shift(1).rolling(126,min_periods=1).sum().fillna(0).to_numpy()
 listed=np.maximum(1,np.minimum(126,np.arange(N)-start));priority=past126*252/listed/c
 adv=(a.Close*a.Volume).shift(1).rolling(20,min_periods=5).mean().to_numpy()
 split=a['Stock Splits'].fillna(0).to_numpy();flag=np.flatnonzero((split!=0)&(np.abs(np.log2(np.where(split>0,split,1))-np.round(np.log2(np.where(split>0,split,1)) ))>.015))
 # All split records saved; synthetic/spin-off warnings manually discussed rather than auto-removed.
 for k in np.flatnonzero(split):actions.append(dict(ticker=s,date=str(DATES[k].date()),ratio=split[k]))
 lag=30 if CAT[s] in ['dividend_stock','reit','bdc'] else 5
 due=np.minimum(N+100,np.searchsorted(DAY,DAY+lag))
 for k,pay in pays.items():due[k]=pay
 total=(c+v)/np.roll(c,1)-1;total[:start+1]=0
 assert np.isfinite(c[start:]).all() and (c[start:]>0).all()
 DATA[s]=dict(close=c,div=v,ttm=ttm,priority=priority,adv=adv,due=due,decl=decs,start=start,lag=lag,total=total,raw=d)
if __name__=='__main__':
 pd.DataFrame(audit).to_csv(P/'issuer_audit.csv',index=False);pd.DataFrame(actions).to_csv(P/'corporate_actions.csv',index=False)
 print('Loaded',len(DATA),'securities. Exact issuer overrides',len(audit),'price sessions',N)
