from rotation import *
rows=[]
for source in ['proxy','venue','transfer']:
 for stress in [False,True]:
  C,O,F=transfer_build(stress) if source=='transfer' else old.build(source,stress);ss=extended(C);wk=ss['weekly_top2'];lt=ss['LT30'];src='proxy' if source=='transfer' else source
  for name,S in [('weekly_LT30',wk*lt),('blend_half_LT30',.5*wk+.5*lt)]:
   for fs in [0.,1.]:
    for per,st,en in periods(C,src):
     r,path,pnl=old.simulate(C,O,F,S,src,stress,st,en,list(range(4)),fs);r.update(source=source,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,design=name,asset_pnl=pnl.tolist());rows.append(r)
     np.savez_compressed(P/'ledgers'/f'hybrid_{source}_{name}_{fs}_{per}_{stress}.npz',equity=path,asset_pnl=pnl)
 print(source,flush=True)
pd.DataFrame(rows).to_csv(P/'hybrid_results.csv',index=False)
