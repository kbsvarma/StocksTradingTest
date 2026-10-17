from portfolio import *
rows=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  C,O,F=build(source,stress);lp=np.log(C);slow=(lp>lp.shift(30));fast=(lp>lp.shift(7));valid=lp.shift(30).notna();base=slow.astype(float)
  scout=base.where(slow,.25*fast).where(valid,0).astype(float)
  confirmed=np.zeros((len(C),4));active=np.zeros(4)
  for i in range(len(C)):
   for j in range(4):
    if not slow.iloc[i,j]:active[j]=0
    elif fast.iloc[i,j]:active[j]=1
   confirmed[i]=active
  B=(np.arange(len(C))[:,None]>=np.array([np.flatnonzero(C.iloc[:,j].notna())[0]+31 for j in range(4)])[None,:]).astype(float)
  strategies=[('mom30',base.shift(1).fillna(0).to_numpy(),1.),('scout25',scout.shift(1).fillna(0).to_numpy(),1.),('confirmed_entry',pd.DataFrame(confirmed).shift(1).fillna(0).to_numpy(),1.),('mom30_no_funding',base.shift(1).fillna(0).to_numpy(),0.),('always_long_no_funding',B,0.)]
  end=len(C) if source=='proxy' else C.index.searchsorted(pd.Timestamp('2026-10-10',tz='UTC'));periods=[('all',0,end)]+([(str(y),C.index.searchsorted(pd.Timestamp(f'{y}-01-01',tz='UTC')),C.index.searchsorted(pd.Timestamp(f'{y+1}-01-01',tz='UTC'))) for y in range(2022,2026)] if source=='proxy' else [('early',0,C.index.searchsorted(pd.Timestamp('2026-09-01',tz='UTC'))),('later',C.index.searchsorted(pd.Timestamp('2026-09-01',tz='UTC')),end)])
  for name,S,scale in strategies:
   for label,st,en in periods:
    r,path,pnl=simulate(C,O,F,S,source,stress,st,en,list(range(4)),scale);r.update(source=source,scenario='combined' if stress else 'base',period=label,design=name);rows.append(r)
pd.DataFrame(rows).to_csv(Q/'scouts.csv',index=False);print(pd.DataFrame(rows).pivot_table(index=['source','scenario','design'],columns='period',values='net').round(2).to_string())
