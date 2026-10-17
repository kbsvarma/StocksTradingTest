from rotation import *
rows=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  C,O,F=old.build(source,stress)
  for excluded in range(4):
   cc=C.copy();cc.iloc[:,excluded]=np.nan;chosen=[j for j in range(4) if j!=excluded]
   S=extended(cc)['weekly_top2']*.75
   for fs in [0.,1.]:
    for per,st,en in periods(C,source):
     r,_,pnl=old.simulate(C,O,F,S,source,stress,st,en,chosen,fs);r.update(source=source,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,omit=old.ASSETS[excluded]);rows.append(r)
pd.DataFrame(rows).to_csv(P/'omissions.csv',index=False)
rows=[]
for source in ['proxy','venue']:
 for stress in [False,True]:
  for fractional in [False,True]:
   C,O,F=old.build(source,stress)
   if fractional:
    for j,a in enumerate(old.ASSETS):
     factor=1e-6/float(old.prior.r.old.M['KX'+a+'PERP']['contract_size']);C.iloc[:,j]*=factor;O.iloc[:,2*j:2*j+2]*=factor;F.iloc[:,j]*=factor
   S=rules(C)['always_long']
   for fs in [0.,1.]:
    for per,st,en in periods(C,source):
     r,_,_=old.simulate(C,O,F,S,source,stress,st,en,list(range(4)),fs,True);r.update(source=source,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,fractional=fractional);rows.append(r)
pd.DataFrame(rows).to_csv(P/'fixed_hold.csv',index=False)
print('Omissions and fixed-quantity benchmarks complete')
