from rotation import *
rows=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  C,O,F=old.build(source,stress);lp=np.log(C);rank=(lp-lp.shift(7)).rank(axis=1,ascending=False,method='first');top=(rank<=2).astype(float)*2;valid=lp.shift(30).notna()
  for weekday in range(7):
   w=top.where(pd.DataFrame(np.tile((C.index.weekday==weekday)[:,None],(1,4)),index=C.index,columns=C.columns)).ffill().fillna(0);S=w.where(valid,0).shift(1).fillna(0).to_numpy()
   for fs in [0.,1.]:
    for per,st,en in periods(C,source):
     r,path,pnl=old.simulate(C,O,F,S,source,stress,st,en,list(range(4)),fs);r.update(source=source,scenario='combined' if stress else 'base',weekday=weekday,funding_enabled=bool(fs),period=per);rows.append(r)
 pd.DataFrame(rows).to_csv(P/'weekday_results.csv',index=False);print(source,flush=True)
rows=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  C,O,F=old.build(source,stress)
  for j,a in enumerate(old.ASSETS):
   unit=float(old.prior.r.old.M['KX'+a+'PERP']['contract_size']);factor=1e-6/unit;C.iloc[:,j]*=factor;O.iloc[:,2*j:2*j+2]*=factor;F.iloc[:,j]*=factor
  for name,S in extended(C).items():
   for fs in [0.,1.]:
    for per,st,en in periods(C,source):
     r,_,_=old.simulate(C,O,F,S,source,stress,st,en,list(range(4)),fs);r.update(source=source,design=name,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per);rows.append(r)
pd.DataFrame(rows).to_csv(P/'fractional_results.csv',index=False)
# Prefix checks: all signals must be identical before changed future prices.
C,O,F=old.build('proxy',False);cut=500;altered=C.copy();altered.iloc[cut:]*=np.exp(np.linspace(0,3,len(C)-cut))[:,None]
for name,s in extended(C).items():assert np.allclose(s[:cut+1],extended(altered)[name][:cut+1]),name
print('PASS: all extended signals invariant to future changes')
