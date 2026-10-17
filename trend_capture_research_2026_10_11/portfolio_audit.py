from portfolio import *
rows=[];nulls=[];stats=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  C,O,F=build(source,stress);lp=np.log(C);sig=np.sign(lp-lp.shift(30)).clip(lower=0);S=sig.shift(1).fillna(0).to_numpy()
  # Same perasset warm-up as momentum comparator, always long thereafter.
  B=(np.arange(len(C))[:,None]>=np.array([np.flatnonzero(C.iloc[:,j].notna())[0]+31 for j in range(4)])[None,:]).astype(float)
  end=len(C) if source=='proxy' else C.index.searchsorted(pd.Timestamp('2026-10-10',tz='UTC'))
  periods=[('all',0,end)]+([(str(y),C.index.searchsorted(pd.Timestamp(f'{y}-01-01',tz='UTC')),C.index.searchsorted(pd.Timestamp(f'{y+1}-01-01',tz='UTC'))) for y in range(2022,2026)] if source=='proxy' else [('early',0,C.index.searchsorted(pd.Timestamp('2026-09-01',tz='UTC'))),('later',C.index.searchsorted(pd.Timestamp('2026-09-01',tz='UTC')),end)])
  for label,st,en in periods:
   for name,signal in [('mom30',S),('always_long',B)]:
    r,path,pnl=simulate(C,O,F,signal,source,stress,st,en,list(range(4)));r.update(source=source,scenario='combined' if stress else 'base',period=label,design=name);rows.append(r)
    if label=='all':
     eq=np.r_[100.,path];ret=np.diff(eq)/eq[:-1];peak=np.maximum.accumulate(eq);pdd=max((peak-eq)/peak);monthly=pd.Series(eq[1:],index=C.index[:end]).resample('ME').last();monthly=np.diff(np.r_[100.,monthly])
     stats.append(dict(source=source,scenario=r['scenario'],design=name,max_dd_pct=pdd*100,positive_months=int((monthly>0).sum()),months=len(monthly),best_day=max(np.diff(eq)),worst_day=min(np.diff(eq)),pnl_without_best5_days=r['net']-np.sort(np.diff(eq))[-5:].sum(),asset_pnl=pnl.tolist()))
  for excluded in range(4):
   r,_,_=simulate(C,O,F,S,source,stress,0,end,[j for j in range(4) if j!=excluded]);r.update(source=source,scenario='combined' if stress else 'base',period='all',design='omit_'+ASSETS[excluded]);rows.append(r)
  # Descriptive random-timing control, not a valid postselection significance test.
  if stress:
   rng=np.random.default_rng(20261011)
   for n in range(200):
    shift=int(rng.integers(31,max(32,end-31)));R=np.roll(S,shift,axis=0);R[B==0]=0
    r,_,_=simulate(C,O,F,R,source,stress,0,end,list(range(4)));nulls.append(dict(source=source,iteration=n,shift=shift,net=r['net'],orders=r['orders'],dd=r['dd']))
pd.DataFrame(rows).to_csv(Q/'controls.csv',index=False);pd.DataFrame(nulls).to_csv(Q/'timing_nulls.csv',index=False);(Q/'diagnostics.json').write_text(json.dumps(stats,indent=2))
print(pd.DataFrame(rows).to_string(index=False))
